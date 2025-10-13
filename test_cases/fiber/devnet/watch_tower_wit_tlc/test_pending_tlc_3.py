# # import time
# #
# # from framework.basic_fiber import FiberTest
# #
# #
# from framework.basic_fiber import FiberTest
#
#
# class TestPendingTLC3(FiberTest):
#     debug = True
#     start_fiber_config = {"fiber_watchtower_check_interval_seconds": 5}
#
#     def test_pending_tlc_3(self):
#         for i in range(20):
#             self.start_new_fiber(self.generate_account(10000))
#         self.fiber3 = self.start_new_fiber(self.generate_account(10000))
#         self.open_channel(self.fiber2, self.fiber3, 1000 * 100000000, 1000 * 100000000)
#         self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1000 * 100000000)
#         for i in range(len(self.new_fibers)-1):
#             self.open_channel(self.new_fibers[i], self.fiber1, 1000 * 100000000, 1000 * 100000000)
#         for i in range(len(self.new_fibers)-1):
#             for i in range(20):
#                 try:
#                     self.send_payment(self.new_fibers[i], self.fiber3, 1 * 100000000,False,try_count=0)
#                 except Exception as e:
#                     pass
#         self.fiber1.get_client().disconnect_peer({
#             "peer_id": self.fiber2.get_peer_id()
#         })
#
#     def test_01000(self):
#         for i in range(21):
#             self.start_new_fiber("")
#         self.get_fiber_graph_balance()
#         # self.send_payment(self.fibers[0], self.fibers[-1], 1 * 100000000, False)
#
#     def test_add_time(self):
#         for i in range(400):
#             self.add_time_and_generate_block(2,900)
#             time.sleep(90)
#
#     def test_00000(self):
#         cells = self.get_commit_cells()
#         print(len(cells))
#         self.node.getClient().get_live_cell("0x0","0x135f65dbc5617dff4cce5e8e427ff77e39d77ecc2680d71dab885cbe2d436923")
#
#     def test_ba(self):
#         for i in range(3):
#             self.start_new_fiber("")
#         self.get_fiber_graph_balance()
