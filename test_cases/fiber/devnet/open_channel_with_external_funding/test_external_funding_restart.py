"""Regression suite for fiber PR #1252.

PR #1252 — *Persist external funding state across restarts*.

Background
----------
`open_channel_with_external_funding` returns an unsigned funding transaction
that the user signs with an external wallet (e.g. JoyID). On the WASM build
this signing step requires a JoyID redirect that can take an arbitrary amount
of time and may be interleaved with a process restart. Before this PR, the
ephemeral state needed to (a) accept the user's signed tx in
`submit_signed_funding_tx` and (b) drive the rest of the funding handshake
lived in memory only — so any restart between `open_channel_with_external_funding`
and `submit_signed_funding_tx`, or between submit and `ChannelReady`, would
abort the channel.

The PR adds an `ExternalFundingPersistState` field on `ChannelActorData` and
calls `hydrate_external_funding_runtime()` on reload / reestablish so the
ephemeral state can be reconstructed. It also introduces
`move_channel_actor_state(old_id, state)` to handle the channel-id transition
on the acceptor side mid-flow, plus a migration that appends `0u8` (None) to
existing entries.

Scenarios covered here (mirroring the upstream Rust e2e tests):

  1. Restart the **initiator** *before* `submit_signed_funding_tx`. After
     restart the initiator can still submit and the channel reaches
     `ChannelReady`.
  2. Restart the **acceptor** *before* `submit_signed_funding_tx`. The
     initiator submits, both peers reach `ChannelReady`.
  3. Restart the **initiator** *after* `submit_signed_funding_tx` but before
     `ChannelReady`. The handshake resumes to `ChannelReady`.
  4. Restart the **acceptor** *after* `submit_signed_funding_tx` but before
     `ChannelReady`. The handshake resumes to `ChannelReady`.
"""

import time

from test_cases.fiber.devnet.open_channel_with_external_funding.external_funding_base import (
    ExternalFundingBase,
)

# ~500 CKB — comfortably above the default
# `open_channel_auto_accept_min_ckb_funding_amount` (100 CKB) so the acceptor
# auto-accepts.
EXTERNAL_FUNDING_AMOUNT = 500 * 100000000


def _submit_signed(fiber, channel_id, signed_tx):
    return fiber.get_client().call(
        "submit_signed_funding_tx",
        [{"channel_id": channel_id, "signed_funding_tx": signed_tx}],
    )


def _list_channels_with_peer(fiber, peer_pubkey, include_closed=False):
    return fiber.get_client().list_channels(
        {"pubkey": peer_pubkey, "include_closed": include_closed}
    )["channels"]


def _list_pending_channels_with_peer(fiber, peer_pubkey):
    return fiber.get_client().list_channels(
        {"pubkey": peer_pubkey, "only_pending": True}
    )["channels"]


def _is_awaiting_external_funding(channel):
    state = channel["state"]
    name = state["state_name"]
    flags = state.get("state_flags", "")
    return name == "AwaitingExternalFunding" or (
        name == "NegotiatingFunding" and "AWAITING_EXTERNAL_FUNDING" in (flags or "")
    )


def _is_post_submit_commitment_replay_window(channel):
    state = channel["state"]
    name = state["state_name"]
    flags = state.get("state_flags", "")
    return (
        name == "SigningCommitment" and "OUR_COMMITMENT_SIGNED_SENT" in (flags or "")
    ) or name in ("AwaitingTxSignatures", "AwaitingChannelReady")


class TestExternalFundingRestart(ExternalFundingBase):
    """Each test method opens a fresh externally-funded channel and exercises
    a different restart point."""

    __test__ = True

    def _restart(self, fiber):
        """Stop and re-start a fiber node, then wait for its RPC to come back."""
        fiber.stop()
        fiber.start(fnn_log_level=self.fnn_log_level)

    def _mine_some(self, n=4):
        for _ in range(n):
            self.Miner.miner_with_version(self.node, "0x0")

    def _wait_state(self, fiber, peer_pubkey, expected, timeout=180):
        """Wait until `fiber` has at least one channel with `peer_pubkey`
        whose `state.state_name == expected`. Returns the channel dict.
        Mines a CKB block on each iteration so funding-tx confirmations can
        make progress."""
        deadline = time.time() + timeout
        last_seen = None
        while time.time() < deadline:
            channels = _list_channels_with_peer(
                fiber, peer_pubkey, include_closed=(expected == "Closed")
            )
            for ch in channels:
                last_seen = ch["state"]["state_name"]
                if last_seen == expected:
                    return ch
            try:
                self.Miner.miner_with_version(self.node, "0x0")
            except Exception:
                pass
            time.sleep(1)
        assert False, (
            f"channel with {peer_pubkey} did not reach {expected!r} in "
            f"{timeout}s (last seen state: {last_seen!r})"
        )

    def _get_channel_from_pending(self, fiber, peer_pubkey, channel_id):
        channels = _list_pending_channels_with_peer(fiber, peer_pubkey)
        for channel in channels:
            if channel["channel_id"] == channel_id:
                return channel
        return None

    def _assert_awaiting_external_funding(self, fiber, peer_pubkey, channel_id, label):
        channel = self._get_channel_from_pending(fiber, peer_pubkey, channel_id)
        assert channel is not None, (
            f"{label}: channel {channel_id} should appear in "
            "list_channels(only_pending=true)"
        )
        assert _is_awaiting_external_funding(channel), (
            f"{label}: expected awaiting external funding, got "
            f"state_name={channel['state']['state_name']!r}, "
            f"state_flags={channel['state'].get('state_flags', '')!r}"
        )
        return channel

    def _wait_pending_closed(self, fiber, peer_pubkey, channel_id, timeout=90):
        deadline = time.time() + timeout
        last_state = None
        while time.time() < deadline:
            channel = self._get_channel_from_pending(fiber, peer_pubkey, channel_id)
            if channel is not None:
                last_state = channel["state"]
                if channel["state"]["state_name"] == "Closed":
                    return channel
            try:
                self.Miner.miner_with_version(self.node, "0x0")
            except Exception:
                pass
            time.sleep(1)
        assert False, (
            f"channel {channel_id} did not appear as Closed in "
            f"list_channels(only_pending=true) within {timeout}s "
            f"(last state: {last_state!r})"
        )

    def _wait_acceptor_commitment_replay_window(self, channel_id, timeout=20):
        deadline = time.time() + timeout
        last_state = None
        while time.time() < deadline:
            channels = _list_channels_with_peer(self.fiber2, self.fiber1.get_pubkey())
            for channel in channels:
                if channel["channel_id"] != channel_id:
                    continue
                last_state = channel["state"]
                if _is_post_submit_commitment_replay_window(channel):
                    return channel
            time.sleep(0.1)
        assert False, (
            "acceptor did not reach the external-funding commitment replay "
            f"window for channel {channel_id} within {timeout}s "
            f"(last state: {last_state!r})"
        )

    def _setup_external_open(self):
        """Returns (channel_id, signed_tx).

        fiber1 = initiator. The funding inputs use a freshly-generated
        external account's lock script (its private key is signed locally
        via ckb-cli, not by any dev-only RPC), avoiding the acceptor's
        "peer uses inputs with our lock script" rejection.
        fiber2 = acceptor.
        """
        # Need some confirmed cells under the external account's lock.
        self._mine_some()

        context = self._open_external_funding_channel(
            funding_amount=EXTERNAL_FUNDING_AMOUNT,
            public=True,
        )
        signed_tx = self._sign_external_funding_tx(
            context["unsigned_funding_tx"], context["external_private_key"]
        )
        return context["channel_id"], signed_tx

    # ------------------------------------------------------------------
    # 1. Restart initiator BEFORE submit_signed_funding_tx
    # ------------------------------------------------------------------
    def test_initiator_restart_before_submit_then_channel_ready(self):
        channel_id, signed_tx = self._setup_external_open()

        self._restart(self.fiber1)
        self.fiber1.connect_peer(self.fiber2)

        result = _submit_signed(self.fiber1, channel_id, signed_tx)
        assert result["funding_tx_hash"], "submit returned empty funding_tx_hash"

        ch1 = self._wait_state(self.fiber1, self.fiber2.get_pubkey(), "ChannelReady")
        ch2 = self._wait_state(self.fiber2, self.fiber1.get_pubkey(), "ChannelReady")
        assert ch1["state"]["state_name"] == "ChannelReady"
        assert ch2["state"]["state_name"] == "ChannelReady"

    # ------------------------------------------------------------------
    # 2. Restart acceptor BEFORE submit_signed_funding_tx
    # ------------------------------------------------------------------
    def test_acceptor_restart_before_submit_then_channel_ready(self):
        channel_id, signed_tx = self._setup_external_open()

        self._restart(self.fiber2)
        self.fiber1.connect_peer(self.fiber2)

        result = _submit_signed(self.fiber1, channel_id, signed_tx)
        assert result["funding_tx_hash"], "submit returned empty funding_tx_hash"

        ch1 = self._wait_state(self.fiber1, self.fiber2.get_pubkey(), "ChannelReady")
        ch2 = self._wait_state(self.fiber2, self.fiber1.get_pubkey(), "ChannelReady")
        assert ch1["state"]["state_name"] == "ChannelReady"
        assert ch2["state"]["state_name"] == "ChannelReady"

    # ------------------------------------------------------------------
    # 3. Restart initiator AFTER submit, before ChannelReady
    # ------------------------------------------------------------------
    def test_initiator_restart_after_submit_resumes_to_channel_ready(self):
        channel_id, signed_tx = self._setup_external_open()

        result = _submit_signed(self.fiber1, channel_id, signed_tx)
        assert result["funding_tx_hash"]

        # Restart immediately — handshake should be in flight or just kicking off.
        self._restart(self.fiber1)
        self.fiber1.connect_peer(self.fiber2)

        ch1 = self._wait_state(
            self.fiber1, self.fiber2.get_pubkey(), "ChannelReady", timeout=180
        )
        ch2 = self._wait_state(
            self.fiber2, self.fiber1.get_pubkey(), "ChannelReady", timeout=180
        )
        assert ch1["state"]["state_name"] == "ChannelReady"
        assert ch2["state"]["state_name"] == "ChannelReady"

    # ------------------------------------------------------------------
    # 4. Restart acceptor AFTER submit, before ChannelReady
    # ------------------------------------------------------------------
    def test_acceptor_restart_after_submit_resumes_to_channel_ready(self):
        channel_id, signed_tx = self._setup_external_open()

        result = _submit_signed(self.fiber1, channel_id, signed_tx)
        assert result["funding_tx_hash"]

        self._restart(self.fiber2)
        self.fiber1.connect_peer(self.fiber2)

        ch1 = self._wait_state(
            self.fiber1, self.fiber2.get_pubkey(), "ChannelReady", timeout=180
        )
        ch2 = self._wait_state(
            self.fiber2, self.fiber1.get_pubkey(), "ChannelReady", timeout=180
        )
        assert ch1["state"]["state_name"] == "ChannelReady"
        assert ch2["state"]["state_name"] == "ChannelReady"

    # ------------------------------------------------------------------
    # 5. Restart acceptor after it has sent its external-funding
    #    CommitmentSigned. This covers PR #1360's reestablish replay window.
    # ------------------------------------------------------------------
    def test_acceptor_restart_after_local_commitment_signed_reaches_ready(self):
        channel_id, signed_tx = self._setup_external_open()
        self.node.stop_miner()
        result = _submit_signed(self.fiber1, channel_id, signed_tx)
        assert result["funding_tx_hash"]

        replay_window_channel = self._wait_acceptor_commitment_replay_window(channel_id)
        assert _is_post_submit_commitment_replay_window(replay_window_channel)

        self._restart(self.fiber2)
        self.fiber1.connect_peer(self.fiber2)

        self.Miner.miner_until_tx_committed(self.node, result["funding_tx_hash"], True)
        self.node.start_miner()
        ch1 = self._wait_state(
            self.fiber1, self.fiber2.get_pubkey(), "ChannelReady", timeout=180
        )
        ch2 = self._wait_state(
            self.fiber2, self.fiber1.get_pubkey(), "ChannelReady", timeout=180
        )
        assert ch1["state"]["state_name"] == "ChannelReady"
        assert ch2["state"]["state_name"] == "ChannelReady"

        payment_hash = self.send_payment(self.fiber1, self.fiber2, 1 * 100000000)
        payment = self.fiber1.get_client().get_payment({"payment_hash": payment_hash})
        assert payment["status"] == "Success"

    # ------------------------------------------------------------------
    # 6. External-funding timeout after restart.
    # ------------------------------------------------------------------
    def test_external_funding_timeout_still_applies_after_restart(self):
        self._restart_fibers_with_config_overrides(
            fiber1_overrides={"external_funding_timeout_seconds": 30}
        )

        channel_id, _ = self._setup_external_open()

        self._assert_awaiting_external_funding(
            self.fiber1,
            self.fiber2.get_pubkey(),
            channel_id,
            "before restart",
        )

        self._restart(self.fiber1)
        self.fiber1.connect_peer(self.fiber2)

        self._assert_awaiting_external_funding(
            self.fiber1,
            self.fiber2.get_pubkey(),
            channel_id,
            "after restart before timeout",
        )

        # Failed channel openings are surfaced via only_pending=true even after
        # they transition to Closed(FUNDING_ABORTED).
        ch = self._wait_pending_closed(
            self.fiber1, self.fiber2.get_pubkey(), channel_id, timeout=90
        )
        assert ch["channel_id"] == channel_id, (
            f"unexpected channel surfaced as Closed: {ch['channel_id']} "
            f"(expected {channel_id})"
        )
        assert ch["state"]["state_name"] == "Closed"
        assert (
            ch["state"].get("state_flags") == "FUNDING_ABORTED"
        ), f"expected FUNDING_ABORTED, got {ch['state'].get('state_flags')!r}"
