import hashlib
import time

from framework.basic_fiber import FiberTest


def sha256_hex(preimage_hex):
    raw = bytes.fromhex(preimage_hex.replace("0x", ""))
    return "0x" + hashlib.sha256(raw).hexdigest()


class TestMppHoldSettleReplayAfterCrash(FiberTest):
    """
    PR-1709 crash-recovery regression: settling a received MPP hold invoice
    while the payer is offline, then crashing the payee, must still recover
    after restart (payment Success, invoice Paid, no pending TLCs). Whether the
    crash lands inside the internal durable-queue/hold-store handoff window is
    timing-dependent and NOT a precondition of these cases; the upstream unit
    tests cover the internal replay path itself.
    """

    # TEST-MAP: PR1709-01
    def test_mpp_hold_settle_replay_after_payee_crash_and_peer_stop(self):
        # two parallel channels force the 1001-CKB invoice to split into two TLC parts
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1000 * 100000000)
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1000 * 100000000)

        preimage = self.generate_random_preimage()
        payment_hash = sha256_hex(preimage)
        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(1001 * 100000000),
                "currency": "Fibd",
                "description": "mpp hold settle replay after payee crash",
                "payment_hash": payment_hash,
                "hash_algorithm": "sha256",
                "allow_mpp": True,
            }
        )
        self.fiber1.get_client().send_payment({"invoice": invoice["invoice_address"]})

        # barrier: both TLC parts have arrived at the payee before cutting the peer
        self.wait_invoice_state(self.fiber2, payment_hash, "Received", 120, 1)

        # peer offline, settle, then SIGKILL the payee right after the settle RPC
        # returns; restart both sides and reconnect. Crash timing relative to the
        # internal handoff is not controlled on purpose (crash-recovery regression).
        self.fiber1.stop()
        self.fiber2.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )
        self.fiber2.force_stop()
        self.fiber2.start(fnn_log_level=self.fnn_log_level)
        self.fiber1.start(fnn_log_level=self.fnn_log_level)
        self.fiber1.connect_peer(self.fiber2)
        time.sleep(3)

        self.wait_payment_state(self.fiber1, payment_hash, "Success", 180)
        invoice_after_restart = self.fiber2.get_client().get_invoice(
            {"payment_hash": payment_hash}
        )
        assert invoice_after_restart["status"] == "Paid"

        for fiber in (self.fiber1, self.fiber2):
            self.wait_fibers_pending_tlc_eq0(fiber, 60)

    # TEST-MAP: PR1709-02
    def test_mpp_hold_settle_replay_after_payee_crash_and_peer_disconnect(self):
        # same as PR1709-01, but the payer goes offline via RPC disconnect instead of
        # a process stop, so the fulfill queue forms over a live-but-disconnected peer
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1000 * 100000000)
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1000 * 100000000)

        preimage = self.generate_random_preimage()
        payment_hash = sha256_hex(preimage)
        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(1001 * 100000000),
                "currency": "Fibd",
                "description": "mpp hold settle replay after disconnect",
                "payment_hash": payment_hash,
                "hash_algorithm": "sha256",
                "allow_mpp": True,
            }
        )
        self.fiber1.get_client().send_payment({"invoice": invoice["invoice_address"]})

        # barrier: both TLC parts have arrived at the payee before cutting the peer
        self.wait_invoice_state(self.fiber2, payment_hash, "Received", 120, 1)

        self.fiber2.get_client().disconnect_peer({"pubkey": self.fiber1.get_pubkey()})
        time.sleep(1)
        self.fiber2.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )
        self.fiber2.force_stop()
        self.fiber2.start(fnn_log_level=self.fnn_log_level)
        self.fiber1.connect_peer(self.fiber2)
        time.sleep(3)

        self.wait_payment_state(self.fiber1, payment_hash, "Success", 180)
        invoice_after_restart = self.fiber2.get_client().get_invoice(
            {"payment_hash": payment_hash}
        )
        assert invoice_after_restart["status"] == "Paid"

        for fiber in (self.fiber1, self.fiber2):
            self.wait_fibers_pending_tlc_eq0(fiber, 60)
