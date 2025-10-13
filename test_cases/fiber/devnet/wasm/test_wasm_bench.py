import pytest

from framework.basic_fiber import FiberTest
from framework.test_wasm_fiber import WasmFiber


class TestWasmBench(FiberTest):

    # @pytest.mark.skip("todo")
    def test_ckb_bench(self):
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
        wasmPaymentHashes = []
        fiber2PaymentHashes = []
        for i in range(50):
            try:
                paymentHash = self.send_payment(
                    wasmFiber,
                    self.fiber2,
                    1 * 100000000,
                    False,
                    self.get_account_udt_script(self.fiber1.account_private),
                    try_count=0,
                )
                wasmPaymentHashes.append(paymentHash)
                paymentHash = self.send_payment(
                    self.fiber2,
                    wasmFiber,
                    1 * 100000000,
                    False,
                    self.get_account_udt_script(self.fiber1.account_private),
                    try_count=0,
                )
                fiber2PaymentHashes.append(paymentHash)
            except Exception as e:
                pass

        for i in range(len(wasmPaymentHashes)):
            self.wait_payment_finished(wasmFiber, wasmPaymentHashes[i])
        for i in range(len(fiber2PaymentHashes)):
            self.wait_payment_finished(self.fiber2, fiber2PaymentHashes[i])

        # wasmFiber.get_client().shutdown_channel({
        #     "channel_id": wasmFiber.get_client().list_channels({})["channels"][0][
        #         "channel_id"
        #     ],
        #     "close_script": self.get_account_script(account_private),
        #     "fee_rate": "0x3FC",
        # })
        # self.wait_for_channel_state(wasmFiber.get_client(), self.fiber1.get_peer_id(), "CLOSED", include_closed=True)
        # wasmFiber.get_client().shutdown_channel({
        #     "channel_id": wasmFiber.get_client().list_channels({})["channels"][0][
        #         "channel_id"
        #     ],
        #     "close_script": self.get_account_script(account_private),
        #     "fee_rate": "0x3FC",
        # })
        # self.wait_for_channel_state(wasmFiber.get_client(), self.fiber1.get_peer_id(), "CLOSED", include_closed=True)
        # assert len(wasmFiber.get_client().list_channels({})["channels"]) == 0
        # node_info0 = wasmFiber.get_client().node_info()
        #
        # wasmFiber.refresh()
        # node_info1 = wasmFiber.get_client().node_info()
        # wasmFiber.stop()
        # wasmFiber.start()
        # node_info2 = wasmFiber.get_client().node_info()
        # assert node_info1 == node_info2
        # assert node_info0 == node_info1
