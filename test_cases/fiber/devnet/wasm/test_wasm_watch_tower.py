import time

import pytest

from framework.basic_fiber import FiberTest
from framework.test_wasm_fiber import WasmFiber


class TestWasmWatchTower(FiberTest):
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 5}

    def test_wasm_watch_tower1(self):
        """
        Test the watch tower functionality in the devnet environment.
        """
        # This test is a placeholder and should be implemented with actual logic.
        account_private = self.generate_account(
            10000, self.Config.ACCOUNT_PRIVATE_1, 10000 * 100000000
        )
        WasmFiber.reset()
        wasmFiber = WasmFiber(
            account_private,
            "0201010101010101010101010101010101010101010101010101010101010101",
            "devnet-watch-tower",
            # "devnet"
        )
        for i in range(2):
            self.open_channel(
                wasmFiber, self.fiber1, 1000 * 100000000, 1000 * 100000000
            )
            for i in range(5):
                self.send_payment(wasmFiber, self.fiber1, 100000)
            for i in range(5):
                self.send_payment(self.fiber1, wasmFiber, 100000)

            self.fiber1.stop()
            wasmFiber.get_client().shutdown_channel(
                {
                    "channel_id": wasmFiber.get_client().list_channels({})["channels"][
                        0
                    ]["channel_id"],
                    "force": True,
                }
            )
            time.sleep(1)
            shutdown_tx = self.wait_and_check_tx_pool_fee(1000, False, 5 * 120)
            self.Miner.miner_until_tx_committed(self.node, shutdown_tx)
            self.node.getClient().generate_epochs("0x1", 0)
            tx_hash = self.wait_and_check_tx_pool_fee(1000, False, 600)
            message = self.get_tx_message(tx_hash)
            print("message:", message)
            # message: {'input_cells':
            # 'output_cells': [{'args': '0xc8328aabcd9b9e8e64fbc566c4385c3bdeb219d7', 'capacity': 106199999545}, {'args': '0x75178f34549c5fe9cd1a0c57aebd01e7ddf9249e', 'capacity': 106199999545}, {'args': '0x470dcdc5e44064909650113a274b3b36aecb6dc7', 'capacity': 519873503699999188}], 'fee': 1267}
            wasmFiberArgs = wasmFiber.get_client().node_info()[
                "default_funding_lock_script"
            ]["args"]
            assert (
                message["output_cells"][0]["args"] == wasmFiberArgs
                or message["output_cells"][1]["args"]
            )
            if message["output_cells"][0]["args"] == wasmFiberArgs:
                assert (
                    1062 * 100000000 - message["output_cells"][0]["capacity"]
                    < 100000000
                    and 1062 * 100000000 - message["output_cells"][0]["capacity"] > 0
                )
            else:
                assert (
                    1062 * 100000000 - message["output_cells"][1]["capacity"]
                    < 100000000
                    and 1062 * 100000000 - message["output_cells"][1]["capacity"] > 0
                )
            self.fiber1.start()

    @pytest.mark.skip("Musig2RoundFinalizeError")
    def test_watch_tower_restart(self):
        account_private = self.generate_account(
            10000, self.Config.ACCOUNT_PRIVATE_1, 10000 * 100000000
        )
        WasmFiber.reset()
        wasmFiber = WasmFiber(
            account_private,
            "0201010101010101010101010101010101010101010101010101010101010101",
            "devnet-watch-tower",
        )

        self.open_channel(wasmFiber, self.fiber1, 1000 * 100000000, 1000 * 100000000)
        wasm_node_id = wasmFiber.get_client().node_info()["node_id"]
        before_fiber1_balance = self.get_fiber_balance(self.fiber1)
        self.fiber2.stop()
        payment = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": wasm_node_id,
                "amount": hex(1 * 100000000),
                "keysend": True,
                "allow_self_payment": True,
            }
        )
        time.sleep(5)
        self.wait_payment_state(self.fiber1, payment["payment_hash"], "Inflight")
        self.fiber2.start()
        wasmFiber.refresh()
        self.wait_payment_state(self.fiber1, payment["payment_hash"], "Success")
        after_fiber1_balance = self.get_fiber_balance(self.fiber1)
        print("before_fiber1_balance:", before_fiber1_balance)
        print("after_fiber1_balance:", after_fiber1_balance)
        self.fiber1.stop()
        wasmFiber.get_client().shutdown_channel(
            {
                "channel_id": wasmFiber.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "force": True,
            }
        )
        shutdown_tx = self.wait_and_check_tx_pool_fee(1000, False, 5 * 120)
        self.Miner.miner_until_tx_committed(self.node, shutdown_tx)
        self.node.getClient().generate_epochs("0x1", 0)
        tx_hash = self.wait_and_check_tx_pool_fee(1000, False, 600)
        message = self.get_tx_message(tx_hash)
        print("message:", message)
        wasmFiberArgs = wasmFiber.get_client().node_info()[
            "default_funding_lock_script"
        ]["args"]
        assert (
            message["output_cells"][0]["args"] == wasmFiberArgs
            or message["output_cells"][1]["args"]
        )
        if message["output_cells"][0]["args"] == wasmFiberArgs:
            assert (
                1063 * 100000000 - message["output_cells"][0]["capacity"] < 100000000
                and 1063 * 100000000 - message["output_cells"][0]["capacity"] > 0
            )
        else:
            assert (
                1063 * 100000000 - message["output_cells"][1]["capacity"] < 100000000
                and 1063 * 100000000 - message["output_cells"][1]["capacity"] > 0
            )
