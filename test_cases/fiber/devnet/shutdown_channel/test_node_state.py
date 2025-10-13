import time

import pytest

from framework.basic_fiber import FiberTest


class TestNodeState(FiberTest):
    """
    交易中 的节点
        强制关闭
        非强制关闭
    节点不在线
        强制关闭
        非强制关闭

    """

    # @pytest.mark.skip("node1 send payment node4 failed")
    # @pytest.mark.skip("交易发送一半，如果交易卡在Inflight，下一笔交易好像也发不出去")
    def test_shutdown_in_send_payment(self):
        """
        payment state 最终会failed

        Returns:

        """
        account_private_3 = self.generate_account(10000)
        account_private_4 = self.generate_account(10000)
        self.fiber3 = self.start_new_fiber(account_private_3)
        self.fiber4 = self.start_new_fiber(account_private_4)
        self.fiber2.connect_peer(self.fiber3)
        self.fiber3.connect_peer(self.fiber4)
        self.fiber4.connect_peer(self.fiber1)
        # node1 open channel node2
        self.open_channel(self.fiber1, self.fiber2, 2000 * 100000000, 1, 0, 0)

        # node2 open channel node3
        self.open_channel(self.fiber2, self.fiber3, 2000 * 100000000, 1, 0, 0)

        # node3 open channel node4
        self.open_channel(self.fiber3, self.fiber4, 2000 * 100000000, 1, 0, 0)

        time.sleep(3)
        # node1 send payment to node4
        node4_info = self.fiber4.get_client().node_info()
        fiber4_pub = node4_info["node_id"]
        for i in range(30):
            payment = self.fiber1.get_client().send_payment(
                {
                    "target_pubkey": fiber4_pub,
                    "amount": hex(10 * 100000000),
                    "keysend": True,
                    # "invoice": "0x123",
                }
            )

        # self.wait_payment_state(self.fiber1, payment["payment_hash"], "Success", 120)
        N3N4_CHANNEL_ID = self.fiber4.get_client().list_channels({})["channels"][0][
            "channel_id"
        ]
        self.get_fibers_balance_message()
        fiber4_before_ckb_balance = self.Ckb_cli.wallet_get_capacity(
            self.fiber4.get_account()["address"]["testnet"]
        )
        # todo 多编写一些多节点shutdown的用例
        for i in range(10):
            try:
                self.fiber3.get_client().shutdown_channel(
                    {
                        "channel_id": N3N4_CHANNEL_ID,
                        "close_script": {
                            "code_hash": "0x1bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8",
                            "hash_type": "type",
                            "args": self.fiber3.get_account()["lock_arg"],
                        },
                        "fee_rate": "0x3FC",
                    }
                )
                break
            except Exception as e:
                print("e:", e)
                time.sleep(1)
        tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        fiber4_after_ckb_balance = self.Ckb_cli.wallet_get_capacity(
            self.fiber4.get_account()["address"]["testnet"]
        )

        msg = self.get_tx_message(tx_hash)
        if msg["output_cells"][0]["args"] == self.fibers[3].get_account()["lock_arg"]:
            fiber4_balance = msg["output_cells"][0]["capacity"]
        else:
            fiber4_balance = msg["output_cells"][1]["capacity"]
        self.wait_fibers_pending_tlc_eq0(self.fiber1, 120)
        fiber_balance = self.get_fiber_balance(self.fiber1)
        fiber1_send_balance = 2000 * 100000000 - fiber_balance["ckb"]["local_balance"]
        fiber4_receive_balance = fiber4_balance - 62 * 100000000 - 1
        channels = self.fibers[3].get_client().list_channels({"include_closed": True})
        print("fiber4_before_ckb_balance:", fiber4_before_ckb_balance)
        print("fiber4_after_ckb_balance:", fiber4_after_ckb_balance)
        print("fiber1_send_balance:", fiber1_send_balance)
        print("fiber_balance:", fiber_balance)
        print("channels:", channels)
        assert channels["channels"][0]["received_tlc_balance"] == "0x0"
        assert fiber1_send_balance == fiber4_receive_balance

        # payment = self.fiber1.get_client().send_payment(
        #     {
        #         "target_pubkey": fiber4_pub,
        #         "amount": hex(1 * 100000000),
        #         "keysend": True,
        #         # "invoice": "0x123",
        #     }
        # )
        # tx_hash = self.wait_and_check_tx_pool_fee(1000, False, 120)
        # tx_message = self.get_tx_message(tx_hash)
        # time.sleep(1)
        # payment = self.fiber1.get_client().get_payment(
        #     {"payment_hash": payment["payment_hash"]}
        # )
        # print("payment:", payment)
        # self.wait_payment_state(self.fiber1, payment["payment_hash"], "Failed", 120)
        # assert {
        #     "args": self.fiber4.get_account()["lock_arg"],
        #     "capacity": 6300000000,
        # } in tx_message["output_cells"]
        # payment = self.fiber1.get_client().send_payment(
        #     {
        #         "target_pubkey": self.fiber3.get_client().node_info()["node_id"],
        #         "amount": hex(1 * 100000000),
        #         "keysend": True,
        #         # "invoice": "0x123",
        #     }
        # )
        # self.wait_payment_state(self.fiber1, payment["payment_hash"])
