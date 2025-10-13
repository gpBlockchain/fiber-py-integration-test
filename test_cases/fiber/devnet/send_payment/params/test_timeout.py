from framework.basic_fiber import FiberTest


class TestTimeout(FiberTest):

    def test_01(self):
        self.fiber3 = self.start_new_fiber(self.generate_account(1000))
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1000 * 100000000)
        self.open_channel(self.fiber2, self.fiber3, 1000 * 100000000, 1000 * 100000000)
        payment = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": self.fiber3.get_client().node_info()["node_id"],
                "amount": hex(10 * 100000000),
                "keysend": True,
                "timeout": hex(0),
            }
        )
        self.wait_payment_state(self.fiber1, payment["payment_hash"], "Success", 100)

        # for i in range(100):
        #     payment = self.fiber1.get_client().send_payment({
        #         "target_pubkey": self.fiber3.get_client().node_info()["node_id"],
        #         "amount": hex(1 * 10000000),
        #         "keysend": True,
        #         "timeout": hex(0)
        #     })
        #     payments.append(payment)
        # for i in range(100):
        #     payment = payments[i]
        #     print(i, payment)
        #     self.wait_payment_finished(self.fiber1, payment["payment_hash"], 1000)
        # payment_result = []
        # for i in range(100):
        #     ret = self.fiber1.get_client().get_payment({
        #         "payment_hash": payments[i]["payment_hash"]
        #     })
        #     payment_result.append(ret)
        # for i in range(100):
        #     ret = payment_result[i]
        #     print(i, ret)
