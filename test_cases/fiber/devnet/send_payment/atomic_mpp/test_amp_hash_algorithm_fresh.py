from framework.basic_fiber import FiberTest


class TestAmpHashAlgorithmFresh(FiberTest):
    """
    PR-1411 regression: atomic MPP (AMP) success path for each invoice
    hash_algorithm using a fresh invoice per payment.

    The legacy test_cases/.../atomic_mpp/test_hash_algorithm.py reuses the
    same invoice in a loop, which the node now rejects with
    "Payment session already exists ... status: Success". This test proves
    the AMP success path still works on rc3 by issuing a new invoice each time.
    """

    def _amp_pay_once(self, hash_algorithm=None):
        params = {
            "amount": hex(1 * 100000000),
            "currency": "Fibd",
            "description": "amp invoice",
            "expiry": "0xe10",
            "final_cltv": "0x28",
            "allow_atomic_mpp": True,
        }
        if hash_algorithm is not None:
            params["hash_algorithm"] = hash_algorithm
        invoice = self.fiber2.get_client().new_invoice(params)
        payment = self.fiber1.get_client().send_payment(
            {"invoice": invoice["invoice_address"], "amp": True}
        )
        self.wait_payment_state(self.fiber1, payment["payment_hash"], "Success", 120)

    def test_amp_success_each_hash_algorithm(self):
        for _ in range(3):
            self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0)

        # default (none), sha256, ckb_hash -- fresh invoice each time
        self._amp_pay_once(None)
        self._amp_pay_once("sha256")
        self._amp_pay_once("ckb_hash")
