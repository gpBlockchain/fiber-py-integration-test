# import time
#
# from framework.basic_fiber import FiberTest
#
#
# class TestWatchToerWitMpp(FiberTest):
#     debug = True
#
#     def test_watch_tower_with_pending_tlc(self):
#         self.fiber3 = self.start_new_fiber(
#             self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
#         )
#         self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0, 0, 0)
#         self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0, 0, 0)
#         self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0, 0, 0)
#         self.open_channel(self.fiber2, self.fiber3, 1500 * 100000000, 0, 0, 0)
#         self.open_channel(self.fiber2, self.fiber3, 1500 * 100000000, 0, 0, 0)
#
#         # self.open_channel(self.fiber3, self.fiber1, 1000 * 100000000, 0, 0, 0)
#         # self.open_channel(self.fiber3, self.fiber1, 1000 * 100000000, 0, 0, 0)
#         payment_hash = self.send_invoice_payment(
#             self.fiber1, self.fiber3, 3000 * 100000000, False
#         )
#         time.sleep(0.2)
#         self.fiber1.get_client().disconnect_peer({"peer_id": self.fiber2.get_peer_id()})
#         self.get_fiber_graph_balance()
#
#     def test_balance(self):
#         self.start_new_mock_fiber("")
#         self.get_fiber_graph_balance()
#         # payment_hash = self.send_invoice_payment(
#         #     self.fiber1, self.fibers[2], 3000 * 100000000, False
#         # )
#         # time.sleep(0.2)
#         # self.fiber1.get_client().disconnect_peer({
#         #     "peer_id": self.fiber2.get_peer_id()
#         # })
#         # self.get_fiber_graph_balance()
#
#     def test_addTime(self):
#         self.add_time_and_generate_block(48, 10)
#
#     def test_shutdown(self):
#         self.fiber3 = self.start_new_mock_fiber("")
#         self.fiber2.get_client().shutdown_channel(
#             {
#                 "channel_id": "0x9772978425b1de2a90bb25f13ee7b7aef356c1ee1b16dd2cab5c50ea8ec01082",
#                 # "close_script": self.get_account_script(self.Config.ACCOUNT_PRIVATE_1),
#                 # "fee_rate": "0x3FC",
#                 "force": True,
#             }
#         )
#
#     def test_timeout(self):
#         # self.add_time_and_generate_block(1,600)
#         self.node.getClient().get_transaction(
#             "0x3ad37809b57ecd6763f3ebc389ed1f6c96f0360bc1e950d39610296df0c98460"
#         )
#
#     def test_001234(self):
#         # self.node.getClient().generate_epochs("0x4")
#         msg = self.get_tx_message(
#             "0xf11a6e77c9c19df48af2b3615e724d45f7bf693a84eac9dcea63033b26d34292"
#         )
#         print(msg)
#
#     def test_fiber2(self):
#         self.fiber3 = self.start_new_mock_fiber("")
#         self.fiber3.get_client().node_info()
#         self.fiber2.get_client().list_channels({"include_closed": True})
