# import time
#
# from framework.basic_fiber import FiberTest
#
#
# class TestPendingTLC2(FiberTest):
#     # debug = True
#     start_fiber_config = {"fiber_watchtower_check_interval_seconds": 5}
#
#     def teardown_method(self, method):
#         self.restore_time()
#         super().teardown_method(method)
#
#     def test_pending_tlc_2(self):
#         """
#         a->b->c
#         Returns:
#
#         """
#         self.fiber3 = self.start_new_fiber(self.generate_account(10000))
#         fibers_balance = []
#         for i in range(len(self.fibers)):
#             balance = self.get_fiber_balance(self.fibers[i])
#             fibers_balance.append(balance)
#
#         self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1)
#         self.open_channel(self.fiber2, self.fiber3, 1000 * 100000000, 1)
#         for i in range(10):
#             self.send_payment(self.fiber1, self.fiber3, 1 * 100000000, False)
#
#         self.fiber1.get_client().disconnect_peer({"peer_id": self.fiber2.get_peer_id()})
#         # time.sleep(10)
#
#         # 确认fiber1 和fiber2 状态一致
#         fiber1_channels = self.fiber1.get_client().list_channels(
#             {"peer_id": self.fiber2.get_peer_id()}
#         )
#         fiber2_channels = self.fiber2.get_client().list_channels(
#             {"peer_id": self.fiber1.get_peer_id()}
#         )
#         fiber3_channels = self.fiber3.get_client().list_channels({})
#         # assert fiber1_channels['channels'][0]['local_balance'] == fiber2_channels['channels'][0]['remote_balance']
#         tlc_size = int(
#             int(fiber1_channels["channels"][0]["offered_tlc_balance"], 16) / 100100000
#         )
#         fiber3_pre_image_size = int(
#             int(fiber3_channels["channels"][0]["local_balance"], 16) / 100000000
#         )
#         # 时间加速48 h 直到 fiber1 自动发送强制shutdown
#         self.add_time_and_generate_block(48, 5)
#         # 生成 等待强制shutdown 交易上链
#         shutdown_tx = self.wait_and_check_tx_pool_fee(1000, False, 200)
#         self.Miner.miner_until_tx_committed(self.node, shutdown_tx)
#         for i in range(len(fibers_balance)):
#             print(fibers_balance[i])
#         while len(self.get_commit_cells()) > 0:
#             # cells = self.get_commit_cells()
#             self.add_time_and_generate_block(1, 600)
#             time.sleep(20)
#
#         channels = self.fiber2.get_client().list_channels({})
#         for channel in channels["channels"]:
#             try:
#                 self.fiber2.get_client().shutdown_channel(
#                     {
#                         "channel_id": channel["channel_id"],
#                         "close_script": self.get_account_script(
#                             self.fiber2.account_private
#                         ),
#                         "fee_rate": "0x3FC",
#                     }
#                 )
#                 shutdown_tx = self.wait_and_check_tx_pool_fee(1000, False, 200)
#                 self.Miner.miner_until_tx_committed(self.node, shutdown_tx)
#             except Exception as e:
#                 pass
#         after_fibers_balance = []
#         for i in range(len(self.fibers)):
#             balance = self.get_fiber_balance(self.fibers[i])
#             after_fibers_balance.append(balance)
#         print("---before-----")
#         for i in range(len(fibers_balance)):
#             print(fibers_balance[i])
#         print("-----after-----")
#         for i in range(len(after_fibers_balance)):
#             print(after_fibers_balance[i])
#         for i in range(len(after_fibers_balance)):
#             print(
#                 f"fiber:{i}: before:{fibers_balance[i]['chain']['ckb']} after:{after_fibers_balance[i]['chain']['ckb']},result:{after_fibers_balance[i]['chain']['ckb'] - fibers_balance[i]['chain']['ckb']}"
#             )
