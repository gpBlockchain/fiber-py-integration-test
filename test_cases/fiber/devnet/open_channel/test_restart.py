import time
from os import wait3

import pytest
from framework.basic_fiber import FiberTest


class TestRestart(FiberTest):
    """
    open channel过程中,状态为非ready的状态
        1. 我方节点重启 open channel
        2. 对方节点重启 open channel
        3. ckb节点重启
    """

    # FiberTest.debug = True
    # @pytest.mark.skip("https://github.com/nervosnetwork/fiber/issues/402")
    def test_restart_fiber_node_open_channel(self):
        """
        fiber1 <-> fiber2 <-> fiber3
        1. 我方节点重启：发送 open channel 过程中fiber1重启
        2. 对方节点重启：发送 open channel 过程中fiber3重启
        Returns:
        """
        account3_private = self.generate_account(1000)
        self.fiber3 = self.start_new_fiber(account3_private)
        self.fiber3.connect_peer(self.fiber2)

        # open channel for fiber1 fiber2
        self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(200 * 100000000),
                "public": True,
            }
        )
        # channels = self.fiber1.get_client().list_channels({})
        # N1N2_CHANNEL_ID = channels["channels"][0]
        # 1. 我方节点重启：发送open channel过程中fiber1重启
        self.wait_for_channel_state(
            self.fiber1.get_client(),
            self.fiber2.get_peer_id(),
            {
                "state_name": "AWAITING_TX_SIGNATURES",
                "state_flags": "OUR_TX_SIGNATURES_SENT | THEIR_TX_SIGNATURES_SENT",
            },
            120,
        )
        self.fiber1.stop()
        self.fiber1.start()
        self.fiber2.stop()
        self.fiber2.start()
        time.sleep(3)
        node_info = self.fiber1.get_client().node_info()
        assert int(node_info["peers_count"], 16) >= 1
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY", 120
        )
        channels = self.fiber1.get_client().list_channels({})
        print(f"after restart query channel info:{channels}")
        assert channels["channels"][0]["state"]["state_name"] == "CHANNEL_READY"
        # open channel for fiber 2 fiber3
        self.fiber2.get_client().open_channel(
            {
                "peer_id": self.fiber3.get_peer_id(),
                "funding_amount": hex(200 * 100000000),
                "public": True,
            }
        )
        # 2.对方节点重启：发送openchannel过程中fiber3重启
        self.fiber3.stop()
        self.fiber3.start()
        time.sleep(3)
        node_info = self.fiber2.get_client().node_info()
        assert int(node_info["peers_count"], 16) >= 1
        self.wait_for_channel_state(
            self.fiber2.get_client(), self.fiber3.get_peer_id(), "CHANNEL_READY", 120
        )
        channels = self.fiber3.get_client().list_channels({})
        print(f"after restart query channel info:{channels}")
        assert channels["channels"][0]["state"]["state_name"] == "CHANNEL_READY"

    # @pytest.mark.skip("https://github.com/nervosnetwork/fiber/issues/402")
    def test_restart_ckb_node_openchannel(self):
        """
        1. ckb节点重启：发送openchannel过程中ckb节点重启
        Returns:
        """
        account3_private = self.generate_account(1000)
        self.fiber3 = self.start_new_fiber(account3_private)
        self.fiber3.connect_peer(self.fiber2)

        # open channel for fiber1 fiber2
        self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(200 * 100000000),
                "public": True,
            }
        )
        # channels = self.fiber1.get_client().list_channels({})
        # N1N2_CHANNEL_ID = channels["channels"][0]
        # 1. ckb节点重启：发送openchannel过程中ckb节点重启
        self.node.stop()
        self.node.start()
        for i in range(20):
            self.Miner.miner_with_version(self.node, "0x0")
        time.sleep(3)
        node_info = self.fiber1.get_client().node_info()
        assert int(node_info["peers_count"], 16) >= 1
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY", 120
        )
        channels = self.fiber1.get_client().list_channels({})
        print(f"after restart query channel info:{channels}")
        assert channels["channels"][0]["state"]["state_name"] == "CHANNEL_READY"

    def test_restart_channel_ready(self):
        """
        1、ready的状态后重启fiber节点
        2、ready的状态后重启ckb节点
        3、再观察重启后生成invoice和send payment能否正常发送，检查通道有效
        """
        account3_private = self.generate_account(1000)
        self.fiber3 = self.start_new_fiber(account3_private)
        self.fiber3.connect_peer(self.fiber2)

        # open channel for fiber1 fiber2
        self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(200 * 100000000),
                "public": True,
            }
        )
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY", 120
        )
        # open channel for fiber 2 fiber3
        self.fiber2.get_client().open_channel(
            {
                "peer_id": self.fiber3.get_peer_id(),
                "funding_amount": hex(200 * 100000000),
                "public": True,
            }
        )
        self.wait_for_channel_state(
            self.fiber2.get_client(), self.fiber3.get_peer_id(), "CHANNEL_READY", 120
        )
        channels = self.fiber1.get_client().list_channels({})
        print(f"before restart query channel info:{channels}")
        # 1、ready的状态后重启发送端节点，观察channel的状态是否会变
        self.fiber1.stop()
        self.fiber1.start()
        # todo 目前重启可能连不上对方节点，不是很稳定
        self.fiber1.connect_peer(self.fiber2)
        self.fiber2.connect_peer(self.fiber3)
        time.sleep(5)
        node_info = self.fiber2.get_client().node_info()
        print(f"node info detail:{node_info}")
        assert int(node_info["peers_count"], 16) == 2
        channels = self.fiber1.get_client().list_channels({})
        print(f"after restart query channel info:{channels}")
        assert channels["channels"][0]["state"]["state_name"] == "CHANNEL_READY"
        # 2、ready的状态后重启接收端端节点，观察channel的状态是否会变
        self.fiber3.stop()
        self.fiber3.start()
        # todo 目前重启可能连不上对方节点，不是很稳定
        self.fiber3.connect_peer(self.fiber1)
        self.fiber3.connect_peer(self.fiber2)
        time.sleep(3)
        node_info = self.fiber2.get_client().node_info()
        print(f"node info detail:{node_info}")
        assert int(node_info["peers_count"], 16) == 2
        channels = self.fiber3.get_client().list_channels({})
        print(f"after restart query channel info:{channels}")
        assert channels["channels"][0]["state"]["state_name"] == "CHANNEL_READY"
        # 3、重启下ckb节点
        self.node.stop()
        self.node.start()
        for i in range(20):
            self.Miner.miner_with_version(self.node, "0x0")
        time.sleep(3)
        node_info = self.fiber1.get_client().node_info()
        assert int(node_info["peers_count"], 16) >= 1
        channels = self.fiber1.get_client().list_channels({})
        print(f"after restart query channel info:{channels}")
        assert channels["channels"][0]["state"]["state_name"] == "CHANNEL_READY"
        # 4、再观察重启后生成invoice和sendpayment能否正常发送，检查通道有效
        invoice = self.fiber3.get_client().new_invoice(
            {
                "amount": hex(10 * 100000000),
                "currency": "Fibd",
                "description": "test invoice generated by node2",
                "expiry": "0xe10",
                "final_cltv": "0x28",
                "payment_preimage": self.generate_random_preimage(),
                "hash_algorithm": "sha256",
            }
        )
        time.sleep(1)
        payment = self.fiber1.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
            }
        )
        self.wait_payment_state(self.fiber1, payment["payment_hash"], "Success", 120)
        channels = self.fiber3.get_client().list_channels({})
        assert channels["channels"][0]["local_balance"] == hex(10 * 100000000)

    def test_restart_with_open_n_channel(self):
        for i in range(5):
            self.start_new_fiber(self.generate_account(100000))
            # self.start_new_mock_fiber("")
        for i in range(1, 7):
            self.fiber1.connect_peer(self.fibers[i])
        time.sleep(1)
        for j in range(500):
            for i in range(1, 7):
                self.fibers[i].get_client().open_channel(
                    {
                        "peer_id": self.fiber1.get_peer_id(),
                        "funding_amount": hex(1000 * 100000000),
                        "public": True,
                    }
                )
            time.sleep(0.1)
        self.fiber1.stop()
        self.fiber1.start()
        time.sleep(30)
        for fiber in self.fibers:
            channels = fiber.get_client().list_channels({})
            for channel in channels["channels"]:
                print(channel["state"]["state_name"])
                assert channel["state"]["state_name"] == "CHANNEL_READY"
