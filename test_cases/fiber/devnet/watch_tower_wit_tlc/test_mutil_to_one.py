import time

from framework.basic_fiber import FiberTest


class TestMutilToOne(FiberTest):
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 5}

    def teardown_method(self, method):

        self.restore_time()
        super().teardown_method(method)

    def test_mutil_to_one(self):
        """
        aN->b->c
        Returns:
        """
        for i in range(10):
            self.start_new_fiber(self.generate_account(10000))
        fibers_balance = []
        for i in range(len(self.fibers)):
            balance = self.get_fiber_balance(self.fibers[i])
            fibers_balance.append(balance)

        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0)
        for i in range(len(self.new_fibers)):
            self.open_channel(self.new_fibers[i], self.fiber1, 1000 * 100000000, 0)
        for i in range(10):
            for j in range(len(self.new_fibers)):
                self.send_payment(self.new_fibers[i], self.fiber2, 1 * 100000000, False)
        self.fiber1.get_client().disconnect_peer({"peer_id": self.fiber2.get_peer_id()})
        self.add_time_and_generate_block(23, 20)
        while len(self.get_commit_cells()) == 0:
            self.add_time_and_generate_block(1, 20)
            time.sleep(15)
        while len(self.get_commit_cells()) > 0:
            # cells = self.get_commit_cells()
            self.add_time_and_generate_block(1, 600)
            time.sleep(20)
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

        time.sleep(10)
        after_fibers_balance = []
        for i in range(len(self.fibers)):
            balance = self.get_fiber_balance(self.fibers[i])
            after_fibers_balance.append(balance)
        print("---before-----")
        for i in range(len(fibers_balance)):
            print(fibers_balance[i])
        print("-----after-----")
        for i in range(len(after_fibers_balance)):
            print(after_fibers_balance[i])
        for i in range(len(after_fibers_balance)):
            print(
                f"fiber:{i}: before:{fibers_balance[i]['chain']['ckb']} after:{after_fibers_balance[i]['chain']['ckb']},result:{after_fibers_balance[i]['chain']['ckb'] - fibers_balance[i]['chain']['ckb']}"
            )

        # self.add_time_and_generate_block(24 - 4, 100)
