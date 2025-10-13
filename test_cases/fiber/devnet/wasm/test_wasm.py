# import time
#
# from framework.basic_fiber import FiberTest
# from framework.test_wasm_fiber import WasmFiber
#
#
# class TestWasm(FiberTest):
#     debug = True
#
#     def test_two_wasm(self):
#         self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1000 * 100000000)
#
#     def test_0001234(self):
#         self.fiber1.get_client().node_info()
#         self.fiber2.get_client().node_info()
#
#     def test_facue1(self):
#         # self.faucet("2db2219027d6c2a206856fed962214cfa47ab1b3da322ce051877c6567bac2c8", 10000)
#         self.faucet("2db2219027d6c2a206856fed962214cfa47ab1b3da322ce051877c6567bac2c1", 10000)
#
#
#     def test_wasm(self):
#         """
#         Test the wasm module.
#         """
#         wasmFiber = WasmFiber("2db2219027d6c2a206856fed962214cfa47ab1b3da322ce051877c6567bac2c8",
#                               "0201010101010101010101010101010101010101010101010101010101010101", "devnet")
#         self.fiber1.get_client().node_info()
#         wasmFiber.connect_peer(self.fiber2)
#
#     def test_facue(self):
#         self.faucet("2db2219027d6c2a206856fed962214cfa47ab1b3da322ce051877c6567bac2c8", 10000,
#                     self.Config.ACCOUNT_PRIVATE_1, 1000 * 100000000)
#
#     def test_open_channel(self):
#         """
#         Open a channel with the wasm fiber.
#         """
#         wasmFiber = WasmFiber("2db2219027d6c2a206856fed962214cfa47ab1b3da322ce051877c6567bac2c8",
#                               "0201010101010101010101010101010101010101010101010101010101010101", "devnet")
#         self.open_channel(wasmFiber, self.fiber2, 1000 * 100000000, 1000 * 100000000)
#         self.open_channel(wasmFiber, self.fiber1, 1000 * 100000000, 1000 * 100000000)
#
#         self.fiber1.get_client().node_info()
#         self.fiber2.get_client().node_info()
#
#     def test_balala(self):
#         wasmFiber = WasmFiber("2db2219027d6c2a206856fed962214cfa47ab1b3da322ce051877c6567bac2c8",
#                               "0201010101010101010101010101010101010101010101010101010101010101", "devnet", True)
#         channel_id = self.fiber1.get_client().list_channels({})["channels"][0][
#             "channel_id"
#         ]
#         for i in range(10000):
#             self.fiber1.get_client().update_channel(
#                 {
#                     "channel_id": channel_id,
#                     "tlc_fee_proportional_millionths": hex(20000 + i),
#                 }
#             )
#             wasmFiber.get_client().update_channel({
#                 "channel_id": channel_id,
#                 "tlc_fee_proportional_millionths": hex(2000 + i),
#             })
#             time.sleep(0.01)
#
#     def test_000(self):
#         self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1000 * 100000000)
#
#     def test_send_payment(self):
#         wasmFiber = WasmFiber("2db2219027d6c2a206856fed962214cfa47ab1b3da322ce051877c6567bac2c8",
#                               "0201010101010101010101010101010101010101010101010101010101010101", "devnet")
#         for i in range(100):
#             self.send_payment(wasmFiber, self.fiber2, 1 * 100000000, False)
#
#     def test_false(self):
#         wasmFiber = WasmFiber("2db2219027d6c2a206856fed962214cfa47ab1b3da322ce051877c6567bac2c8",
#                               "0201010101010101010101010101010101010101010101010101010101010101",
#                               "devnet",
#                               True)
#         for i in range(10000):
#             self.send_payment(wasmFiber, wasmFiber, 1 * 100000000, True)
#             self.send_payment(self.fiber1, self.fiber1, 1 * 100000000, True)
#             self.send_payment(self.fiber2, self.fiber2, 1 * 100000000, True)
#
#     def test_00(self):
#         # self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0)
#         # self.send_payment(self.fiber1, self.fiber2, 100 * 100000000, True)
#         # self.fiber1.start()
#         wasmFiber = WasmFiber("2db2219027d6c2a206856fed962214cfa47ab1b3da322ce051877c6567bac2c8",
#                               "0201010101010101010101010101010101010101010101010101010101010101", "devnet", True)
#         # for i in range(100):
#         #     self.send_payment(wasmFiber,self.fiber2,88,False)
#         wasmFiber.get_client().node_info()
#         # self.fiber1.stop()
