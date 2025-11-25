# import time
#
# from framework.basic import CkbTest
# from framework.fiber_rpc import FiberRPCClient
# from framework.test_fiber import Fiber, FiberConfigPath
#
#
# class TestFiber2(CkbTest):
#     cryptapeFiber1 = FiberRPCClient("http://18.163.221.211:8227")
#     cryptapeFiber2 = FiberRPCClient("http://18.162.235.225:8227")
#
#     ACCOUNT_PRIVATE_1 = (
#         "0xaae4515b745efcd6f00c1b40aaeef3dd66c82d75f8f43d0f18e1a1eecb90ada4"
#     )
#     ACCOUNT_PRIVATE_2 = (
#         "0x518d76bbfe5ffe3a8ef3ad486e784ec333749575fb3c697126cdaa8084d42532"
#     )
#     fiber1: Fiber
#     fiber2: Fiber
#
#     @classmethod
#     def setup_class(cls):
#         print("\nSetup TestClass2")
#         cls.fiber1 = Fiber.init_by_port(
#             FiberConfigPath.CURRENT_TESTNET,
#             cls.ACCOUNT_PRIVATE_1,
#             "fiber/node1",
#             "8228",
#             "8227",
#         )
#
#         # cls.fiber1.prepare()
#         # cls.fiber1.start()
#         #
#         # cls.fiber1.get_client().connect_peer(
#         #     {"address": cls.cryptapeFiber1.node_info()["addresses"][0]}
#         # )
#
#         # time.sleep(10)
#
#     def test_lll(self):
#         self.fiber1.stop()
#         self.fiber1.start()
#         # self.fiber1.get_client().list_peers()
#         # self.fiber1.start()
#         # self.fiber1.get_client().list_peers()
#         # self.fiber1.get_client().list_channels({})
#         # self.fiber1.get_client().disconnect_peer(
#         #     {"peer_id": self.cryptapeFiber1.get_peer_id()}
#         # )
#         # send_payment(
#         #     self.fiber1.get_client(),
#         #     self.cryptapeFiber1,
#         #     100000,
#         #     None,
#         #     20 * 60,
#         # )
#
#     def test_0000(self):
#         self.fiber1.get_client().open_channel(
#             {
#                 "peer_id": self.cryptapeFiber1.get_peer_id(),
#                 "funding_amount": hex(500 * 100000000),
#                 "public": True,
#                 # "tlc_fee_proportional_millionths": "0x4B0",
#             }
#         )
#         wait_for_channel_state(
#             self.fiber1.get_client(),
#             self.cryptapeFiber1.get_peer_id(),
#             "CHANNEL_READY",
#             120,
#         )
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
