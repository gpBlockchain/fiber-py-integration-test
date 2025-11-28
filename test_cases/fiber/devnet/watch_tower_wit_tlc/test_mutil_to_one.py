import time

from framework.basic_fiber import FiberTest
from framework.util import ckb_hash


class TestMutilToOne(FiberTest):
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 3}

    def teardown_class(cls):
        cls.restore_time()
        super().teardown_class()

    # def test_002(self):
    #     channels = self.fiber1.get_client().list_channels({"include_closed": True})
    #     fiber2_channels = self.fiber2.get_client().list_channels({"include_closed": True})
    #
    #     for channel in channels['channels']:
    #         for tlc in channel['pending_tlcs']:
    #             print(f"hash:{tlc['payment_hash']}, tlc type:{tlc['status']},expiry time:{hex_timestamp_to_datetime(tlc['expiry'])}")
    #     print("---fiber2-----")
    #     for channel in fiber2_channels['channels']:
    #         for tlc in channel['pending_tlcs']:
    #             print(f"hash:{tlc['payment_hash']}, tlc type:{tlc['status']},expiry time:{hex_timestamp_to_datetime(tlc['expiry'])}")

    def test_mutil_to_one(self):
        """
        aN->b->c
        Returns:
        """
        for i in range(10):
            self.start_new_fiber(self.generate_account(10000))

        before_balance = self.get_fibers_balance()

        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0)
        for i in range(len(self.new_fibers)):
            self.open_channel(self.new_fibers[i], self.fiber1, 1000 * 100000000, 0)
        for i in range(10):
            for j in range(len(self.new_fibers)):
                self.send_invoice_payment(
                    self.new_fibers[i], self.fiber2, 1 * 100000000, False
                )
        self.fiber1.get_client().disconnect_peer({"peer_id": self.fiber2.get_peer_id()})
        for channel in self.fiber1.get_client().list_channels({})["channels"]:
            try:
                self.fiber1.get_client().shutdown_channel(
                    {"channel_id": channel["channel_id"], "force": True}
                )
            except Exception as e:
                pass
        time.sleep(10)
        self.add_time_and_generate_block(1, 600)
        time.sleep(10)
        while (
            self.node.getClient().get_tip_block_number()
            - self.get_latest_commit_tx_number()
            < 20
        ):
            time.sleep(5)
        while len(self.get_commit_cells()) > 0:
            self.add_time_and_generate_block(24 * 3, 600)
            time.sleep(10)
        after_fibers_balance = []
        for i in range(len(self.fibers)):
            balance = self.get_fiber_balance(self.fibers[i])
            after_fibers_balance.append(balance)

        after_balance = self.get_fibers_balance()
        result = self.get_balance_change(before_balance, after_balance)
        assert result[0]["ckb"] < 1 * 100000000
        ckb_fee = 0
        for rt in result:
            ckb_fee += rt["ckb"]
        assert ckb_fee < 1 * 100000000

    def test_mutil_to_one_udt(self):
        """
        aN->b->c
        Returns:
        """
        for i in range(10):
            self.start_new_fiber(
                self.generate_account(
                    10000, self.fiber1.account_private, 10000 * 100000000
                )
            )
        self.faucet(
            self.fiber1.account_private,
            0,
            self.fiber1.account_private,
            10000 * 100000000,
        )
        fibers_balance = []
        for i in range(len(self.fibers)):
            balance = self.get_fiber_balance(self.fibers[i])
            fibers_balance.append(balance)

        self.open_channel(
            self.fiber1,
            self.fiber2,
            1000 * 100000000,
            0,
            udt=self.get_account_udt_script(self.fiber1.account_private),
        )
        for i in range(len(self.new_fibers)):
            self.open_channel(
                self.new_fibers[i],
                self.fiber1,
                1000 * 100000000,
                0,
                udt=self.get_account_udt_script(self.fiber1.account_private),
            )
        udt = self.get_account_udt_script(self.fiber1.account_private)
        for i in range(20):
            for j in range(len(self.new_fibers)):
                # self.send_invoice_payment(self.new_fibers[i], self.fiber2, 1 * 100000000, False,udt=self.get_account_udt_script(self.fiber1.account_private))
                self.send_payment(
                    self.new_fibers[j],
                    self.fiber2,
                    1 * 100000000,
                    False,
                    udt=udt,
                    try_count=0,
                )

        self.fiber1.get_client().disconnect_peer({"peer_id": self.fiber2.get_peer_id()})
        for channel in self.fiber1.get_client().list_channels({})["channels"]:
            try:
                self.fiber1.get_client().shutdown_channel(
                    {"channel_id": channel["channel_id"], "force": True}
                )
            except Exception as e:
                pass
        time.sleep(10)
        self.add_time_and_generate_block(1, 600)
        time.sleep(10)
        while (
            self.node.getClient().get_tip_block_number()
            - self.get_latest_commit_tx_number()
            < 20
        ):
            time.sleep(5)
        while len(self.get_commit_cells()) > 0:
            self.add_time_and_generate_block(24, 600)
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

        discard_ckb_balance = 0
        for i in range(len(after_fibers_balance)):
            print(
                f"fiber:{i}: before:{fibers_balance[i]['chain']['udt']} after:{after_fibers_balance[i]['chain']['udt']},result:{after_fibers_balance[i]['chain']['udt'] - fibers_balance[i]['chain']['udt']}"
            )
            discard_ckb_balance = discard_ckb_balance + (
                fibers_balance[i]["chain"]["udt"]
                - after_fibers_balance[i]["chain"]["udt"]
            )
        print("discard_ckb_balance:", discard_ckb_balance)
        assert discard_ckb_balance == 0
        assert (
            fibers_balance[0]["chain"]["udt"] - after_fibers_balance[0]["chain"]["udt"]
            < 1 * 100000000
        )
