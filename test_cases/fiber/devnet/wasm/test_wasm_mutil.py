from framework.basic_fiber import FiberTest
from framework.test_wasm_fiber import WasmFiber


class MutilWasm(FiberTest):
    def test_mutil(self):
        account_private = self.generate_account(
            10000, self.Config.ACCOUNT_PRIVATE_1, 10000 * 100000000
        )
        WasmFiber.reset()
        wasmFiber1 = WasmFiber(
            account_private,
            "0201010101010101010101010101010101010101010101010101010101010101",
            "devnet",
            databasePrefix="wasm1",
        )
        account_private2 = self.generate_account(
            10000, self.Config.ACCOUNT_PRIVATE_1, 10000 * 100000000
        )
        wasmFiber2 = WasmFiber(
            account_private2,
            "2201010101010101010101010101010101010101010101010101010101010101",
            "devnet",
            databasePrefix="wasm2",
        )
        self.open_channel(wasmFiber1, self.fiber1, 1000 * 100000000, 1000 * 100000000)
        self.open_channel(wasmFiber2, self.fiber1, 1000 * 100000000, 1000 * 100000000)
        self.open_channel(wasmFiber2, self.fiber2, 1000 * 100000000, 1000 * 100000000)
        self.open_channel(wasmFiber1, self.fiber2, 1000 * 100000000, 1000 * 100000000)
        for i in range(20):
            self.send_invoice_payment(wasmFiber1, wasmFiber1, 1 * 100000000)
            self.send_invoice_payment(wasmFiber2, wasmFiber2, 1 * 100000000)
