"""Integration coverage for peer message admission policy."""

import time
from pathlib import Path

import requests
import yaml

from framework.basic_fiber import FiberTest

CKB = 100_000_000
MESSAGE_INTERVAL_MS = 10_000
OPEN_CHANNEL_AMOUNT = 200 * CKB
POLICY_OVERFLOW = "Disconnecting Fiber peer after ingress admission overflow"
POLICY_BAN = "Disconnecting peer after repeated Fiber message rate-limit violations"
ACTIVE_POLICY_BAN = "Disconnecting peer while its Fiber message ban is active"
POLICY_OVERFLOW_FAILED = (
    "Failed to disconnect Fiber peer after ingress admission overflow"
)
POLICY_BAN_FAILED = "Failed to disconnect rate-limited Fiber peer"
ACTIVE_POLICY_BAN_FAILED = "Failed to disconnect peer with active Fiber message ban"
POLICY_LOG_MARKERS = (POLICY_OVERFLOW, POLICY_BAN, ACTIVE_POLICY_BAN)


def _rpc_once(fiber, method, params, timeout=5):
    response = requests.post(
        fiber.get_client().url,
        json={"id": 42, "jsonrpc": "2.0", "method": method, "params": params},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if "error" in payload:
        raise RuntimeError(payload["error"].get("message", "unknown RPC error"))
    return payload.get("result")


def _peer_address(fiber):
    return (
        fiber.get_client().node_info()["addresses"][0].replace("0.0.0.0", "127.0.0.1")
    )


def _connect_once(sender, receiver):
    return _rpc_once(
        sender,
        "connect_peer",
        [{"address": _peer_address(receiver), "save": False}],
    )


def _log_path(fiber):
    return Path(fiber.tmp_path) / "node.log"


def _log_offset(fiber):
    path = _log_path(fiber)
    return path.stat().st_size if path.exists() else 0


def _read_log_since(fiber, offset):
    path = _log_path(fiber)
    if not path.exists():
        return ""
    with path.open("rb") as log_file:
        log_file.seek(offset)
        return log_file.read().decode("utf-8", errors="replace")


def _wait_for_log(fiber, offset, marker, timeout=10):
    deadline = time.monotonic() + timeout
    log_text = ""
    while time.monotonic() < deadline:
        log_text = _read_log_since(fiber, offset)
        if marker in log_text:
            return time.monotonic()
        time.sleep(0.1)
    raise AssertionError(
        f"expected {marker!r} in new receiver log; tail={log_text[-2000:]!r}"
    )


def _peer_is_listed(fiber, pubkey):
    peers = fiber.get_client().list_peers().get("peers") or []
    return any(peer.get("pubkey") == pubkey for peer in peers)


def _wait_for_bilateral_peer_state(
    first,
    second,
    expected_connected,
    timeout=10,
    consecutive_samples=3,
    sample_interval=0.3,
):
    first_pubkey = first.get_pubkey()
    second_pubkey = second.get_pubkey()
    deadline = time.monotonic() + timeout
    matching_samples = 0
    last_state = None

    while time.monotonic() < deadline:
        first_has_second = _peer_is_listed(first, second_pubkey)
        second_has_first = _peer_is_listed(second, first_pubkey)
        last_state = (first_has_second, second_has_first)
        matches = (
            first_has_second and second_has_first
            if expected_connected
            else not first_has_second and not second_has_first
        )
        matching_samples = matching_samples + 1 if matches else 0
        if matching_samples >= consecutive_samples:
            return
        time.sleep(sample_interval)

    expected = "connected" if expected_connected else "disconnected"
    raise AssertionError(
        f"peers did not remain bilaterally {expected}; last_state={last_state}"
    )


def _wait_for_graph_edge(observer, first_pubkey, second_pubkey, timeout=120):
    expected_nodes = {first_pubkey, second_pubkey}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        channels = observer.get_client().graph_channels({}).get("channels") or []
        for channel in channels:
            if {channel.get("node1"), channel.get("node2")} == expected_nodes:
                return channel
        time.sleep(1)
    raise AssertionError(
        f"public channel {sorted(expected_nodes)} did not reach observer graph"
    )


def _wait_for_new_message_window(interval_ms=MESSAGE_INTERVAL_MS):
    deadline = time.monotonic() + interval_ms / 1000 + 2
    while time.monotonic() < deadline:
        now_ms = time.time_ns() // 1_000_000
        phase_ms = now_ms % interval_ms
        if phase_ms <= 750:
            return now_ms // interval_ms
        time.sleep(min(0.25, (interval_ms - phase_ms) / 1000 + 0.02))
    raise AssertionError("failed to enter a fresh peer-message refill window")


def _open_channel_in_current_window(sender, receiver):
    window = _wait_for_new_message_window()
    _connect_once(sender, receiver)
    deadline_ms = (window + 1) * MESSAGE_INTERVAL_MS - 3_000
    last_error = None

    while time.time_ns() // 1_000_000 < deadline_ms:
        try:
            result = _rpc_once(
                sender,
                "open_channel",
                [
                    {
                        "pubkey": receiver.get_pubkey(),
                        "funding_amount": hex(OPEN_CHANNEL_AMOUNT),
                        "public": False,
                    }
                ],
            )
            temporary_channel_id = result.get("temporary_channel_id")
            assert temporary_channel_id, "open_channel did not return a temporary id"
            current_window = (time.time_ns() // 1_000_000) // MESSAGE_INTERVAL_MS
            assert current_window == window, (
                f"test stimulus crossed message refill window: start={window}, "
                f"current={current_window}"
            )
            return temporary_channel_id
        except RuntimeError as error:
            if not any(
                expected in str(error)
                for expected in (
                    "is not connected",
                    "waiting for peer to send Init message",
                )
            ):
                raise
            last_error = error
            time.sleep(0.1)

    raise AssertionError(
        f"open_channel was not sent with enough time left in window {window}: {last_error}"
    )


def _wait_for_no_pending_channel(sender, receiver, timeout=10):
    deadline = time.monotonic() + timeout
    active_pending_channels = []
    while time.monotonic() < deadline:
        pending_channels = sender.get_client().list_channels(
            {"pubkey": receiver.get_pubkey(), "only_pending": True}
        )["channels"]
        active_pending_channels = [
            channel
            for channel in pending_channels
            if channel["state"]["state_name"] != "Closed"
        ]
        if not active_pending_channels:
            return
        time.sleep(0.2)
    raise AssertionError(
        "pending channel did not reach a terminal state after abandon: "
        f"{active_pending_channels}"
    )


def _suppress_reconnect(sender, receiver, temporary_channel_id=None):
    if temporary_channel_id is not None:
        try:
            sender.get_client().abandon_channel({"channel_id": temporary_channel_id})
        except Exception as error:
            pending_channels = sender.get_client().list_channels(
                {"pubkey": receiver.get_pubkey(), "only_pending": True}
            )["channels"]
            active_pending_channels = [
                channel
                for channel in pending_channels
                if channel["state"]["state_name"] != "Closed"
            ]
            if active_pending_channels:
                raise AssertionError(
                    f"failed to abandon pending channel: {active_pending_channels}"
                ) from error
        _wait_for_no_pending_channel(sender, receiver)

    if _peer_is_listed(sender, receiver.get_pubkey()) or _peer_is_listed(
        receiver, sender.get_pubkey()
    ):
        try:
            sender.get_client().disconnect_peer({"pubkey": receiver.get_pubkey()})
        except Exception as error:
            if _peer_is_listed(sender, receiver.get_pubkey()) or _peer_is_listed(
                receiver, sender.get_pubkey()
            ):
                raise AssertionError("failed to suppress peer reconnect") from error


def _assert_no_ready_channel(first, second):
    for fiber, peer_pubkey in (
        (first, second.get_pubkey()),
        (second, first.get_pubkey()),
    ):
        channels = fiber.get_client().list_channels({"pubkey": peer_pubkey})["channels"]
        assert all(
            channel["state"]["state_name"] != "ChannelReady" for channel in channels
        )


def _assert_no_policy_event(fiber, offset):
    log_text = _read_log_since(fiber, offset)
    found = [marker for marker in POLICY_LOG_MARKERS if marker in log_text]
    assert not found, f"unexpected peer-message policy events: {found}"


def _assert_policy_disconnected(first, second, receiver_log_offset, failure_marker):
    _wait_for_bilateral_peer_state(
        first,
        second,
        expected_connected=False,
        timeout=5,
        consecutive_samples=1,
    )
    log_text = _read_log_since(second, receiver_log_offset)
    assert (
        failure_marker not in log_text
    ), f"policy logged a disconnect failure: {failure_marker}"


class TestPeerMessagePolicy(FiberTest):
    __test__ = True

    def _start_policy_receiver(self, policy, disable_auto_accept=False):
        receiver_config = self.fiber1.fiber_config.copy()
        receiver_config.pop("fiber_listening_addr", None)
        receiver_config.pop("rpc_listening_addr", None)
        receiver_config["fiber_peer_message_policy"] = policy
        if disable_auto_accept:
            receiver_config["fiber_open_channel_auto_accept_min_ckb_funding_amount"] = (
                10**18
            )

        receiver = self.start_new_fiber(
            self.generate_account(1_000), config=receiver_config
        )
        rendered_config = yaml.safe_load(Path(receiver.fiber_config_path).read_text())
        assert rendered_config["fiber"]["peer_message_policy"] == policy
        return receiver

    def _send_direct_payment(self, sender, receiver):
        payment = sender.get_client().send_payment(
            {
                "target_pubkey": receiver.get_pubkey(),
                "amount": hex(CKB),
                "keysend": True,
                "allow_self_payment": True,
                "max_fee_rate": hex(1_000_000_000_000_000),
            }
        )
        self.wait_payment_state(
            sender, payment["payment_hash"], "Success", timeout=120, interval=1
        )
        result = sender.get_client().get_payment(
            {"payment_hash": payment["payment_hash"]}
        )
        assert result["status"] == "Success"

    # TEST-MAP: PMP-01
    def test_default_policy_keeps_channel_and_payment_compatible(self):
        assert (
            "peer_message_policy:"
            not in Path(self.fiber1.fiber_config_path).read_text()
        )
        assert (
            "peer_message_policy:"
            not in Path(self.fiber2.fiber_config_path).read_text()
        )

        self.fiber1.get_client().disconnect_peer({"pubkey": self.fiber2.get_pubkey()})
        _wait_for_bilateral_peer_state(
            self.fiber1, self.fiber2, expected_connected=False
        )
        log_offset = _log_offset(self.fiber2)
        _connect_once(self.fiber1, self.fiber2)
        _wait_for_bilateral_peer_state(
            self.fiber1, self.fiber2, expected_connected=True
        )

        self.open_channel(self.fiber1, self.fiber2, 200 * CKB, 0)
        _wait_for_graph_edge(
            self.fiber1, self.fiber1.get_pubkey(), self.fiber2.get_pubkey()
        )
        self._send_direct_payment(self.fiber1, self.fiber2)

        _wait_for_bilateral_peer_state(
            self.fiber1, self.fiber2, expected_connected=True
        )
        assert self.fiber1.get_client().node_info()["pubkey"]
        assert self.fiber2.get_client().node_info()["pubkey"]
        _assert_no_policy_event(self.fiber2, log_offset)

    # TEST-MAP: PMP-02
    def test_partial_nonzero_policy_allows_normal_business(self):
        # Keep the burst below the default 400 while leaving several times the
        # observed headroom for one channel setup and direct payment.
        receiver = self._start_policy_receiver(
            {
                "peer_message_interval_ms": 10,
                "peer_message_burst": 100,
                "peer_message_rate_bytes_per_sec": 262_144,
                "peer_message_burst_bytes": 1_048_576,
            }
        )
        log_offset = _log_offset(receiver)

        self.open_channel(self.fiber1, receiver, 200 * CKB, 0)
        _wait_for_graph_edge(
            self.fiber1, self.fiber1.get_pubkey(), receiver.get_pubkey()
        )
        _wait_for_graph_edge(
            self.fiber2, self.fiber1.get_pubkey(), receiver.get_pubkey()
        )
        self._send_direct_payment(self.fiber1, receiver)

        _wait_for_bilateral_peer_state(self.fiber1, receiver, expected_connected=True)
        _assert_no_policy_event(receiver, log_offset)

    # TEST-MAP: PMP-03
    def test_message_burst_overflow_disconnects_only_the_peer(self):
        receiver = self._start_policy_receiver(
            {
                "peer_message_interval_ms": MESSAGE_INTERVAL_MS,
                "peer_message_burst": 1,
                "violation_ban_threshold": 1_000,
            },
            disable_auto_accept=True,
        )
        log_offset = _log_offset(receiver)

        temporary_channel_id = _open_channel_in_current_window(self.fiber1, receiver)
        _wait_for_log(receiver, log_offset, POLICY_OVERFLOW)
        _assert_policy_disconnected(
            self.fiber1, receiver, log_offset, POLICY_OVERFLOW_FAILED
        )
        _suppress_reconnect(self.fiber1, receiver, temporary_channel_id)

        _wait_for_bilateral_peer_state(self.fiber1, receiver, expected_connected=False)
        _assert_no_ready_channel(self.fiber1, receiver)
        assert receiver.get_client().node_info()["pubkey"] == receiver.get_pubkey()
        assert POLICY_BAN not in _read_log_since(receiver, log_offset)

    # TEST-MAP: PMP-04
    def test_byte_burst_overflow_disconnects_only_the_peer(self):
        receiver = self._start_policy_receiver(
            {
                "peer_message_rate_bytes_per_sec": 1_024,
                "peer_message_burst_bytes": 1,
                "peer_message_interval_ms": 1,
                "peer_message_burst": 1_000,
                "violation_ban_threshold": 1_000,
            }
        )
        log_offset = _log_offset(receiver)

        _connect_once(self.fiber1, receiver)
        _wait_for_log(receiver, log_offset, POLICY_OVERFLOW)
        _assert_policy_disconnected(
            self.fiber1, receiver, log_offset, POLICY_OVERFLOW_FAILED
        )
        _suppress_reconnect(self.fiber1, receiver)

        _wait_for_bilateral_peer_state(self.fiber1, receiver, expected_connected=False)
        _assert_no_ready_channel(self.fiber1, receiver)
        assert receiver.get_client().node_info()["pubkey"] == receiver.get_pubkey()
        assert POLICY_BAN not in _read_log_since(receiver, log_offset)

    # TEST-MAP: PMP-05
    def test_rate_limit_ban_rejects_then_allows_reconnect_after_expiry(self):
        receiver = self._start_policy_receiver(
            {
                "peer_message_interval_ms": MESSAGE_INTERVAL_MS,
                "peer_message_burst": 1,
                "violation_ban_threshold": 1,
                "ban_duration_ms": 15_000,
            },
            disable_auto_accept=True,
        )
        initial_log_offset = _log_offset(receiver)

        temporary_channel_id = _open_channel_in_current_window(self.fiber1, receiver)
        ban_seen_at = _wait_for_log(receiver, initial_log_offset, POLICY_BAN)
        _assert_policy_disconnected(
            self.fiber1, receiver, initial_log_offset, POLICY_BAN_FAILED
        )
        _suppress_reconnect(self.fiber1, receiver, temporary_channel_id)
        _wait_for_bilateral_peer_state(self.fiber1, receiver, expected_connected=False)

        active_ban_log_offset = _log_offset(receiver)
        _connect_once(self.fiber1, receiver)
        _wait_for_log(receiver, active_ban_log_offset, ACTIVE_POLICY_BAN, timeout=5)
        _assert_policy_disconnected(
            self.fiber1, receiver, active_ban_log_offset, ACTIVE_POLICY_BAN_FAILED
        )
        _suppress_reconnect(self.fiber1, receiver)
        _wait_for_bilateral_peer_state(self.fiber1, receiver, expected_connected=False)
        assert receiver.get_client().node_info()["pubkey"] == receiver.get_pubkey()

        recovery_at = ban_seen_at + 18
        if time.monotonic() < recovery_at:
            time.sleep(recovery_at - time.monotonic())
        recovery_log_offset = _log_offset(receiver)
        _connect_once(self.fiber1, receiver)
        _wait_for_bilateral_peer_state(
            self.fiber1,
            receiver,
            expected_connected=True,
            timeout=15,
            consecutive_samples=5,
            sample_interval=0.5,
        )
        _assert_no_ready_channel(self.fiber1, receiver)
        assert receiver.get_client().node_info()["pubkey"] == receiver.get_pubkey()
        _assert_no_policy_event(receiver, recovery_log_offset)
