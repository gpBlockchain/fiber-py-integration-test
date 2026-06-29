import concurrent.futures
import hashlib

from framework.basic_fiber import FiberTest


def sha256_hex(preimage_hex):
    raw = bytes.fromhex(preimage_hex.replace("0x", ""))
    return "0x" + hashlib.sha256(raw).hexdigest()


class TestSameInvoiceRace(FiberTest):
    """
    PR-1289 regression: concurrent trampoline payments to the same hold invoice.
    """

    def test_same_hold_invoice_two_trampoline_paths_only_one_settles(self):
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
                "description": "same hold invoice trampoline race",
                "payment_hash": payment_hash,
                "hash_algorithm": "sha256",
                "allow_trampoline_routing": True,
            }
        )

        results = {}

        def send_invoice_payment(name, payer, trampoline):
            try:
                payment = payer.get_client().send_payment(
                    {
                        "invoice": invoice["invoice_address"],
                        "trampoline_hops": [
                            trampoline.get_client().node_info()["pubkey"],
                        ],
                    }
                )
                results[name] = {"payment_hash": payment["payment_hash"]}
            except Exception as e:
                results[name] = {"error": str(e)}

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(send_invoice_payment, "payer_a", payer_a, trampoline_a),
                executor.submit(send_invoice_payment, "payer_b", payer_b, trampoline_b),
            ]
            concurrent.futures.wait(futures)

        assert set(results) == {"payer_a", "payer_b"}
        self.wait_invoice_state(payee, payment_hash, "Received", 120, 1)

        payee.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )

        final_statuses = {}
        for name, payer in (("payer_a", payer_a), ("payer_b", payer_b)):
            if "error" in results[name]:
                final_statuses[name] = "Failed"
                continue
            final_statuses[name] = self.wait_payment_finished(payer, payment_hash, 120)[
                "status"
            ]

        assert sorted(final_statuses.values()) == ["Failed", "Success"]

        invoice_after_settle = payee.get_client().get_invoice(
            {"payment_hash": payment_hash}
        )
        assert invoice_after_settle["status"] == "Paid"

        for fiber in (payer_a, trampoline_a, payer_b, trampoline_b, payee):
            self.wait_fibers_pending_tlc_eq0(fiber, 60)

        failed_payer = payer_a
        failed_trampoline = trampoline_a
        if final_statuses["payer_b"] == "Failed":
            failed_payer = payer_b
            failed_trampoline = trampoline_b

        retry_preimage = self.generate_random_preimage()
        retry_invoice = payee.get_client().new_invoice(
            {
                "amount": hex(1 * 100000000),
                "currency": "Fibd",
                "description": "trampoline retry after hold tlc timeout",
                "payment_preimage": retry_preimage,
                "hash_algorithm": "sha256",
                "allow_trampoline_routing": True,
            }
        )
        retry_payment = failed_payer.get_client().send_payment(
            {
                "invoice": retry_invoice["invoice_address"],
                "trampoline_hops": [
                    failed_trampoline.get_client().node_info()["pubkey"],
                ],
            }
        )
        self.wait_payment_state(
            failed_payer, retry_payment["payment_hash"], "Success", 120
        )
