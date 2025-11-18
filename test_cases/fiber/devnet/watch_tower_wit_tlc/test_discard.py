import time

import pytest

from framework.basic_fiber import FiberTest


class TestDiscard(FiberTest):
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 5}

    def teardown_method(self, method):
        super().teardown_method(method)

    def teardown_class(cls):
        cls.restore_time()
        super().teardown_class()

    def test_udt(self):
        udt = self.get_account_udt_script(self.fiber1.account_private)
        self.faucet(
            self.fiber2.account_private,
            0,
            self.fiber1.account_private,
            10000 * 1000000000,
        )
        self.faucet(
            self.fiber1.account_private,
            0,
            self.fiber1.account_private,
            10000 * 1000000000,
        )
        self.fiber3 = self.start_new_fiber(self.generate_account(1000))
        before_udt_balances = self.get_fibers_balance()

        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0, udt=udt)
        self.open_channel(self.fiber2, self.fiber3, 1000 * 100000000, 0, udt=udt)

        for i in range(10):
            try:
                self.send_payment(
                    self.fiber1, self.fiber3, 1 * 100000000, False, udt=udt, try_count=0
                )
                # self.send_payment(self.fiber3, self.fiber1,  10000000, False, udt=udt, try_count=0)
            except Exception as e:
                pass
        self.fiber2.get_client().disconnect_peer({"peer_id": self.fiber3.get_peer_id()})
        self.fiber2.get_client().disconnect_peer({"peer_id": self.fiber1.get_peer_id()})
        list_channels = self.fiber2.get_client().list_channels({})
        for channel in list_channels["channels"]:
            self.fiber2.get_client().shutdown_channel(
                {"channel_id": channel["channel_id"], "force": True}
            )
        tx_hash = self.wait_and_check_tx_pool_fee(1000, False)

        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        for i in range(5):
            self.Miner.miner_with_version(self.node, "0x0")

        while len(self.get_commit_cells()) > 0:
            self.add_time_and_generate_epoch(4, 1)
            time.sleep(10)

        after_udt_balances = self.get_fibers_balance()
        result = self.get_balance_change(before_udt_balances, after_udt_balances)
        udt_change_balance = 0
        for i in range(len(result)):
            udt_change_balance += result[i]["udt"]
        assert udt_change_balance == 0
        ckb_change_balance = 0
        for i in range(len(result)):
            ckb_change_balance += result[i]["ckb"]
        assert ckb_change_balance < 20000
        assert ckb_change_balance > 0

    # @pytest.mark.skip("会遗弃tlc")
    def test_ckb(self):
        self.fiber3 = self.start_new_fiber(self.generate_account(1000))
        before_udt_balances = []
        for fiber in self.fibers:
            before_udt_balances.append(self.get_fiber_balance(fiber))

        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0)
        self.open_channel(self.fiber2, self.fiber3, 1000 * 100000000, 0)

        for i in range(10):
            try:
                self.send_payment(
                    self.fiber1, self.fiber3, 1 * 100000000, False, try_count=0
                )
                # self.send_payment(self.fiber3, self.fiber1,  10000000, False, udt=udt, try_count=0)
            except Exception as e:
                pass
        self.fiber2.get_client().disconnect_peer({"peer_id": self.fiber3.get_peer_id()})
        self.fiber2.get_client().disconnect_peer({"peer_id": self.fiber1.get_peer_id()})
        time.sleep(1)
        list_channels = self.fiber2.get_client().list_channels({})
        for channel in list_channels["channels"]:
            self.fiber2.get_client().shutdown_channel(
                {"channel_id": channel["channel_id"], "force": True}
            )
        tx_hash = self.wait_and_check_tx_pool_fee(1000, False)

        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        for i in range(5):
            self.Miner.miner_with_version(self.node, "0x0")

        while len(self.get_commit_cells()) > 0:
            self.add_time_and_generate_epoch(4, 1)
            time.sleep(10)

        after_udt_balances = []
        for fiber in self.fibers:
            after_udt_balances.append(self.get_fiber_balance(fiber))

        results = []
        for i in range(len(before_udt_balances)):
            print(
                f"ckb:{before_udt_balances[i]['chain']['ckb']} - {after_udt_balances[i]['chain']['ckb']} = {before_udt_balances[i]['chain']['ckb'] - after_udt_balances[i]['chain']['ckb']}"
            )
            results.append(
                {
                    "ckb": before_udt_balances[i]["chain"]["ckb"]
                    - after_udt_balances[i]["chain"]["ckb"]
                }
            )
        ckb_change_balance = 0
        for i in range(len(results)):
            ckb_change_balance += results[i]["ckb"]
        assert ckb_change_balance < 20000
        assert ckb_change_balance > 0
