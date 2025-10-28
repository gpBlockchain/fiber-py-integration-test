import time
from framework.basic_fiber import FiberTest


class TestForceRestart(FiberTest):
    # FiberTest.debug = True

    def test_force_restart_fiber_node_shutdown_channel(self):
        """
        shutdown过程强制重启
        1.发起方强制重启
        2.接受方强制重启
        3.检查通道是否被正常关闭，并且检查balance是否返还
        Returns:

        """
        self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
            }
        )
        open_channel_tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY", 120
        )
        channels = self.fiber1.get_client().list_channels(
            {"peer_id": self.fiber2.get_peer_id()}
        )
        N1N2_CHANNEL_ID = channels["channels"][0]["channel_id"]
        self.fiber1.get_client().graph_channels()

        before_balance1 = self.Ckb_cli.wallet_get_capacity(
            self.account1["address"]["testnet"]
        )
        before_balance2 = self.Ckb_cli.wallet_get_capacity(
            self.account2["address"]["testnet"]
        )
        # shutdown channel
        N1N2_CHANNEL_ID = self.fiber1.get_client().list_channels({})["channels"][0][
            "channel_id"
        ]
        print(N1N2_CHANNEL_ID)
        cell = self.node.getClient().get_live_cell("0x0", open_channel_tx_hash)
        assert cell["status"] == "live"

        shutdown_content = self.fiber1.get_client().shutdown_channel(
            {
                "channel_id": N1N2_CHANNEL_ID,
                "close_script": {
                    "code_hash": "0x9bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8",
                    "hash_type": "type",
                    "args": self.account1["lock_arg"],
                },
                "fee_rate": "0x3FC",
            }
        )
        print(f"shutdown_content:{shutdown_content}")  # return None
        result = self.node.getClient().get_live_cell("0x0", open_channel_tx_hash)
        assert result["status"] == "live"
        # 1.接收方stop
        self.fiber2.force_stop()
        time.sleep(10)
        result = self.node.getClient().get_live_cell("0x0", open_channel_tx_hash)
        assert result["status"] == "unknown"
        # 2.发送方重启
        self.fiber1.force_stop()
        time.sleep(10)
        self.fiber1.start()
        time.sleep(10)
        # 3.接收方start
        self.fiber2.start()
        time.sleep(10)
        result = self.node.getClient().get_live_cell("0x0", open_channel_tx_hash)
        assert result["status"] == "unknown"
        print(f"result=:{result}")
        # 2.检查channel被close成功
        node_info = self.fiber1.get_client().node_info()
        print("node info :", node_info)
        assert node_info["channel_count"] == "0x0"
        after_balance1 = self.Ckb_cli.wallet_get_capacity(
            self.account1["address"]["testnet"]
        )
        after_balance2 = self.Ckb_cli.wallet_get_capacity(
            self.account2["address"]["testnet"]
        )
        print("before_balance1:", before_balance1)
        print("before_balance2:", before_balance2)
        print("after_balance1:", after_balance1)
        print("after_balance2:", after_balance2)
        # 3.检查关闭后balance被正常返还
        assert after_balance2 - before_balance2 == 62.0
        # todo check channel state
