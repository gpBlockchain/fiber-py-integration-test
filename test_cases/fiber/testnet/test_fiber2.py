import time

from framework.basic import CkbTest
from framework.fiber_rpc import FiberRPCClient
from framework.test_fiber import Fiber, FiberConfigPath


class TestFiber2(CkbTest):
    ACCOUNT_PRIVATE_1 = (
        "0xaae4515b745efcd6f00c1b40aaeef3dd66c82d75f8f43d0f18e1a1eecb90ada4"
    )
    ACCOUNT_PRIVATE_2 = (
        "0x518d76bbfe5ffe3a8ef3ad486e784ec333749575fb3c697126cdaa8084d42532"
    )
    fiber1: Fiber
    fiber2: Fiber

    @classmethod
    def setup_class(cls):
        print("\nSetup TestClass2")
        cls.fiber1 = Fiber.init_by_port(
            FiberConfigPath.CURRENT_TESTNET,
            cls.ACCOUNT_PRIVATE_1,
            "fiber/node1",
            "8228",
            "8227",
        )

        cls.fiber2 = Fiber.init_by_port(
            FiberConfigPath.CURRENT_TESTNET,
            cls.ACCOUNT_PRIVATE_2,
            "fiber/node2",
            "8229",
            "8230",
        )

        cls.fiber1.prepare()
        cls.fiber1.start()

        cls.fiber2.prepare()
        cls.fiber2.start()
        cls.fiber1.connect_peer(cls.fiber2)
        time.sleep(10)

    def test_list_peer(self):
        self.fiber1.get_client().node_info()

    def test_oooo(self):
        # self.fiber1.get_client().connect_peer({
        #     "address": "/ip4/18.162.235.225/tcp/8119/p2p/QmXen3eUHhywmutEzydCsW4hXBoeVmdET2FJvMX69XJ1Eo"
        # })
        # self.fiber1.get_client().list_peers()
        # self.fiber1.get_client().node_info()
        temporary_channel_id = self.fiber1.get_client().open_channel(
            {
                "peer_id": "QmXen3eUHhywmutEzydCsW4hXBoeVmdET2FJvMX69XJ1Eo",
                "funding_amount": hex(500 * 100000000),
                "public": True,
                # "tlc_fee_proportional_millionths": "0x4B0",
            }
        )

    def test_shutdown(self):
        channels = self.fiber1.get_client().list_channels({})
        for i in range(len(channels["channels"])):
            channel = channels["channels"][i]
            if channel["state"]["state_name"] != "CHANNEL_READY":
                continue
            self.fiber1.get_client().shutdown_channel(
                {
                    "channel_id": channel["channel_id"],
                    "close_script": {
                        "code_hash": "0x9bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8",
                        "hash_type": "type",
                        "args": self.fiber1.get_account()["lock_arg"],
                    },
                    "fee_rate": "0x3FC",
                }
            )

    def test_open_channel(self):
        temporary_channel_id = self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(500 * 100000000),
                "public": True,
                # "tlc_fee_proportional_millionths": "0x4B0",
            }
        )
        wait_for_channel_state(
            self.fiber1.get_client(),
            self.fiber2.get_peer_id(),
            "CHANNEL_READY",
            120,
        )
        send_payment(
            self.fiber1.get_client(),
            self.fiber2.get_client(),
            100000,
            None,
            20 * 60,
        )


def wait_for_channel_state(client, peer_id, expected_state, timeout=120):
    """Wait for a channel to reach a specific state."""
    for _ in range(timeout):
        channels = client.list_channels({"peer_id": peer_id, "include_closed": True})
        if channels["channels"][0]["state"]["state_name"] == expected_state:
            print(f"Channel reached expected state: {expected_state}")
            return channels["channels"][0]["channel_id"]
        print(
            f"Waiting for channel state: {expected_state}, current state: {channels['channels'][0]['state']['state_name']}"
        )
        time.sleep(1)
    raise TimeoutError(
        f"Channel did not reach state {expected_state} within timeout period."
    )


def send_payment(
    fiber1: FiberRPCClient, fiber2: FiberRPCClient, amount, udt=None, wait_times=300
):
    try_times = 0
    payment = None
    for i in range(wait_times):
        try:
            payment = fiber1.send_payment(
                {
                    "amount": hex(amount),
                    "target_pubkey": fiber2.node_info()["node_id"],
                    "keysend": True,
                    "udt_type_script": udt,
                }
            )
            break
        except Exception as e:
            print(e)
            print(f"send try count: {i}")
            time.sleep(1)
            continue
    for i in range(wait_times):
        time.sleep(1)
        try:
            payment = fiber1.get_payment({"payment_hash": payment["payment_hash"]})
            if payment["status"] == "Failed":
                return send_payment(fiber1, fiber2, amount, udt, wait_times - i)
            if payment["status"] == "Success":
                print("payment success")
                return payment
        except Exception as e:
            print(e)
            print(f"wait try count: {i}")
            continue
    raise TimeoutError("payment timeout")
