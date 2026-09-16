"""Regression: AnnounceWaitPrevAck settles on-chain without an actor panic.

Withhold the peer ACK, inject an inbound TLC into the local commitment, and
let the ACK timeout force-close it. Check an on-chain balance increase and
inbound TLC removal progress without panic or manual recovery. LocalRemoved
is progress, not final settlement; channel closure and outgoing TLC/payment
completion are not required by this regression.
"""

import os
import time

from framework.basic_p2p import P2pFiberTest

CKB = 100000000
PAYMENT_AMOUNT = 1 * CKB
ACK_TIMEOUT_SECONDS = 35
PANIC_MARKERS = (
    "panicked at",
    "assertion failed",
)


class TestAnnounceWaitPrevAckOnchainPanic(P2pFiberTest):
    tmp_path_name = "tmp-p2p-awpa"
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 5}
    fnn_log_level = "debug"
    # Keep local <= auto-accept min so open_channel uses accept_channel and
    # attacker actually funds the remote side (no rebalance payment).
    channel_local_balance = 50 * CKB
    channel_remote_balance = 200 * CKB

    @classmethod
    def teardown_class(cls):
        try:
            cls.restore_time()
        finally:
            super().teardown_class()

    def _tlc(self, fiber, payment_hash):
        channel = self.channel_of(fiber, include_closed=True)
        for tlc in channel.get("pending_tlcs") or []:
            if tlc.get("payment_hash") == payment_hash:
                return tlc
        return None

    def _wait_tlc(self, fiber, payment_hash, expected=None, timeout=60):
        last = None
        deadline = time.time() + timeout
        while time.time() < deadline:
            last = self._tlc(fiber, payment_hash)
            if last is not None and (
                expected is None or last.get("status") == expected
            ):
                return last
            time.sleep(0.5)
        raise TimeoutError(
            f"{fiber.tmp_path} TLC {payment_hash} status {last} != {expected}"
        )

    def _mine_watchtower_rounds(self, rounds=4):
        interval = self.start_fiber_config["fiber_watchtower_check_interval_seconds"]
        deadline = time.time() + interval * rounds + 2
        while time.time() < deadline:
            pool = self.node.getClient().get_raw_tx_pool()
            pending = list(pool.get("pending") or [])
            if pending:
                self.Miner.miner_until_tx_committed(self.node, pending[0])
            else:
                self.Miner.miner_with_version(self.node, "0x0")
            time.sleep(1)

    def _chain_ckb(self, fiber):
        lock_script = self.get_account_script(fiber.account_private)
        return int(
            self.node.getClient().get_cells_capacity(
                {
                    "script": lock_script,
                    "script_type": "lock",
                    "script_search_mode": "exact",
                }
            )["capacity"],
            16,
        )

    def _log_path(self, fiber):
        return os.path.join(fiber.tmp_path, "node.log")

    def _log_size(self, fiber):
        try:
            return os.path.getsize(self._log_path(fiber))
        except FileNotFoundError:
            return 0

    def _read_log(self, fiber, offset=0):
        with open(self._log_path(fiber), "rb") as log_file:
            log_file.seek(offset)
            return log_file.read().decode(errors="replace")

    def _panic_hits(self, log):
        lower = log.lower()
        return [marker for marker in PANIC_MARKERS if marker.lower() in lower]

    def _rpc_alive(self, fiber):
        try:
            fiber.get_client().node_info()
            return True
        except Exception:
            return False

    def test_inbound_announce_wait_prev_ack_settles_without_panic(self):
        ready = self.channel_of(self.victim)
        assert ready["state"]["state_name"] == "ChannelReady", ready
        victim_log_offset = self._log_size(self.victim)
        victim_ckb_before = self._chain_ckb(self.fiber1)

        # Capture victim CS so attacker does not auto-send CS / set waiting_ack.
        # Drop attacker RAA so victim stays waiting_ack after its own CS.
        self.peer.intercept(
            self.channel_id,
            capture_in=["CommitmentSigned"],
            drop_out=["RevokeAndAck"],
            drop_raa=True,
        )
        outbound = self.victim.get_client().send_payment(
            {
                "target_pubkey": self.attacker.get_pubkey(),
                "amount": hex(PAYMENT_AMOUNT),
                "keysend": True,
                "max_fee_rate": hex(1000000000000000),
            }
        )
        outbound_hash = outbound["payment_hash"]
        self.wait_payment_state(self.victim, outbound_hash, "Inflight", timeout=30)
        outbound_tlc = self._wait_tlc(
            self.victim, outbound_hash, {"Outbound": "LocalAnnounced"}
        )
        captured_cs = self.peer.wait("CommitmentSigned", timeout=10)
        assert captured_cs.get("kind") in (
            "CommitmentSigned",
            "commitment_signed",
        ), captured_cs

        # Attacker AddTlc + CS while victim is still waiting_ack. Dev add_tlc
        # does not need a graph route. The inbound TLC is written into the
        # local commitment as AnnounceWaitPrevAck.
        invoice = self.victim.get_client().new_invoice(
            {
                "amount": hex(PAYMENT_AMOUNT),
                "currency": "Fibd",
                "description": "announce-wait-prev-ack settlement regression",
                "payment_preimage": self.generate_random_preimage(),
                "hash_algorithm": "sha256",
            }
        )
        inbound_hash = invoice["invoice"]["data"]["payment_hash"]
        self.attacker.get_client().add_tlc(
            {
                "channel_id": self.channel_id,
                "amount": hex(PAYMENT_AMOUNT),
                "payment_hash": inbound_hash,
                # Release FNN uses 4h epochs; expiry must beat commitment delay.
                "expiry": hex((int(time.time()) + 24 * 60 * 60) * 1000),
            }
        )
        inbound = self._wait_tlc(
            self.victim,
            inbound_hash,
            {"Inbound": "AnnounceWaitPrevAck"},
            timeout=10,
        )
        assert inbound["status"] == {"Inbound": "AnnounceWaitPrevAck"}, inbound

        # 30s PEER_CHANNEL_RESPONSE_TIMEOUT: victim force-closes locally.
        deadline = time.time() + ACK_TIMEOUT_SECONDS + 15
        state = self.channel_state(self.victim, include_closed=True)
        while time.time() < deadline and state == "ChannelReady":
            time.sleep(1)
            state = self.channel_state(self.victim, include_closed=True)
        assert state != "ChannelReady", self.channel_of(
            self.victim, include_closed=True
        )

        close_tx = self.wait_and_check_tx_pool_fee(1000, False, try_size=40)
        self.Miner.miner_until_tx_committed(self.node, close_tx)
        # Both TLCs are in the local commitment; allow on-chain expiry handling.
        # The keysend expiry can be much later than the injected inbound TLC.
        # Advance past the later expiry, then mine the delay epoch.
        expiry_ms = max(int(tlc["expiry"], 16) for tlc in (outbound_tlc, inbound))
        hours = max(1, (expiry_ms - int(time.time() * 1000) + 3599999) // 3600000 + 1)
        self.add_time_and_generate_epoch(hours, 1)
        # Let the running watchtower settle without restarting the local node.
        deadline = time.monotonic() + 120
        victim_ckb_after = self._chain_ckb(self.fiber1)
        while time.monotonic() < deadline and victim_ckb_after <= victim_ckb_before:
            assert self._rpc_alive(self.victim), "victim RPC stopped during settlement"
            log = self._read_log(self.victim, victim_log_offset)
            assert not self._panic_hits(log), log[-4000:]
            self._mine_watchtower_rounds(1)
            victim_ckb_after = self._chain_ckb(self.fiber1)
        assert victim_ckb_after > victim_ckb_before, (
            f"fiber1 did not recover on-chain funds: "
            f"before={victim_ckb_before}, after={victim_ckb_after}"
        )

        # LocalRemoved proves inbound removal progressed without the old panic.
        # It does not mean settlement is complete or require a peer ACK.
        deadline = time.monotonic() + 120
        while True:
            assert self._rpc_alive(self.victim), "victim RPC stopped during settlement"
            log = self._read_log(self.victim, victim_log_offset)
            assert not self._panic_hits(log), log[-4000:]
            channel_after = self.channel_of(self.fiber1, include_closed=True)
            tlcs = {tlc["payment_hash"]: tlc for tlc in channel_after["pending_tlcs"]}
            inbound_after = tlcs.get(inbound_hash)
            inbound_progressed = inbound_after is None or inbound_after["status"] in (
                {"Inbound": "LocalRemoved"},
                {"Inbound": "RemoveAckConfirmed"},
            )
            if inbound_progressed:
                break
            if time.monotonic() >= deadline:
                break
            self._mine_watchtower_rounds(1)

        assert inbound_progressed, {
            "payment_hash": inbound_hash,
            "inbound_tlc": inbound_after,
            "state": channel_after["state"],
        }
        # Outgoing TLC/payment states are diagnostics, not completion criteria.
        outbound_after = tlcs.get(outbound_hash)
        payment_after = self.fiber1.get_client().get_payment(
            {"payment_hash": outbound_hash}
        )
        print(
            "after announce-wait-prev-ack inbound removal progress",
            {
                "close_tx": close_tx,
                "fiber1_ckb_before": victim_ckb_before,
                "fiber1_ckb_after": victim_ckb_after,
                "state": channel_after["state"],
                "inbound_tlc": (inbound_after or {}).get("status"),
                "outbound_tlc": (outbound_after or {}).get("status"),
                "payment": payment_after["status"],
            },
        )
