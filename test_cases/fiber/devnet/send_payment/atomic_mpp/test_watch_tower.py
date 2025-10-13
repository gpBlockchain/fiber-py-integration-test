import time

from framework.basic_fiber import FiberTest


class TestWatchTower(FiberTest):
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 5}
    debug = True
    # def teardown_method(self, method):
    #     self.restore_time()
    #     super().teardown_method(method)

    def test_get_tx(self):
        tx1 = self.node.getClient().get_transaction("0x0534591ecdf9760fe6045e3e96352ff461b513ede53c394056fa93359b0c61ff")
        print("block_number:",int(tx1['tx_status']['block_number'],16))
        # block_number: 2640
        # 2/3 epoch
        # block_number: 3881


    def test_watch_tower(self):
        """
        a-b
        """
        before_balance = []
        for fiber in self.fibers:
            balance = self.get_fiber_balance(fiber)
            before_balance.append(balance)
        for i in range(10):
            self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0, 0, 0)


        for i in range(5):
            self.send_invoice_payment(self.fiber1, self.fiber2, 1001 * 100000000,
                                      False, None, 0
                                      # other_options={"allow_atomic_mpp": True}
                                      )

        self.fiber1.get_client().disconnect_peer({"peer_id": self.fiber2.get_peer_id()})

        self.add_time_and_generate_block(23, 20)
        while len(self.get_commit_cells()) == 0:
            self.add_time_and_generate_block(1, 20)
            time.sleep(15)
        while len(self.get_commit_cells()) > 0:
            # cells = self.get_commit_cells()
            self.add_time_and_generate_block(1, 600)
            time.sleep(20)
        self.fiber1.connect_peer(self.fiber2)
        time.sleep(1)
        channels = self.fiber1.get_client().list_channels({})
        for channel in channels["channels"]:
            try:
                self.fiber1.get_client().shutdown_channel(
                    {
                        "channel_id": channel["channel_id"],
                        "close_script": self.get_account_script(
                            self.fiber1.account_private
                        ),
                        "fee_rate": "0x3FC",
                    }
                )
                shutdown_tx = self.wait_and_check_tx_pool_fee(1000, False, 200)
                self.Miner.miner_until_tx_committed(self.node, shutdown_tx)
            except Exception as e:
                pass
        after_fibers_balance = []
        for i in range(len(self.fibers)):
            balance = self.get_fiber_balance(self.fibers[i])
            after_fibers_balance.append(balance)
        print("---before-----")
        for i in range(len(before_balance)):
            print(before_balance[i])
        print("-----after-----")
        for i in range(len(after_fibers_balance)):
            print(after_fibers_balance[i])
        for i in range(len(after_fibers_balance)):
            print(
                f"fiber:{i}: before:{before_balance[i]['chain']['ckb']} after:{after_fibers_balance[i]['chain']['ckb']},result:{after_fibers_balance[i]['chain']['ckb'] - before_balance[i]['chain']['ckb']}"
            )

    def test_009(self):
        self.fiber1.connect_peer(self.fiber2)
        time.sleep(1)
        channels = self.fiber1.get_client().list_channels({})
        for channel in channels["channels"]:
            try:
                self.fiber1.get_client().shutdown_channel(
                    {
                        "channel_id": channel["channel_id"],
                        "close_script": self.get_account_script(
                            self.fiber1.account_private
                        ),
                        "fee_rate": "0x3FC",
                    }
                )
                shutdown_tx = self.wait_and_check_tx_pool_fee(1000, False, 200)
                self.Miner.miner_until_tx_committed(self.node, shutdown_tx)
            except Exception as e:
                pass