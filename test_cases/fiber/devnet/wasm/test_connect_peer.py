from framework.basic_fiber import FiberTest
from framework.test_wasm_fiber import WasmFiber


class ConnectPeer(FiberTest):

    def test_connect_20_nodes(self):

        account_private = self.generate_account(
            1000000, self.Config.ACCOUNT_PRIVATE_1, 10000 * 100000000
        )
        WasmFiber.reset()
        wasmFiber = WasmFiber(
            account_private,
            "0201010101010101010101010101010101010101010101010101010101010101",
            "devnet",
        )

        for i in range(20):
            self.start_new_fiber(self.generate_account(10000))
        for i in range(len(self.fibers)):
            self.open_channel(
                wasmFiber, self.fibers[i], 1000 * 100000000, 1000 * 100000000
            )

        payments = []
        invoices = []
        for i in range(len(self.fibers)):
            payment_hash = self.send_invoice_payment(
                self.fibers[i], wasmFiber, 1 * 100000000, False
            )
            invoices.append(payment_hash)
            payment_hash = self.send_payment(
                wasmFiber, self.fibers[i], 1 * 100000000, False
            )
            payments.append(payment_hash)
        print("invoices", invoices)
        print("payments", payments)
        for i in range(len(payments)):
            self.wait_payment_finished(wasmFiber, payments[i], 1000)
        for i in range(len(invoices)):
            self.wait_invoice_state(wasmFiber, invoices[i])
