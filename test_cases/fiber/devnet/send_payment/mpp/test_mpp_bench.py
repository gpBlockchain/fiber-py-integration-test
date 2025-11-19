import time

import pytest

from framework.basic_fiber import FiberTest
from framework.config import DEFAULT_MIN_DEPOSIT_CKB


class MppBench(FiberTest):

    def test_bench_self(self):
        self.fiber3 = self.start_new_fiber(
            self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
        )
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber2, self.fiber3, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber2, self.fiber3, 1000 * 100000000, 0, 0, 0)

        self.open_channel(self.fiber3, self.fiber1, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber3, self.fiber1, 1000 * 100000000, 0, 0, 0)

        time.sleep(10)
        payments = [[]]
        for i in range(100):
            for i in range(3):
                try:
                    payment_hash = self.send_invoice_payment(
                        self.fibers[i],
                        self.fibers[i],
                        1001 * 100000000,
                        False,
                        try_count=0,
                    )
                    payments[i].append(payment_hash)
                except:
                    pass
        for i in range(len(payments)):
            for payment_hash in payments[i]:
                self.wait_payment_finished(self.fibers[i], payment_hash, 1000)
        time.sleep(200)
        self.get_fiber_graph_balance()
        for i in range(3):
            payment_hash = self.send_invoice_payment(
                self.fibers[i], self.fibers[i], 100 * 100000000, True
            )
        for fiber in self.fibers:
            fiber1_channels = fiber.get_client().list_channels({})
            for channel in fiber1_channels["channels"]:
                fiber.get_client().shutdown_channel(
                    {
                        "channel_id": channel["channel_id"],
                        "close_script": self.get_account_script(fiber.account_private),
                        "fee_rate": "0x3FC",
                        # "force": True,
                    }
                )
                shutdown_tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
                self.Miner.miner_until_tx_committed(self.node, shutdown_tx_hash)
                shutdown_tx = self.get_tx_message(shutdown_tx_hash)
                print("Shutdown tx :", shutdown_tx)
                print("tx message:", channel)
                assert {
                    "args": self.get_account_script(fiber.account_private)["args"],
                    "capacity": int(channel["local_balance"], 16)
                    + DEFAULT_MIN_DEPOSIT_CKB
                    - shutdown_tx["fee"],
                } in shutdown_tx["output_cells"]

    @pytest.mark.skip("not stable: stop cause mutilSig Err")
    def test_bench_self_with_stop(self):
        self.fiber3 = self.start_new_fiber(
            self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
        )
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber2, self.fiber3, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber2, self.fiber3, 1000 * 100000000, 0, 0, 0)

        self.open_channel(self.fiber3, self.fiber1, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber3, self.fiber1, 1000 * 100000000, 0, 0, 0)

        time.sleep(10)
        payments = [[]]
        for i in range(100):
            for i in range(3):
                try:
                    payment_hash = self.send_invoice_payment(
                        self.fibers[i],
                        self.fibers[i],
                        1001 * 100000000,
                        False,
                        try_count=0,
                    )
                    payments[i].append(payment_hash)
                except:
                    pass
        self.fibers[2].stop()
        time.sleep(100)
        self.send_payment(self.fiber1, self.fiber2, 1 * 100000000)
        self.send_payment(self.fiber2, self.fiber1, 1 * 100000000)
        time.sleep(100)
        self.send_payment(self.fiber1, self.fiber2, 1 * 100000000)
        self.send_payment(self.fiber2, self.fiber1, 1 * 100000000)
        self.fibers[2].start()
        self.send_payment(self.fiber1, self.fiber2, 1 * 100000000)
        self.send_payment(self.fiber2, self.fiber1, 1 * 100000000)
        self.get_fiber_graph_balance()
        self.fiber1.connect_peer(self.fibers[2])
        self.fiber2.connect_peer(self.fibers[2])
        time.sleep(200)
        for fiber in self.fibers:
            balance = self.get_fiber_balance(fiber)
            assert balance["ckb"]["offered_tlc_balance"] == 0
            assert balance["ckb"]["received_tlc_balance"] == 0
        for i in range(3):
            for i in range(3):
                payment_hash = self.send_payment(
                    self.fibers[i], self.fibers[i], 1 * 100000000, True, try_count=3
                )
        for fiber in self.fibers:
            balance = self.get_fiber_balance(fiber)
            assert balance["ckb"]["offered_tlc_balance"] == 0
            assert balance["ckb"]["received_tlc_balance"] == 0
        # for i in range(len(payments)):
        #     for payment_hash in payments[i]:
        #         self.wait_payment_finished(self.fibers[i], payment_hash, 1000)
        # time.sleep(200)
        # self.get_fiber_graph_balance()
        # for i in range(3):
        #     payment_hash = self.send_invoice_payment(
        #         self.fibers[i],
        #         self.fibers[i],
        #         100 * 100000000,
        #         True
        #     )
        # for fiber in self.fibers:
        #     fiber1_channels = fiber.get_client().list_channels({})
        #     for channel in fiber1_channels['channels']:
        #         fiber.get_client().shutdown_channel({
        #             "channel_id": channel['channel_id'],
        #             "close_script": self.get_account_script(fiber.account_private),
        #             "fee_rate": "0x3FC",
        #             # "force": True,
        #         })
        #         shutdown_tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        #         self.Miner.miner_until_tx_committed(self.node, shutdown_tx_hash)
        #         shutdown_tx = self.get_tx_message(shutdown_tx_hash)
        #         print("Shutdown tx :", shutdown_tx)
        #         print("tx message:", channel)
        #         assert {'args': self.get_account_script(fiber.account_private)['args'],
        #                 'capacity': int(channel['local_balance'],16)+ 62 *100000000 - shutdown_tx['fee']} in shutdown_tx['output_cells']
