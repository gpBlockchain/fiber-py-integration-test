import time

from framework.basic_fiber import FiberTest
from framework.test_wasm_fiber import WasmFiber


class WasmDemo(FiberTest):

    def test_ckb_udt_demo(self):
        account_private = self.generate_account(
            10000, self.Config.ACCOUNT_PRIVATE_1, 10000 * 100000000
        )
        WasmFiber.reset()
        wasmFiber = WasmFiber(
            account_private,
            "0201010101010101010101010101010101010101010101010101010101010101",
            "devnet",
        )
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1000 * 100000000)
        self.open_channel(wasmFiber, self.fiber1, 1000 * 100000000, 1000 * 100000000)
        self.send_payment(wasmFiber, self.fiber2, 1 * 100000000)
        self.send_payment(self.fiber2, wasmFiber, 1 * 100000000)

        self.faucet(
            self.fiber1.account_private,
            10000,
            self.fiber1.account_private,
            10000 * 100000000,
        )
        self.open_channel(
            self.fiber1,
            self.fiber2,
            1000 * 100000000,
            1000 * 100000000,
            udt=self.get_account_udt_script(self.fiber1.account_private),
        )
        self.open_channel(
            wasmFiber,
            self.fiber1,
            1000 * 100000000,
            1000 * 100000000,
            udt=self.get_account_udt_script(self.fiber1.account_private),
        )
        self.send_payment(
            wasmFiber,
            self.fiber2,
            1 * 100000000,
            True,
            self.get_account_udt_script(self.fiber1.account_private),
        )
        self.send_payment(
            self.fiber2,
            wasmFiber,
            1 * 100000000,
            True,
            self.get_account_udt_script(self.fiber1.account_private),
        )

        wasmFiber.get_client().shutdown_channel(
            {
                "channel_id": wasmFiber.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "close_script": self.get_account_script(account_private),
                "fee_rate": "0x3FC",
            }
        )
        self.wait_for_channel_state(
            wasmFiber.get_client(),
            self.fiber1.get_peer_id(),
            "CLOSED",
            include_closed=True,
        )
        shutdown_channel_id = wasmFiber.get_client().list_channels({})["channels"][0][
            "channel_id"
        ]
        wasmFiber.get_client().shutdown_channel(
            {
                "channel_id": shutdown_channel_id,
                "close_script": self.get_account_script(account_private),
                "fee_rate": "0x3FC",
            }
        )
        time.sleep(1)
        self.wait_for_channel_state(
            wasmFiber.get_client(),
            self.fiber1.get_peer_id(),
            "CLOSED",
            include_closed=True,
            channel_id=shutdown_channel_id,
        )
        assert len(wasmFiber.get_client().list_channels({})["channels"]) == 0
        node_info0 = wasmFiber.get_client().node_info()

        wasmFiber.refresh()
        node_info1 = wasmFiber.get_client().node_info()
        node_info2 = wasmFiber.get_client().node_info()
        assert node_info1["node_id"] == node_info2["node_id"]
        assert node_info0["node_id"] == node_info1["node_id"]
