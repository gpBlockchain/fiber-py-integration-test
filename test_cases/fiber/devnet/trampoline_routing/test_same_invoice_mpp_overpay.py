import hashlib

from framework.basic_fiber import FiberTest


def sha256_hex(preimage_hex):
    raw = bytes.fromhex(preimage_hex.replace("0x", ""))
    return "0x" + hashlib.sha256(raw).hexdigest()


class TestSameInvoiceMppOverpay(FiberTest):
    """
    PR-1289 regression: trampoline MPP overpay to one hold invoice.
    """

    def test_trampoline_mpp_same_hold_invoice_overpay_does_not_poison_route(self):
        self.fiber3 = self.start_new_fiber(self.generate_account(10000))
        self.fiber4 = self.start_new_fiber(self.generate_account(10000))
        self.fiber5 = self.start_new_fiber(self.generate_account(10000))

        payer_a = self.fiber1
        trampoline_a = self.fiber2
        payer_b = self.fiber3
        trampoline_b = self.fiber4
        payee = self.fiber5

        self.open_channel(payer_a, trampoline_a, 1000 * 100000000, 0)
        self.open_channel(trampoline_a, payee, 1000 * 100000000, 0)
        self.open_channel(payer_b, trampoline_b, 1000 * 100000000, 0)
        self.open_channel(trampoline_b, payee, 1000 * 100000000, 0)

        preimage = self.generate_random_preimage()
        payment_hash = sha256_hex(preimage)
        invoice = payee.get_client().new_invoice(
            {
                "amount": hex(1 * 100000000),
                "currency": "Fibd",
                "description": "trampoline mpp overpay hold invoice",
                "payment_hash": payment_hash,
                "hash_algorithm": "sha256",
                "allow_mpp": True,
                "allow_trampoline_routing": True,
            }
        )

        payer_a.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "trampoline_hops": [
                    trampoline_a.get_client().node_info()["pubkey"],
                ],
            }
        )
        self.wait_invoice_state(payee, payment_hash, "Received", 120, 1)

        second_send_error = None
        try:
            payer_b.get_client().send_payment(
                {
                    "invoice": invoice["invoice_address"],
                    "trampoline_hops": [
                        trampoline_b.get_client().node_info()["pubkey"],
                    ],
                }
            )
        except Exception as e:
            second_send_error = str(e)

        if second_send_error is None:
            second_payment = self.wait_payment_finished(payer_b, payment_hash, 120)
            assert second_payment["status"] == "Failed"

        payee.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )
        first_payment = self.wait_payment_finished(payer_a, payment_hash, 120)
        assert first_payment["status"] == "Success"

        invoice_after_settle = payee.get_client().get_invoice(
            {"payment_hash": payment_hash}
        )
        assert invoice_after_settle["status"] == "Paid"

        for fiber in (payer_a, trampoline_a, payer_b, trampoline_b, payee):
            self.wait_fibers_pending_tlc_eq0(fiber, 60)

        retry_invoice = payee.get_client().new_invoice(
            {
                "amount": hex(1 * 100000000),
                "currency": "Fibd",
                "description": "trampoline mpp overpay retry",
                "payment_preimage": self.generate_random_preimage(),
                "hash_algorithm": "sha256",
                "allow_trampoline_routing": True,
            }
        )
        retry_payment = payer_b.get_client().send_payment(
            {
                "invoice": retry_invoice["invoice_address"],
                "trampoline_hops": [
                    trampoline_b.get_client().node_info()["pubkey"],
                ],
            }
        )
        self.wait_payment_state(payer_b, retry_payment["payment_hash"], "Success", 120)
