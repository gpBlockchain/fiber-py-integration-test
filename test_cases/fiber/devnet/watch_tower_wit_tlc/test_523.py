# import time
#
# import pytest
#
# from framework.basic_fiber import FiberTest
# from framework.test_fiber import FiberConfigPath
#
#
# class Test523(FiberTest):
#     fiber_version = FiberConfigPath.CURRENT_DEV_DEBUG
#     start_fiber_config = {"fiber_watchtower_check_interval_seconds": 5}
#
#     # @pytest.mark.skip(reason="https://github.com/nervosnetwork/fiber/issues/523")
#     def test_523(self):
#         self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1)
#         CHANNEL_ID = self.fiber1.get_client().list_channels({})["channels"][0][
#             "channel_id"
#         ]
#         self.fiber1.get_client().add_tlc(
#             {
#                 "channel_id": CHANNEL_ID,
#                 "amount": hex(300 * 100000000),
#                 "payment_hash": "0x0000000000000000000000000000000000000000000000000000000000000000",
#                 "expiry": hex((int(time.time()) + 8000) * 1000),
#             }
#         )
#         time.sleep(1)
#         self.fiber1.get_client().shutdown_channel(
#             {
#                 "channel_id": CHANNEL_ID,
#                 "force": True,
#             }
#         )
#         tx = self.wait_and_check_tx_pool_fee(1000, False)
#         self.Miner.miner_until_tx_committed(self.node, tx)
#         self.fiber1.stop()
#         self.node.getClient().generate_epochs("0xa", 0)
#         tx = self.wait_and_check_tx_pool_fee(1000, False)
#         msg = self.get_tx_message(tx)
#         assert msg["fee"] > 30000000000
