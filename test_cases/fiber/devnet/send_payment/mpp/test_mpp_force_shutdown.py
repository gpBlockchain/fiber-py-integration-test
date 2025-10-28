# import time
#
# from framework.basic_fiber import FiberTest
#
#
# # todo
# class ForceShutdownTest(FiberTest):
#     start_fiber_config = {"fiber_watchtower_check_interval_seconds": "5"}
#
#     def test_001(self):
#         for i in range(20):
#             self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0, 0, 0)
#         for i in range(10):
#             try:
#                 self.send_invoice_payment(
#                     self.fiber1, self.fiber2, 1001 * 100000000, False, None, 0
#                 )
#             except Exception as e:
#                 pass
#         self.fiber1.get_client().disconnect_peer({"peer_id": self.fiber2.get_peer_id()})
#
#     def test_000(self):
#         self.fiber3 = self.start_new_fiber(
#             self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
#         )
#
#         self.open_channel(self.fiber1, self.fiber2, 2000 * 100000000, 0, 0, 0, None)
#         # self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0, 0, 0)
#
#         self.open_channel(self.fiber2, self.fiber3, 1000 * 100000000, 0, 0, 0)
#         self.open_channel(self.fiber2, self.fiber3, 1000 * 100000000, 0, 0, 0)
#
#         self.open_channel(
#             self.fiber1,
#             self.fiber3,
#             0,
#             1000 * 100000000,
#             0,
#             0,
#             None,
#             # {"commitment_delay_epoch": hex(24), "tlc_expiry_delta": hex(240400000)},
#         )
#         self.open_channel(
#             self.fiber1,
#             self.fiber3,
#             0,
#             1000 * 100000000,
#             0,
#             0,
#             None,
#             # {"commitment_delay_epoch": hex(24), "tlc_expiry_delta": hex(240400000)},
#         )
#
#         # self.open_channel(self.fiber3, self.fiber1, 1000 * 100000000, 0, 0, 0)
#         # self.open_channel(self.fiber3, self.fiber1, 1000 * 100000000, 0, 0, 0)
#         payment_hash = self.send_invoice_payment(
#             self.fiber1, self.fiber1, 1 * 100000000, True
#         )
#         time.sleep(0.07)
#
#     def test_00002(self):
#         self.fiber3 = self.start_new_fiber(
#             self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
#         )
#
#         self.fiber1.stop()
#         self.fiber1.start()
#         time.sleep(5)
#         payment_hash = self.send_invoice_payment(
#             self.fiber1, self.fiber1, 1 * 100000000, True
#         )
#         time.sleep(0.07)
#
#     def test_force_shutdown(self):
#         """
#         Returns:
#         """
#         self.fiber3 = self.start_new_fiber(
#             self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
#         )
#
#         self.open_channel(self.fiber1, self.fiber2, 2000 * 100000000, 0, 0, 0, None)
#         # self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0, 0, 0)
#
#         self.open_channel(self.fiber2, self.fiber3, 1000 * 100000000, 0, 0, 0)
#         self.open_channel(self.fiber2, self.fiber3, 1000 * 100000000, 0, 0, 0)
#
#         self.open_channel(
#             self.fiber1,
#             self.fiber3,
#             0,
#             1000 * 100000000,
#             0,
#             0,
#             None,
#             {"commitment_delay_epoch": hex(24)},
#         )
#         self.open_channel(
#             self.fiber1,
#             self.fiber3,
#             0,
#             1000 * 100000000,
#             0,
#             0,
#             None,
#             {"commitment_delay_epoch": hex(24)},
#         )
#
#         # self.open_channel(self.fiber3, self.fiber1, 1000 * 100000000, 0, 0, 0)
#         # self.open_channel(self.fiber3, self.fiber1, 1000 * 100000000, 0, 0, 0)
#         payment_hash = self.send_invoice_payment(
#             self.fiber1, self.fiber1, 2000 * 100000000, False
#         )
#         time.sleep(0.07)
#         # cancel the payment
#
#         self.fiber1.get_client().cancel_invoice({"payment_hash": payment_hash})
#         time.sleep(10)
#         #
#         balance = self.get_fiber_balance(self.fiber1)
#         assert balance["ckb"]["offered_tlc_balance"] != 0
#         assert balance["ckb"]["received_tlc_balance"] != 0
#         # shutdown pending tlc fiber1-fiber3
#         # shutdown pending tlc  fiber1-fiber2
#         channels = self.fiber1.get_client().list_channels({})
#         for channel in channels["channels"]:
#             if (
#                 channel["offered_tlc_balance"] != 0
#                 or channel["received_tlc_balance"] != 0
#             ):
#                 self.fiber1.get_client().shutdown_channel(
#                     {
#                         "channel_id": channel["channel_id"],
#                         "force": True,
#                     }
#                 )
#                 tx_hash = self.wait_and_check_tx_pool_fee(1000, False, 120)
#                 self.Miner.miner_until_tx_committed(self.node, tx_hash)
#
#         # generate epoch 0x6
#         self.node.getClient().generate_epochs("0x6", 0)
#         fiber1_2_tx_hash = self.wait_and_check_tx_pool_fee(1000, False, 120)
#         fiber1_2_tx_message = self.get_tx_message(fiber1_2_tx_hash)
#         # generate epoch 0x2
#         self.node.getClient().generate_epochs("0x8", 0)
#         fiber1_3_tx_hash = self.wait_and_check_tx_pool_fee(1000, False, 120)
#         fiber1_3_tx_message = self.get_tx_message(fiber1_3_tx_hash)
#         print("fiber1_2_tx_message:", fiber1_2_tx_message)
#         # todo bug
#         assert fiber1_2_tx_message["fee"] > 1 * 100000000
#         print("fiber1_3_tx_message:", fiber1_3_tx_message)
