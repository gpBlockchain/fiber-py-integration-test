# import time
#
# from framework.basic_fiber import FiberTest
#
#
import datetime
import time

from framework.basic_fiber import FiberTest


class TestWatchToerWitMpp(FiberTest):
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 5}

    def teardown_class(cls):
        cls.restore_time()
        super().teardown_class()

    def test_watch_tower_with_bench_pending_tlc(self):
        self.fiber3 = self.start_new_fiber(
            self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
        )
        before_balance = self.get_fibers_balance()

        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber2, self.fiber3, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber2, self.fiber3, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber3, self.fiber1, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber3, self.fiber1, 1000 * 100000000, 0, 0, 0)

        self.open_channel(self.fiber3, self.fiber1, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber3, self.fiber1, 1000 * 100000000, 0, 0, 0)
        for i in range(60):
            for fiber in self.fibers:
                try:
                    payment_hash = self.send_invoice_payment(
                        fiber, fiber, 1001 * 100000000, False, None, 0
                    )
                except Exception as e:
                    pass
        self.fiber1.get_client().disconnect_peer({"peer_id": self.fiber2.get_peer_id()})

        for fiber in self.fibers:
            try:
                for channel in fiber.get_client().list_channels({})["channels"]:
                    fiber.get_client().shutdown_channel(
                        {"channel_id": channel["channel_id"], "force": True}
                    )
            except Exception as e:
                pass
        time.sleep(20)
        # for i in range(600):
        #     self.Miner.miner_with_version(self.node, "0x0")
        self.node.getClient().generate_epochs("0x1")
        time.sleep(10)
        while (
            self.node.getClient().get_tip_block_number()
            - self.get_latest_commit_tx_number()
            < 50
        ):
            time.sleep(20)
        self.add_time_and_generate_epoch(25, 2)
        while len(self.get_commit_cells()) > 0:
            self.add_time_and_generate_epoch(24, 1)
            time.sleep(10)

        after_balance = self.get_fibers_balance()
        result = self.get_balance_change(before_balance, after_balance)
        for rt in result:
            assert rt["ckb"] < 100000000

