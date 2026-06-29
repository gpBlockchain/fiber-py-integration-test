import hashlib
import time

from framework.basic_fiber import FiberTest


def sha256_hex(preimage_hex):
    raw = bytes.fromhex(preimage_hex.replace("0x", ""))
    return "0x" + hashlib.sha256(raw).hexdigest()


class TestHoldInvoiceRegressions(FiberTest):
    """
    PR-1289 regressions around hold invoice TLC-set state transitions.
    """

    def test_same_hold_invoice_rejected_after_invoice_received(self):
        self.fiber3 = self.start_new_fiber(self.generate_account(10000))
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1000 * 100000000)
        self.open_channel(self.fiber2, self.fiber3, 1000 * 100000000, 1000 * 100000000)

        preimage = self.generate_random_preimage()
        payment_hash = sha256_hex(preimage)
        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(1 * 100000000),
                "currency": "Fibd",
                "description": "same hold invoice after received",
                "payment_hash": payment_hash,
                "hash_algorithm": "sha256",
            }
        )

        self.fiber1.get_client().send_payment({"invoice": invoice["invoice_address"]})
        self.wait_invoice_state(self.fiber2, payment_hash, "Received", 120, 1)

        second_send_error = None
        try:
            self.fiber3.get_client().send_payment(
                {"invoice": invoice["invoice_address"]}
            )
        except Exception as e:
            second_send_error = str(e)

        if second_send_error is None:
            second_payment = self.wait_payment_finished(self.fiber3, payment_hash, 120)
            assert second_payment["status"] == "Failed"

        self.fiber2.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )
        first_payment = self.wait_payment_finished(self.fiber1, payment_hash, 120)
        assert first_payment["status"] == "Success"

        invoice_after_settle = self.fiber2.get_client().get_invoice(
            {"payment_hash": payment_hash}
        )
        assert invoice_after_settle["status"] == "Paid"

        for fiber in (self.fiber1, self.fiber2, self.fiber3):
            self.wait_fibers_pending_tlc_eq0(fiber, 60)

    def test_received_hold_invoice_settle_retries_after_payee_restart(self):
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1000 * 100000000)

        preimage = self.generate_random_preimage()
        payment_hash = sha256_hex(preimage)
        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(1 * 100000000),
                "currency": "Fibd",
                "description": "received hold invoice settle retry after restart",
                "payment_hash": payment_hash,
                "hash_algorithm": "sha256",
            }
        )

        self.fiber1.get_client().send_payment({"invoice": invoice["invoice_address"]})
        self.wait_invoice_state(self.fiber2, payment_hash, "Received", 120, 1)

        self.fiber1.stop()
        self.fiber2.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )
        self.fiber2.stop()
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
