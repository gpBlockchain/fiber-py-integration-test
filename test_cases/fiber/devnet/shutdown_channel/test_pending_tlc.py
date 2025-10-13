# import time
#
# import pytest
#
# from framework.basic_fiber import FiberTest
#
#
# class Testo1(FiberTest):
#     FiberTest.debug = True
#
#     def test_basdadasd(self):
#         self.fiber1.get_client().add_tlc({
#             "channel_id": self.fiber1.get_client().list_channels({})["channels"][0]["channel_id"],
#             "amount": hex(1 * 100000000),
#             "payment_hash": "0x0000000000000000000000000000000000000000000000000000000000000100",
#             "expiry": hex((int(time.time()) + 20) * 1000),
#         })
#
#     def test_bbb1(self):
#         # self.get_fibers_balance_message()
#         # balance = self.udtContract.list_cell(self.node.getClient(),
#         #                                      self.get_account_script(self.fiber1.account_private)["args"],
#         #                                      self.get_account_script(self.fiber2.account_private)["args"],
#         #                                      )
#         # print(balance)
#         # self.node.getClient().get_transaction("0xd3b8e4353cc7c182a57d5b452355300bc38dc0e9e3abc4bb22d949445f4d56fe")
#         tx0 = self.get_tx_message("0x79d3410e9b3e8fa12029142243d390b921b2c61873712c5dfcf1deef4858411e")
#         tx1 = self.get_tx_message("0xbdcca0c997af2db070f32f099400254d69bd38fce56d9548d9a7981e1ef24862")
#         tx2 = self.get_tx_message("0x15bfecb4c310625b7aaf7662e111f97754145cd5fef3f287729bc43186f1da42")
#         tx3 = self.get_tx_message("0xe8e29b598c50cb02755a9c4c4a63c8fa3c7681db63450473a93a80ecb5a503f4")
#         # tx4 = self.get_tx_message("0xdb6b1256b2e936af139a26002f4bd66500899f8bf8f3d5c3827c73f703dfa9ff")
#         print(tx0)
#         print(tx1)
#         print(tx2)
#         print(tx3)
#         # print(tx4)
#
#     def test_21bbb(self):
#         self.fiber1.stop()
#
#     def test_ccc22(self):
#         # 0x5eb87b61068ace1864b7caf0e352baaf506d8077497e39858fa47c2e0968a933
#         # 0xa5f2cc454f9ffd049eb91dd93efb45147baa23a9b3b3187c99248e76d25e6801
#         # ret = self.get_tx_message("0x6b75b51098592146a8d0c039fc5b6d56a780dd4a21645caadb5cdc3f6db111f2")
#         ret = self.get_tx_message("0x0c8679687d691935577895ce618fb3060c7811f8abdfccb5a89c08de60c7f887")
#         # ret = self.get_fibers_balance_message()
#         print(ret)
#
#     def test_cc2cc(self):
#         # self.fiber1.start()
#         self.node.getClient().generate_epochs("0xa")
#         # self.get_fibers_balance_message()
#         # Step 12: Wait for the transaction to be committed and check the transaction message
#         tx_hash = self.wait_and_check_tx_pool_fee(1000, False, 1000)
#         self.Miner.miner_until_tx_committed(self.node, tx_hash)
#         tx_message = self.get_tx_message(tx_hash)
#         print(tx_message)
#
#     def test_cccc(self):
#         self.node.getClient().generate_epochs("0xa")
#         # self.get_fibers_balance_message()
#         # Step 12: Wait for the transaction to be committed and check the transaction message
#         tx_hash = self.wait_and_check_tx_pool_fee(1000, False, 1000)
#         tx_message = self.get_tx_message(tx_hash)
#         print(tx_message)
#
#         # self.get_fibers_balance_message()
#
#     def test_ckb_bibibi(self):
#         fiber3 = self.start_new_fiber(self.generate_account(1000))
#         # self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1000 * 100000000, 1000, 1000)
#         self.fiber1.get_client().open_channel({
#             "peer_id": self.fiber2.get_peer_id(),
#             "funding_amount": hex(1000 * 100000000),
#             "public": True,
#             "commitment_delay_epoch": "0xa000000000000000"
#         })
#         self.wait_for_channel_state(self.fiber1.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY")
#         self.send_payment(self.fiber1, self.fiber2, 100 * 100000000)
#         CHANNEL_ID = self.fiber1.get_client().list_channels({})["channels"][0]["channel_id"]
#         # 09-node1-add-tlc1.bru
#         self.fiber1.get_client().add_tlc({
#             "channel_id": CHANNEL_ID,
#             "amount": hex(1 * 100000000),
#             "payment_hash": "0x0000000000000000000000000000000000000000000000000000000000000000",
#             "expiry": hex((int(time.time()) + 10) * 1000),
#         })
#         # node3 generate invoice
#         time.sleep(1)
#         self.fiber2.get_client().add_tlc({
#             "channel_id": CHANNEL_ID,
#             "amount": hex(100 * 10000000),
#             "payment_hash": "0x0000000000000000000000000000000000000000000000000000000000000001",
#             "expiry": hex((int(time.time()) + 10) * 1000),
#         })
#         # node3 generate invoice
#         time.sleep(1)
#         tlcs = []
#         payment_preimages = []
#         # for i in range(1):
#         #     payment_preimage = self.generate_random_preimage()
#         #     invoice = fiber3.get_client().new_invoice({
#         #         "amount": hex(300 * 100000000),
#         #         "currency": "Fibd",
#         #         "description": "test invoice generated by node3",
#         #         "expiry": "0xe10",
#         #         "final_expiry_delta": "0xDFFA0",
#         #         "payment_preimage": payment_preimage
#         #     })
#         #     tlc = self.fiber1.get_client().add_tlc({
#         #         "channel_id": CHANNEL_ID,
#         #         "amount": hex(300 * 100000000),
#         #         "payment_hash": invoice["invoice"]["data"]["payment_hash"],
#         #         "expiry": hex((int(time.time()) + 200) * 1000),
#         #     })
#         #     payment_preimages.append(payment_preimage)
#         #     tlcs.append(tlc)
#         #     time.sleep(1)
#
#         time.sleep(1)
#         self.fiber1.get_client().shutdown_channel({
#             "channel_id": CHANNEL_ID,
#             "close_script": {
#                 "code_hash": "0x9bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8",
#                 "hash_type": "type",
#                 "args": self.account1["lock_arg"],
#             },
#             "fee_rate": "0x3FC",
#             "force": True,
#         })
#         # self.fiber1.stop()
#         # for i in range(1):
#         #     self.fiber2.get_client().remove_tlc({
#         #         "channel_id": CHANNEL_ID,
#         #         "tlc_id": tlcs[i]["tlc_id"],
#         #         "reason": {
#         #             "payment_preimage": payment_preimages[i]
#         #         }
#         #     })
#
#         tx_hash = self.wait_and_check_tx_pool_fee(1000, False, 1000)
#         self.Miner.miner_until_tx_committed(self.node, tx_hash)
#         self.fiber2.stop()
#         # tx_hash = self.wait_and_check_tx_pool_fee(1000, False, 1000)
#         # self.Miner.miner_until_tx_committed(self.node, tx_hash)
#         # self.fiber1.stop()
#         # tx_hash2 = self.wait_and_check_tx_pool_fee(1000, False, 1000)
#         # self.Miner.miner_until_tx_committed(self.node, tx_hash2)
#         #
#         # # Step 11: Generate epochs
#         # self.node.getClient().generate_epochs("0xa")
#         # self.get_fibers_balance_message()
#         # # Step 12: Wait for the transaction to be committed and check the transaction message
#         # tx_hash3 = self.wait_and_check_tx_pool_fee(1000, False, 1000)
#         # tx_message = self.get_tx_message(tx_hash)
#         # print(tx_message)
#         #
#         # tx_message2 = self.get_tx_message(tx_hash2)
#         #
#         # tx_message3 = self.get_tx_message(tx_hash3)
#         # print(tx_message3)
#         # tx_message3 = self.get_tx_message(tx_hash3)
#         # print(tx_message)
#         # print(tx_message2)
#         # print(tx_message3)
#
#     def test_ckb(self):
#         fiber3 = self.start_new_fiber(self.generate_account(1000))
#         self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1000 * 100000000, 1000, 1000)
#         CHANNEL_ID = self.fiber1.get_client().list_channels({})["channels"][0]["channel_id"]
#         # 09-node1-add-tlc1.bru
#         # self.fiber1.get_client().add_tlc({
#         #     "channel_id": CHANNEL_ID,
#         #     "amount": hex(1 * 100000000),
#         #     "payment_hash": "0x0000000000000000000000000000000000000000000000000000000000000000",
#         #     "expiry": hex((int(time.time()) + 400) * 1000),
#         # })
#         # node3 generate invoice
#
#         tlcs = []
#         payment_preimages = []
#         for i in range(1):
#             payment_preimage = self.generate_random_preimage()
#             invoice = fiber3.get_client().new_invoice({
#                 "amount": hex(300 * 100000000),
#                 "currency": "Fibd",
#                 "description": "test invoice generated by node3",
#                 "expiry": "0xe10",
#                 "final_expiry_delta": "0xDFFA0",
#                 "payment_preimage": payment_preimage
#             })
#             tlc = self.fiber1.get_client().add_tlc({
#                 "channel_id": CHANNEL_ID,
#                 "amount": hex(300 * 100000000),
#                 "payment_hash": invoice["invoice"]["data"]["payment_hash"],
#                 "expiry": hex((int(time.time()) + 100) * 1000),
#             })
#             payment_preimages.append(payment_preimage)
#             tlcs.append(tlc)
#             time.sleep(1)
#
#         time.sleep(1)
#         # self.fiber1.stop()
#         self.fiber1.get_client().shutdown_channel({
#             "channel_id": CHANNEL_ID,
#             "close_script": {
#                 "code_hash": "0x9bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8",
#                 "hash_type": "type",
#                 "args": self.account1["lock_arg"],
#             },
#             "fee_rate": "0x3FC",
#             "force": True,
#         })
#         for i in range(1):
#             self.fiber2.get_client().remove_tlc({
#                 "channel_id": CHANNEL_ID,
#                 "tlc_id": tlcs[i]["tlc_id"],
#                 "reason": {
#                     "payment_preimage": payment_preimages[i]
#                 }
#             })
#
#         tx_hash = self.wait_and_check_tx_pool_fee(1000, False, 1000)
#         # self.fiber1.stop()
#         message = self.get_tx_message(tx_hash)
#
#         self.get_fibers_balance_message()
#         print(message)
#         # self.Miner.miner_until_tx_committed(self.node, tx_hash)
#         #
#         # tx_hash2 = self.wait_and_check_tx_pool_fee(1000, False, 1000)
#         # self.Miner.miner_until_tx_committed(self.node, tx_hash2)
#         #
#         # # Step 11: Generate epochs
#         # self.node.getClient().generate_epochs("0xa")
#         # self.get_fibers_balance_message()
#         # # Step 12: Wait for the transaction to be committed and check the transaction message
#         # tx_hash3 = self.wait_and_check_tx_pool_fee(1000, False, 1000)
#         # tx_message = self.get_tx_message(tx_hash)
#         # print(tx_message)
#         #
#         # tx_message2 = self.get_tx_message(tx_hash2)
#         #
#         # tx_message3 = self.get_tx_message(tx_hash3)
#         # print(tx_message3)
#         # tx_message3 = self.get_tx_message(tx_hash3)
#         # print(tx_message)
#         # print(tx_message2)
#         # print(tx_message3)
#
#     def test_udt_with_2tl2c(self):
#         self.fiber1.get_client().open_channel({
#             "peer_id": self.fiber2.get_peer_id(),
#             "funding_amount": hex(1000 * 100000000),
#             "public": True,
#             "commitment_delay_epoch": "0xa000000000000000",
#             "funding_udt_type_script": self.get_account_udt_script(
#                 self.fiber1.account_private
#             ),
#         })
#         time.sleep(1)
#         self.wait_for_channel_state(self.fiber1.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY")
#         CHANNEL_ID = self.fiber1.get_client().list_channels({})["channels"][0]["channel_id"]
#
#         self.send_payment(self.fiber1, self.fiber2, 1000, True, self.get_account_udt_script(
#             self.fiber1.account_private
#         ))
#
#         self.fiber1.get_client().add_tlc({
#             "channel_id": CHANNEL_ID,
#             "amount": hex(1 * 100000000),
#             "payment_hash": "0x0000000000000000000000000000000000000000000000000000000000000000",
#             "expiry": hex((int(time.time()) + 10) * 1000),
#         })
#         # node3 generate invoice
#         time.sleep(1)
#         self.fiber2.get_client().add_tlc({
#             "channel_id": CHANNEL_ID,
#             "amount": hex(100),
#             "payment_hash": "0x0000000000000000000000000000000000000000000000000000000000000001",
#             "expiry": hex((int(time.time()) + 10) * 1000),
#         })
#
#         time.sleep(1)
#         self.fiber1.get_client().shutdown_channel({
#             "channel_id": CHANNEL_ID,
#             "close_script": {
#                 "code_hash": "0x9bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8",
#                 "hash_type": "type",
#                 "args": self.account1["lock_arg"],
#             },
#             "fee_rate": "0x3FC",
#             "force": True,
#         })
#
#     def test_udt(self):
#         fiber3 = self.start_new_fiber(self.generate_account(1000))
#         self.fiber1.get_client().open_channel({
#             "peer_id": self.fiber2.get_peer_id(),
#             "funding_amount": hex(1000 * 100000000),
#             "public": True,
#             "funding_udt_type_script": self.get_account_udt_script(
#                 self.fiber1.account_private
#             ),
#         })
#         time.sleep(1)
#         self.wait_for_channel_state(self.fiber1.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY")
#         CHANNEL_ID = self.fiber1.get_client().list_channels({})["channels"][0]["channel_id"]
#         tlcs = []
#         payment_preimages = []
#         self.send_payment(self.fiber1, self.fiber2, 1, True, self.get_account_udt_script(
#             self.fiber1.account_private
#         ))
#         for i in range(1):
#             payment_preimage = self.generate_random_preimage()
#             invoice = fiber3.get_client().new_invoice({
#                 "amount": hex(300 * 100000000),
#                 "currency": "Fibd",
#                 "description": "test invoice generated by node3",
#                 "expiry": "0xe10",
#                 "final_expiry_delta": "0xDFFA0",
#                 "payment_preimage": payment_preimage,
#                 "udt_type_script": self.get_account_udt_script(
#                     self.fiber1.account_private
#                 ),
#             })
#             tlc = self.fiber1.get_client().add_tlc({
#                 "channel_id": CHANNEL_ID,
#                 "amount": hex(300 * 100000000),
#                 "payment_hash": invoice["invoice"]["data"]["payment_hash"],
#                 "expiry": hex((int(time.time()) + 10) * 1000),
#             })
#             payment_preimages.append(payment_preimage)
#             tlcs.append(tlc)
#             time.sleep(1)
#
#         time.sleep(1)
#         self.fiber1.get_client().shutdown_channel({
#             "channel_id": CHANNEL_ID,
#             "close_script": {
#                 "code_hash": "0x9bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8",
#                 "hash_type": "type",
#                 "args": self.account1["lock_arg"],
#             },
#             "fee_rate": "0x3FC",
#             "force": True,
#         })
#         # self.fiber1.stop()
#         for i in range(1):
#             self.fiber2.get_client().remove_tlc({
#                 "channel_id": CHANNEL_ID,
#                 "tlc_id": tlcs[i]["tlc_id"],
#                 "reason": {
#                     "payment_preimage": payment_preimages[i]
#                 }
#             })
#         time.sleep(5)
#         # self.fiber1.stop()
#         tx_hash = self.wait_and_check_tx_pool_fee(1000, False, 1000)
#         self.Miner.miner_until_tx_committed(self.node, tx_hash)
#
#         tx_hash2 = self.wait_and_check_tx_pool_fee(1000, False, 1000)
#         self.Miner.miner_until_tx_committed(self.node, tx_hash2)
#
#         # Step 11: Generate epochs
#         self.node.getClient().generate_epochs("0xa")
#         self.get_fibers_balance_message()
#         # Step 12: Wait for the transaction to be committed and check the transaction message
#         tx_hash3 = self.wait_and_check_tx_pool_fee(1000, False, 1000)
#         self.Miner.miner_until_tx_committed(self.node, tx_hash3)
#         tx_message = self.get_tx_message(tx_hash)
#         print(tx_message)
#
#         tx_message2 = self.get_tx_message(tx_hash2)
#
#         tx_message3 = self.get_tx_message(tx_hash3)
#         print(tx_message3)
#         tx_message3 = self.get_tx_message(tx_hash3)
#         print(tx_message)
#         print(tx_message2)
#         print(tx_message3)
#
#     @pytest.mark.skip("https://github.com/nervosnetwork/fiber/issues/523")
#     def test_ckb_with_no_image_tlc(self):
#         self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1000 * 100000000, 1000, 1000)
#         CHANNEL_ID = self.fiber1.get_client().list_channels({})["channels"][0]["channel_id"]
#
#         tlcs = []
#         payment_preimages = []
#         for i in range(1):
#             payment_preimage = self.generate_random_preimage()
#
#             tlc = self.fiber1.get_client().add_tlc({
#                 "channel_id": CHANNEL_ID,
#                 "amount": hex(300 * 100000000),
#                 "payment_hash": payment_preimage,
#                 "expiry": hex((int(time.time()) + 10) * 1000),
#             })
#             tlcs.append(tlc)
#             time.sleep(1)
#
#         time.sleep(1)
#         self.fiber1.get_client().shutdown_channel({
#             "channel_id": CHANNEL_ID,
#             "close_script": {
#                 "code_hash": "0x9bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8",
#                 "hash_type": "type",
#                 "args": self.account1["lock_arg"],
#             },
#             "fee_rate": "0x3FC",
#             "force": True,
#         })
#         tx_hash = self.wait_and_check_tx_pool_fee(1000, False, 1000)
#         self.fiber1.stop()
#         self.get_tx_message(tx_hash)
#         self.Miner.miner_until_tx_committed(self.node, tx_hash)
#
#         tx_hash2 = self.wait_and_check_tx_pool_fee(1000, False, 1000)
#         self.Miner.miner_until_tx_committed(self.node, tx_hash2)
#
#         # Step 11: Generate epochs
#         self.node.getClient().generate_epochs("0xa")
#         self.get_fibers_balance_message()
#         # Step 12: Wait for the transaction to be committed and check the transaction message
#         tx_hash3 = self.wait_and_check_tx_pool_fee(1000, False, 1000)
#         tx_message = self.get_tx_message(tx_hash)
#         print(tx_message)
#
#         tx_message2 = self.get_tx_message(tx_hash2)
#
#         tx_message3 = self.get_tx_message(tx_hash3)
#         print(tx_message3)
#         tx_message3 = self.get_tx_message(tx_hash3)
#         print(tx_message)
#         print(tx_message2)
#         print(tx_message3)
#
#     def test_pending_tlc_attack(self):
#         pass
