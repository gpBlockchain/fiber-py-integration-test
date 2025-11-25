import time

from framework.basic_fiber import FiberTest


class TestHashAlgorithm(FiberTest):
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 5}

    def test_hash_algorithm(self):
        before_udt_balances = []
        for fiber in self.fibers:
            before_udt_balances.append(self.get_fiber_balance(fiber))

        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0)
        self.send_invoice_payment(
            self.fiber1,
            self.fiber2,
            1 * 100000000,
            other_options={"hash_algorithm": "ckb_hash"},
        )
        self.fiber1.get_client().shutdown_channel(
            {
                "channel_id": self.fiber1.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "force": True,
            }
        )
        tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        self.node.getClient().generate_epochs("0x1", wait_time=0)
        tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
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
        assert abs(results[0]["ckb"] - 100000000) < 2000
        assert abs(results[1]["ckb"] + 100000000) < 2000

    def test_hash_algorithm_udt(self):
        self.faucet(self.fiber1.account_private, 0, self.fiber1.account_private)
        before_udt_balances = []
        for fiber in self.fibers:
            before_udt_balances.append(self.get_fiber_balance(fiber))

        self.open_channel(
            self.fiber1,
            self.fiber2,
            1000 * 100000000,
            0,
            udt=self.get_account_udt_script(self.fiber1.account_private),
        )
        time.sleep(1)
        self.send_invoice_payment(
            self.fiber1,
            self.fiber2,
            1,
            udt=self.get_account_udt_script(self.fiber1.account_private),
            other_options={"hash_algorithm": "ckb_hash"},
        )
        self.fiber1.get_client().shutdown_channel(
            {
                "channel_id": self.fiber1.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "force": True,
            }
        )
        tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        self.node.getClient().generate_epochs("0x1", wait_time=0)
        tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        after_udt_balances = []
        for fiber in self.fibers:
            after_udt_balances.append(self.get_fiber_balance(fiber))

        results = []
        for i in range(len(before_udt_balances)):
            print(
                f"ckb:{before_udt_balances[i]['chain']['ckb']} - {after_udt_balances[i]['chain']['ckb']} = {before_udt_balances[i]['chain']['ckb'] - after_udt_balances[i]['chain']['ckb']}"
            )
            print(
                f"udt:{before_udt_balances[i]['chain']['udt']} - {after_udt_balances[i]['chain']['udt']} = {before_udt_balances[i]['chain']['udt'] - after_udt_balances[i]['chain']['udt']}"
            )
            results.append(
                {
                    "ckb": before_udt_balances[i]["chain"]["ckb"]
                    - after_udt_balances[i]["chain"]["ckb"],
                    "udt": before_udt_balances[i]["chain"]["udt"]
                    - after_udt_balances[i]["chain"]["udt"],
                }
            )
        assert results[0]["udt"] == 1
        assert results[1]["udt"] == -1
