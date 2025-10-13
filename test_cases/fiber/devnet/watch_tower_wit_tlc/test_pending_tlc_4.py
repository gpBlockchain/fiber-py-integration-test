# from framework.basic_fiber import FiberTest
#
#
# class TestPendingTLC4(FiberTest):
#     debug = True
#     def test_04(self):
#         for i in range(4):
#             fiber = self.start_new_fiber(self.generate_account(10000))
#             fiber.connect_peer(self.fiber1)
#
#         for i in range(2):
#             self.open_channel(
#                 self.fibers[i], self.fibers[i + 1], 1000 * 100000000, 1000 * 100000000
#             )
#             self.open_channel(
#                 self.fibers[i+1], self.fibers[i], 1000 * 100000000, 1000 * 100000000
#             )
#
#         for i in range(2):
#             self.open_channel(
#                 self.fibers[i + 3],
#                 self.fibers[i + 4],
#                 1000 * 100000000,
#                 1000 * 100000000,
#             )
#             self.open_channel(
#                 self.fibers[i + 4],
#                 self.fibers[i + 3],
#                 1000 * 100000000,
#                 1000 * 100000000,
#             )
#         for i in range(3):
#             self.open_channel(
#                     self.fibers[i + 3],self.fibers[i], 1000 * 100000000, 1000 * 100000000
#             )
#             self.open_channel(
#                  self.fibers[i],self.fibers[i + 3], 1000 * 100000000, 1000 * 100000000
#             )
#         hashes = [[], [], [], [], [], []]
#         for j in range(100):
#             for i in range(len(self.fibers)):
#                 try:
#                     payment_hash = self.send_payment(
#                         self.fibers[i], self.fibers[i], 500 * 10000000, False, None, 0
#                     )
#                     hashes[i].append(payment_hash)
#                 except:
#                     pass
#
#         # for i in range(len(self.fibers)):
#         #     list_peers = self.fibers[i].get_client().list_peers()
#         #     for peer in list_peers['peers']:
#         #         self.fibers[i].get_client().disconnect_peer({
#         #             'peer_id': peer['peer_id']
#         #         })
#         self.get_fiber_graph_balance()
#         # for i in range(len(self.fibers)):
#         #     channels = self.fibers[i].get_client().list_channels({})
#         #     for channel in channels["channels"]:
#         #         self.fiber2.get_client().shutdown_channel(
#         #             {
#         #                 "channel_id": channel["channel_id"],
#         #                 "close_script": self.get_account_script(
#         #                     self.fiber2.account_private
#         #                 ),
#         #                 "fee_rate": "0x3FC",
#         #             }
#         #         )
#         #         shutdown_tx = self.wait_and_check_tx_pool_fee(1000, False, 200)
#         #         self.Miner.miner_until_tx_committed(self.node, shutdown_tx)
#
#     def test_1(self):
#         for i in range(4):
#             fiber = self.start_new_fiber("")
#         self.get_fiber_graph_balance()
#         self.get_fibers_balance_message()
