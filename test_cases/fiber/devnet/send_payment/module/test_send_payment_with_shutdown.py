import pytest

from framework.basic_fiber import FiberTest


class TestSendPaymentWithShutdown(FiberTest):
    # @pytest.mark.skip("https://github.com/nervosnetwork/fiber/issues/503")
    def test_shutdown_in_send_payment(self):
        self.start_new_fiber(self.generate_account(10000))
        self.start_new_fiber(self.generate_account(10000))
        self.open_channel(
            self.fibers[0], self.fibers[1], 1000 * 100000000, 1000 * 100000000
        )
        self.open_channel(
            self.fibers[1], self.fibers[2], 1000 * 100000000, 1000 * 100000000
        )
        self.open_channel(
            self.fibers[2], self.fibers[3], 1000 * 100000000, 1000 * 100000000
        )
        payment_node1_hashes = []
        payment_node3_hashes = []
        for i in range(10):
            payment_hash = self.send_payment(self.fibers[0], self.fibers[3], 1, False)
            payment_node1_hashes.append(payment_hash)
            payment_hash = self.send_payment(self.fibers[3], self.fibers[0], 1, False)
            payment_node3_hashes.append(payment_hash)
        N3N4_CHANNEL_ID = (
            self.fibers[3].get_client().list_channels({})["channels"][0]["channel_id"]
        )

        self.fibers[3].get_client().shutdown_channel(
            {
                "channel_id": N3N4_CHANNEL_ID,
                "close_script": {
                    "code_hash": "0x1bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8",
                    "hash_type": "type",
                    "args": self.fibers[3].get_account()["lock_arg"],
                },
                "fee_rate": "0x3FC",
            }
        )
        tx_hash = self.wait_and_check_tx_pool_fee(1000, False, 120)
        tx_message = self.get_tx_message(tx_hash)
        for payment_hash in payment_node1_hashes:
            payment = (
                self.fibers[0].get_client().get_payment({"payment_hash": payment_hash})
            )
            # todo add check
            # print("payment status:", payment["status"])
            self.wait_payment_finished(self.fibers[0], payment_hash, 1200)
        for payment_hash in payment_node3_hashes:
            payment = (
                self.fibers[3].get_client().get_payment({"payment_hash": payment_hash})
            )
            # todo add check
            # print("payment status:", payment["status"])
            self.wait_payment_finished(self.fibers[3], payment_hash, 1200)

        # print("tx message:", tx_message)

    @pytest.mark.skip("https://github.com/nervosnetwork/fiber/issues/503")
    def test_force_shutdown_in_send_payment(self):
        """"""
        self.start_new_fiber(self.generate_account(10000))
        self.start_new_fiber(self.generate_account(10000))
        self.open_channel(
            self.fibers[0], self.fibers[1], 1000 * 100000000, 1000 * 100000000
        )
        self.open_channel(
            self.fibers[1], self.fibers[2], 1000 * 100000000, 1000 * 100000000
        )
        self.open_channel(
            self.fibers[2], self.fibers[3], 1000 * 100000000, 1000 * 100000000
        )
        payment_hashes = []
        for i in range(10):
            payment_hash = self.send_payment(self.fibers[0], self.fibers[3], 1, False)
            payment_hashes.append(payment_hash)
        N3N4_CHANNEL_ID = (
            self.fibers[3].get_client().list_channels({})["channels"][0]["channel_id"]
        )

        self.fibers[3].get_client().shutdown_channel(
            {
                "channel_id": N3N4_CHANNEL_ID,
                # "close_script": {
                #     "code_hash": "0x1bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8",
                #     "hash_type": "type",
                #     "args": self.fibers[3].get_account()["lock_arg"],
                # },
                "force": True,
                # "fee_rate": "0x3FC",
            }
        )
        tx_hash = self.wait_and_check_tx_pool_fee(1000, False, 120)
        tx_message = self.get_tx_message(tx_hash)
        print("tx_message:")
        for payment_hash in payment_hashes:
            payment = (
                self.fibers[0].get_client().get_payment({"payment_hash": payment_hash})
            )
            # print("payment status:", payment["status"])
            self.wait_payment_finished(self.fibers[0], payment_hash, 1200)
