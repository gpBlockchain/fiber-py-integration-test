from framework.basic_fiber import FiberTest


class TestTlcFee(FiberTest):

    def test_01(self):
        """
        Returns:
        """
        for i in range(2):
            self.start_new_fiber(self.generate_account(10000))
        self.open_channel(
            self.fibers[0],
            self.fibers[1],
            1000 * 100000000,
            1000 * 100000000,
            1000,
            1000,
        )
        self.open_channel(
            self.fibers[1],
            self.fibers[2],
            1000 * 100000000,
            1000 * 100000000,
            2000,
            1000,
        )
        self.open_channel(
            self.fibers[2],
            self.fibers[3],
            1000 * 100000000,
            1000 * 100000000,
            3000,
            1000,
        )

        payment_hash = self.send_payment(self.fibers[0], self.fibers[3], 1 * 100000000)
        payment = (
            self.fibers[0].get_client().get_payment({"payment_hash": payment_hash})
        )
        amount = 1 * 100000000
        payment = (
            self.fibers[0]
            .get_client()
            .send_payment(
                {
                    "target_pubkey": self.fibers[3].get_client().node_info()["node_id"],
                    "amount": hex(amount),
                    "keysend": True,
                    "allow_self_payment": True,
                    "udt_type_script": None,
                    "dry_run": True,
                }
            )
        )
        payment = (
            self.fibers[0]
            .get_client()
            .send_payment(
                {
                    "target_pubkey": self.fibers[3].get_client().node_info()["node_id"],
                    "amount": hex(amount),
                    "keysend": True,
                    "allow_self_payment": True,
                    "udt_type_script": None,
                    "max_fee_amount": payment["fee"],
                }
            )
        )
        self.wait_payment_state(
            self.fibers[0], payment["payment_hash"], "Success", 1200
        )
        assert int(payment["fee"], 16) == self.calculate_tx_fee(amount, [2000, 3000])
