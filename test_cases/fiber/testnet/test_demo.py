# import time
#
# from framework.fiber_rpc import FiberRPCClient
# from framework.rpc import RPCClient
# from framework.util import to_int_from_big_uint128_le
#
#
# class Test1:
#     cryptapeFiber1 = FiberRPCClient("http://18.162.235.225:8117")
#     cryptapeFiber2 = FiberRPCClient("http://18.163.221.211:8117")
#     # ckbCLient = RPCClient("http://18.167.71.41:8080")
#     # http://43.199.108.57:8148
#     # http://43.199.108.57:8129
#     # attackFiber = FiberRPCClient("http://18.167.71.41:8128")
#     # cryptapeFiber1 = FiberRPCClient("http://43.199.108.57:8127")
#     # cryptapeFiber2 = FiberRPCClient("http://43.199.108.57:8129")
#     ckbClient = RPCClient("https://testnet.ckb.dev")
#
#     def test_002(self):
#         self.cryptapeFiber1.node_info()
#         self.cryptapeFiber2.node_info()
#
#     def test_001(self):
#         # channels = self.cryptapeFiber2.list_channels({"include_closed": True})
#         channels = self.cryptapeFiber1.list_channels({
#             # "peer_id":self.cryptapeFiber1.get_peer_id()
#         })
#         lock_script = self.cryptapeFiber1.node_info()['default_funding_lock_script']
#         for channel in channels['channels']:
#             if channel['state']['state_name'] == 'CHANNEL_READY':
#                 try:
#                     self.cryptapeFiber1.shutdown_channel({
#                         "channel_id": channel["channel_id"],
#                         "close_script": lock_script,
#                         "fee_rate": "0x3FC",
#                     })
#                     time.sleep(1)
#                 except Exception as e:
#                     pass
#
#     def get_tx_message(self, tx_hash):
#         if tx_hash is None:
#             return []
#         tx = self.ckbClient.get_transaction(tx_hash)
#         input_cells = []
#         output_cells = []
#
#         # self.node.getClient().get_transaction(tx['transaction']['inputs'][])
#         for i in range(len(tx["transaction"]["inputs"])):
#             pre_cell = self.ckbClient.get_transaction(
#                 tx["transaction"]["inputs"][i]["previous_output"]["tx_hash"]
#             )["transaction"]["outputs"][
#                 int(tx["transaction"]["inputs"][i]["previous_output"]["index"], 16)
#             ]
#             pre_cell_outputs_data = self.ckbClient.get_transaction(
#                 tx["transaction"]["inputs"][i]["previous_output"]["tx_hash"]
#             )["transaction"]["outputs_data"][
#                 int(tx["transaction"]["inputs"][i]["previous_output"]["index"], 16)
#             ]
#             if pre_cell["type"] is None:
#                 input_cells.append(
#                     {
#                         "args": pre_cell["lock"]["args"],
#                         "capacity": int(pre_cell["capacity"], 16),
#                     }
#                 )
#                 continue
#             input_cells.append(
#                 {
#                     "args": pre_cell["lock"]["args"],
#                     "capacity": int(pre_cell["capacity"], 16),
#                     "udt_args": pre_cell["type"]["args"],
#                     "udt_capacity": to_int_from_big_uint128_le(pre_cell_outputs_data),
#                 }
#             )
#
#         for i in range(len(tx["transaction"]["outputs"])):
#             if tx["transaction"]["outputs"][i]["type"] is None:
#                 output_cells.append(
#                     {
#                         "args": tx["transaction"]["outputs"][i]["lock"]["args"],
#                         "capacity": int(
#                             tx["transaction"]["outputs"][i]["capacity"], 16
#                         ),
#                     }
#                 )
#                 continue
#             output_cells.append(
#                 {
#                     "args": tx["transaction"]["outputs"][i]["lock"]["args"],
#                     "capacity": int(tx["transaction"]["outputs"][i]["capacity"], 16),
#                     "udt_args": tx["transaction"]["outputs"][i]["type"]["args"],
#                     "udt_capacity": to_int_from_big_uint128_le(
#                         tx["transaction"]["outputs_data"][i]
#                     ),
#                 }
#             )
#         print({"input_cells": input_cells, "output_cells": output_cells})
#         input_cap = 0
#         for i in range(len(input_cells)):
#             input_cap = input_cap + input_cells[i]["capacity"]
#         for i in range(len(output_cells)):
#             input_cap = input_cap - output_cells[i]["capacity"]
#         return {
#             "input_cells": input_cells,
#             "output_cells": output_cells,
#             "fee": input_cap,
#         }
#
#     def get_ln_tx_trace(self, open_channel_tx_hash):
#         tx_trace = []
#         tx_trace.append(
#             {
#                 "tx_hash": open_channel_tx_hash,
#                 "msg:": self.get_tx_message(open_channel_tx_hash),
#             }
#         )
#         tx, code_hash = self.get_ln_cell_death_hash(open_channel_tx_hash)
#         tx_trace.append({"tx_hash": tx, "msg": self.get_tx_message(tx)})
#         while tx is not None:
#             tx, new_code_hash = self.get_ln_cell_death_hash(tx)
#             tx_trace.append({"tx_hash": tx, "msg": self.get_tx_message(tx)})
#             if (
#                 new_code_hash
#                 != "0x740dee83f87c6f309824d8fd3fbdd3c8380ee6fc9acc90b1a748438afcdf81d8"
#             ):
#                 print("code_hash changed, stop trace")
#                 print("old code_hash:", code_hash, "new code_hash:", new_code_hash)
#                 tx = None
#         for i in range(len(tx_trace)):
#             print(tx_trace[i])
#         return tx_trace
#
#     def test_list_channe(self):
#         self.cryptapeFiber2.list_channels({"include_closed": True})
#
#     def test_check_shutdown_tx_status(self):
#         """
#         1. 查询list_channel output is live status
#         2. 如果不是live 状态，查询该cell 是否消费
#         3. 如果消费了，查询该cell 接下来的动向
#         Returns:
#         """
#         list_channels = self.cryptapeFiber2.list_channels({"include_closed": True})
#         traces = []
#         fiber1_arg = self.cryptapeFiber1.node_info()["default_funding_lock_script"][
#             "args"
#         ]
#         fiber2_arg = self.cryptapeFiber2.node_info()["default_funding_lock_script"][
#             "args"
#         ]
#         fee = 455
#         for channel in list_channels["channels"]:
#             if channel["state"] != {
#                 "state_name": "CLOSED",
#                 "state_flags": "UNCOOPERATIVE",
#             }:
#                 continue
#             trace = self.get_ln_tx_trace(channel["channel_outpoint"][:-8])
#             traces.append({"channel_id": channel["channel_id"], "trace": trace})
#             fiber1_balance = int(channel["remote_balance"], 16)
#             fiber2_balance = (
#                 int(channel["local_balance"], 16)
#                 - int(channel["offered_tlc_balance"], 16)
#                 + int(channel["received_tlc_balance"], 16)
#             )
#
#             assert {
#                 "args": fiber1_arg,
#                 "capacity": fiber1_balance - fee + 6200000000,
#             } in trace[-2]["msg"]["output_cells"]
#             assert {
#                 "args": fiber2_arg,
#                 "capacity": fiber2_balance - fee + 6200000000,
#             } in trace[-2]["msg"]["output_cells"]
#
#         i = 0
#         for trace in traces:
#             print("i:", i)
#             print("trace:", trace)
#             i += 1
#
#     def test_0201(self):
#         trace = self.get_ln_tx_trace(
#             "0x0d12df7ebe648edbddb37279c433987aba301239a4a6e61f88c8a055861636f0"
#         )
#         print(trace)
#
#     def get_ln_cell_death_hash(self, tx_hash):
#         tx = self.ckbClient.get_transaction(tx_hash)
#         cellLock = tx["transaction"]["outputs"][0]["lock"]
#
#         txs = self.ckbClient.get_transactions(
#             {
#                 "script": cellLock,
#                 "script_type": "lock",
#                 "script_search_mode": "exact",
#             },
#             "asc",
#             "0xff",
#             None,
#         )
#         if len(txs["objects"]) == 2:
#             return txs["objects"][1]["tx_hash"], cellLock["code_hash"]
#         return None, None
#
#     def test_get_next_cell(self):
#         pass
#
#     def test_force_shutdown(self):
#
#         default_funding_lock_script = self.cryptapeFiber2.node_info()[
#             "default_funding_lock_script"
#         ]
#         for channel in self.cryptapeFiber2.list_channels({})["channels"]:
#             self.cryptapeFiber2.shutdown_channel(
#                 {
#                     "channel_id": channel["channel_id"],
#                     "close_script": default_funding_lock_script,
#                     # "force": True
#                 }
#             )
#
#     def test_shutdown(self):
#         for channel in self.cryptapeFiber2.list_channels({})["channels"]:
#             self.cryptapeFiber2.shutdown_channel(
#                 {
#                     "channel_id": channel["channel_id"],
#                     # "close_script": default_funding_lock_script,
#                     "force": True,
#                 }
#             )
#
#     def test_send_batch_tx(self):
#         for i in range(200):
#             self.cryptapeFiber2.send_payment(
#                 {
#                     "amount": hex(1),
#                     "target_pubkey": "025077ba0890c3ff83aff3c13369e62c4ff54a1c3c889243835f8206a8a2d07981",
#                     "keysend": True,
#                 }
#             )
#         for channel in self.cryptapeFiber2.list_channels({})["channels"]:
#             self.cryptapeFiber2.shutdown_channel(
#                 {
#                     "channel_id": channel["channel_id"],
#                     # "close_script": default_funding_lock_script,
#                     "force": True,
#                 }
#             )
#         # self.cryptapeFiber2.disconnect_peer({
#         #     "peer_id": "Qme5iDzNKjnnSK3ryJvm1sbURXNshicUH1YdakDRtAPLuv"
#         # })
#
#     def test_list_peer(self):
#         self.cryptapeFiber2.node_info()
#         for i in range(1):
#             self.cryptapeFiber2.open_channel(
#                 {
#                     "peer_id": "Qme5iDzNKjnnSK3ryJvm1sbURXNshicUH1YdakDRtAPLuv",
#                     "funding_amount": hex(1000 * 100000000),
#                     "public": True,
#                 }
#             )
#
#             wait_for_channel_state(
#                 self.cryptapeFiber2,
#                 "Qme5iDzNKjnnSK3ryJvm1sbURXNshicUH1YdakDRtAPLuv",
#                 "CHANNEL_READY",
#                 120,
#             )
#             # self.cryptapeFiber2.list_channels({})
#             self.cryptapeFiber2.send_payment(
#                 {
#                     "amount": hex(1 * 100000000),
#                     "target_pubkey": "025077ba0890c3ff83aff3c13369e62c4ff54a1c3c889243835f8206a8a2d07981",
#                     "keysend": True,
#                 }
#             )
#
#         # default_funding_lock_script = self.cryptapeFiber2.node_info()['default_funding_lock_script']
#         # self.cryptapeFiber2.shutdown_channel({
#         #     "channel_id": self.cryptapeFiber2.list_channels({})['channels'][0]['channel_id'],
#         #     # "close_script": default_funding_lock_script,
#         #     "force": True
#         # })
#         # self.cryptapeFiber2.graph_channels()
#         # self.cryptapeFiber1.connect_peer(self.cryptapeFiber2)
#
#     def test_bbb(self):
#         # self.cryptapeFiber2.list_channels({
#         #     "peer_id":"QmPoru4YHCpkjHNdfbPkD5mYtoZQRfCvctVrnuoDz2j4fR"
#         # })
#         self.cryptapeFiber1.node_info()
#         # {"jsonrpc": "2.0", "result": {"channels": [{"channel_id": "0xa27e5a37c9d63de37adf96364f694bdf74e7f8e914ee30d06534e818afbd65ad", "is_public": true, "channel_outpoint": "0xff4e248f343db4e014d9e48dd6d747b6aac8d3c99e24ad290907449524ce059600000000", "peer_id": "QmXen3eUHhywmutEzydCsW4hXBoeVmdET2FJvMX69XJ1Eo", "funding_udt_type_script": null, "state": {"state_name": "CHANNEL_READY"}, "local_balance": "0xa2cb77e54", "offered_tlc_balance": "0x5f769a2", "remote_balance": "0x46688b3ac", "received_tlc_balance": "0x0", "latest_commitment_transaction_hash": "0x0b63f8d3001f9f10740d765711e36386df62b0390e02f92130ab53584558be43", "created_at": "0x195f475b021", "enabled": true, "tlc_expiry_delta": "0x5265c00", "tlc_fee_proportional_millionths": "0x3e8"}]}, "id": 42}
#         # {"jsonrpc": "2.0", "result": {"channels": [{"channel_id": "0xa27e5a37c9d63de37adf96364f694bdf74e7f8e914ee30d06534e818afbd65ad", "is_public": true, "channel_outpoint": "0xff4e248f343db4e014d9e48dd6d747b6aac8d3c99e24ad290907449524ce059600000000", "peer_id": "QmXen3eUHhywmutEzydCsW4hXBoeVmdET2FJvMX69XJ1Eo", "funding_udt_type_script": null, "state": {"state_name": "CHANNEL_READY"}, "local_balance": "0xa2cb77e52", "offered_tlc_balance": "0x5f769a2", "remote_balance": "0x46688b3ae", "received_tlc_balance": "0x0", "latest_commitment_transaction_hash": "0xc8598f3e6fcfb344edf34b488e87f7284542573a56ab4efb7ff88bc63cb28470", "created_at": "0x195f475b021", "enabled": true, "tlc_expiry_delta": "0x5265c00", "tlc_fee_proportional_millionths": "0x3e8"}]}, "id": 42}
#         # {"jsonrpc": "2.0", "result": {"channels": [{"channel_id": "0xa27e5a37c9d63de37adf96364f694bdf74e7f8e914ee30d06534e818afbd65ad", "is_public": true, "channel_outpoint": "0xff4e248f343db4e014d9e48dd6d747b6aac8d3c99e24ad290907449524ce059600000000", "peer_id": "QmXen3eUHhywmutEzydCsW4hXBoeVmdET2FJvMX69XJ1Eo", "funding_udt_type_script": null, "state": {"state_name": "CHANNEL_READY"}, "local_balance": "0xa2cb77e50", "offered_tlc_balance": "0x5f769a2", "remote_balance": "0x46688b3b0", "received_tlc_balance": "0x0", "latest_commitment_transaction_hash": "0x9535e4425f0bd26fa15cce82030339aa250c0bad97e6fa635a1c10a58dc48bf6", "created_at": "0x195f475b021", "enabled": true, "tlc_expiry_delta": "0x5265c00", "tlc_fee_proportional_millionths": "0x3e8"}]}, "id": 42}
#         # {"jsonrpc": "2.0", "result": {"channels": [{"channel_id": "0xa27e5a37c9d63de37adf96364f694bdf74e7f8e914ee30d06534e818afbd65ad", "is_public": true, "channel_outpoint": "0xff4e248f343db4e014d9e48dd6d747b6aac8d3c99e24ad290907449524ce059600000000", "peer_id": "QmXen3eUHhywmutEzydCsW4hXBoeVmdET2FJvMX69XJ1Eo", "funding_udt_type_script": null, "state": {"state_name": "CHANNEL_READY"}, "local_balance": "0xa2cb77e4e", "offered_tlc_balance": "0x5f769a2", "remote_balance": "0x46688b3b2", "received_tlc_balance": "0x0", "latest_commitment_transaction_hash": "0xbe22f195afcd957a3bb1ccf95d666471cb8b285b8f6f6fcfedb0064a4cf1277f", "created_at": "0x195f475b021", "enabled": true, "tlc_expiry_delta": "0x5265c00", "tlc_fee_proportional_millionths": "0x3e8"}]}, "id": 42}
#         # local: 43699895892
#         # 0xff4e248f343db4e014d9e48dd6d747b6aac8d3c99e24ad290907449524ce0596
#
#         # hash
#
#     def test_bblll(self):
#         channels = self.cryptapeFiber1.list_channels(
#             {
#                 "peer_id": self.cryptapeFiber2.get_peer_id(),
#             }
#         )
#         for channel in channels["channels"]:
#             print(channel["channel_outpoint"][:-8])
#
#     def test_01(self):
#         # self.cryptapeFiber1.node_info()
#         # self.cryptapeFiber2.node_info()
#         # cryptapeFiber1 = FiberRPCClient("http://18.162.235.225:8227")
#         chanels = self.cryptapeFiber1.graph_channels({"limit": "0xffffff"})
#         chanels = self.cryptapeFiber1.list_channels(
#             {"peer_id": self.cryptapeFiber2.get_peer_id()}
#         )
#
#         print(chanels)
#         # print(len(chanels["channels"]))
#         # status_list = []
#         # for channel in chanels["channels"]:
#         #     print(channel["channel_outpoint"][:-8])
#         #     status = self.ckbCLient.get_live_cell("0x0", channel["channel_outpoint"][:-8])
#         #     status_list.append({
#         #         "hash": channel["channel_outpoint"][:-8],
#         #         "status": status["status"]
#         #     })
#         # for i in range(len(status_list)):
#         #     print(status_list[i])
#
#     def test_00000(self):
#         self.cryptapeFiber1.node_info()
#         self.cryptapeFiber2.node_info()
#
#     def test_02(self):
#         channels = self.cryptapeFiber2.list_channels(
#             {
#                 "peer_id": self.cryptapeFiber1.get_peer_id(),
#             }
#         )
#         for channel in channels["channels"]:
#             # self.cryptapeFiber2.update_channel({
#             #     "channel_id": channel["channel_id"],
#             #     "tlc_fee_proportional_millionths": hex(1001),
#             # })
#             # self.cryptapeFiber1.update_channel({
#             #     "channel_id": channel["channel_id"],
#             #     "tlc_fee_proportional_millionths": hex(1001),
#             # })
#             print(int(channel["tlc_fee_proportional_millionths"], 16))
#
#         channels = self.cryptapeFiber1.list_channels(
#             {
#                 "peer_id": self.cryptapeFiber2.get_peer_id(),
#             }
#         )
#         for channel in channels["channels"]:
#             # self.cryptapeFiber2.update_channel({
#             #     "channel_id": channel["channel_id"],
#             #     "tlc_fee_proportional_millionths": hex(1001),
#             # })
#             # self.cryptapeFiber1.update_channel({
#             #     "channel_id": channel["channel_id"],
#             #     "tlc_fee_proportional_millionths": hex(1001),
#             # })
#             print(int(channel["tlc_fee_proportional_millionths"], 16))
#
#     def test_fiber2_balance(self):
#         channels = self.cryptapeFiber2.list_channels(
#             {"peer_id": self.cryptapeFiber2.get_peer_id()}
#         )
#         for channel in channels["channels"]:
#             print(
#                 "status:",
#                 channel["state"]["state_name"],
#                 " channel:",
#                 int(channel["remote_balance"], 16),
#                 " udt:",
#                 channel["funding_udt_type_script"],
#                 "channel_id",
#                 channel["channel_id"],
#             )
#
#     def test_fiber2_send_payment(self):
#         funding_udt_type_script = {
#             "code_hash": "0x1142755a044bf2ee358cba9f2da187ce928c91cd4dc8692ded0337efa677d21a",
#             "hash_type": "type",
#             "args": "0x878fcc6f1f08d48e87bb1c3b3d5083f23f8a39c5d5c764f253b55b998526439b",
#         }
#         send_payment(
#             self.cryptapeFiber2,
#             self.cryptapeFiber1,
#             1112012,
#             funding_udt_type_script,
#             20 * 60,
#         )
#
#     def test_open_channel(self):
#         # ckb
#         for i in range(3):
#             funding_amount = 100000 * 100000000
#             send_amount = 50001 * 100000000
#             self.cryptapeFiber1.open_channel(
#                 {
#                     "peer_id": self.cryptapeFiber2.get_peer_id(),
#                     "funding_amount": hex(funding_amount),
#                     "public": True,
#                 }
#             )
#             wait_for_channel_state(
#                 self.cryptapeFiber1, self.cryptapeFiber2.get_peer_id(), "CHANNEL_READY", 120
#             )
#             send_payment(
#                 self.cryptapeFiber1, self.cryptapeFiber2, send_amount, None, 20 * 60
#             )
#
#
#     def test_open_channel_udt(self):
#
#         funding_amount = 1000 * 100000000
#         send_amount = 501 * 100000000
#         funding_udt_type_script = {
#             "code_hash": "0x1142755a044bf2ee358cba9f2da187ce928c91cd4dc8692ded0337efa677d21a",
#             "hash_type": "type",
#             "args": "0x878fcc6f1f08d48e87bb1c3b3d5083f23f8a39c5d5c764f253b55b998526439b",
#         }
#         self.cryptapeFiber1.open_channel(
#             {
#                 "peer_id": self.cryptapeFiber2.get_peer_id(),
#                 "funding_amount": hex(funding_amount),
#                 "public": True,
#                 "funding_udt_type_script": funding_udt_type_script,
#             }
#         )
#         wait_for_channel_state(
#             self.cryptapeFiber1, self.cryptapeFiber2.get_peer_id(), "CHANNEL_READY", 120
#         )
#         send_payment(
#             self.cryptapeFiber1,
#             self.cryptapeFiber2,
#             send_amount,
#             funding_udt_type_script,
#             20 * 60,
#         )
#
#     def test_00005(self):
#         channels = self.cryptapeFiber2.list_channels(
#             {"peer_id": "QmeZBuYm6iyWrhU3ennkcwYJM5LmFuparVfh7ni4hkUaCE"}
#         )
#         print(channels)
#
#
# def wait_for_channel_state(client, peer_id, expected_state, timeout=120):
#     """Wait for a channel to reach a specific state."""
#     for _ in range(timeout):
#         channels = client.list_channels({"peer_id": peer_id, "include_closed": True})
#         if channels["channels"][0]["state"]["state_name"] == expected_state:
#             print(f"Channel reached expected state: {expected_state}")
#             return channels["channels"][0]["channel_id"]
#         print(
#             f"Waiting for channel state: {expected_state}, current state: {channels['channels'][0]['state']['state_name']}"
#         )
#         time.sleep(1)
#     raise TimeoutError(
#         f"Channel did not reach state {expected_state} within timeout period."
#     )
#
#
# def send_payment(
#     fiber1: FiberRPCClient, fiber2: FiberRPCClient, amount, udt=None, wait_times=300
# ):
#     try_times = 0
#     payment = None
#     for i in range(wait_times):
#         try:
#             payment = fiber1.send_payment(
#                 {
#                     "amount": hex(amount),
#                     "target_pubkey": fiber2.node_info()["node_id"],
#                     "keysend": True,
#                     "udt_type_script": udt,
#                 }
#             )
#             break
#         except Exception as e:
#             print(e)
#             print(f"send try count: {i}")
#             time.sleep(1)
#             continue
#     for i in range(wait_times):
#         time.sleep(1)
#         try:
#             payment = fiber1.get_payment({"payment_hash": payment["payment_hash"]})
#             if payment["status"] == "Failed":
#                 return send_payment(fiber1, fiber2, amount, udt, wait_times - i)
#             if payment["status"] == "Success":
#                 print("payment success")
#                 return payment
#         except Exception as e:
#             print(e)
#             print(f"wait try count: {i}")
#             continue
#     raise TimeoutError("payment timeout")
