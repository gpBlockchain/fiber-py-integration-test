import time

import pytest

from framework.basic_fiber import FiberTest


class TestRestart(FiberTest):
    """
    发送过程中
        1. 中间节点重启 send_payment
        2. 我方节点重启 send_payment
        3. 对方节点重启 send_payment

    重启后
        1. 中间节点重启后，发送 send_payment
        2. 我方节点重启后， 发送 send_payment
        3. 对方节点重启后， 发送 send_payment
    """

    # FiberTest.debug = True

    # @pytest.mark.skip("https://github.com/nervosnetwork/fiber/issues/334")
    def test_restart_node_send_payment_key_send(self):
        """
        1. 发送端重启，send_payment
        2. 中间节点重启 send_payment
        3. 最终节点重启 send_payment
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
        # N1N2_CHANNEL_ID = channels["channels"][0]["channel_id"]
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

        # 1. 发送端重启，send_payment
        self.fiber1.stop()
        self.fiber1.start()
        self.fiber1.connect_peer(self.fiber2)
        self.fiber1.connect_peer(self.fiber3)
        time.sleep(5)
        node_info = self.fiber1.get_client().node_info()
        assert int(node_info["peers_count"], 16) >= 1
        node3_info = self.fiber3.get_client().node_info()
        fiber3_pub = node3_info["node_id"]
        payment = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": fiber3_pub,
                "amount": hex(10 * 100000000),
                "keysend": True,
                # "invoice": "0x123",
            }
        )
        self.wait_payment_state(self.fiber1, payment["payment_hash"], "Success", 120)
        channels = self.fiber3.get_client().list_channels({})
        assert channels["channels"][0]["local_balance"] == hex(10 * 100000000)
        # 2. 中间节点2重启 send_payment
        self.fiber2.stop()
        self.fiber2.start()
        self.fiber2.connect_peer(self.fiber1)
        self.fiber2.connect_peer(self.fiber3)
        time.sleep(5)
        node_info = self.fiber2.get_client().node_info()
        assert node_info["peers_count"] == "0x2"
        payment = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": fiber3_pub,
                "amount": hex(10 * 100000000),
                "keysend": True,
                # "invoice": "0x123",
            }
        )
        self.wait_payment_state(self.fiber1, payment["payment_hash"], "Success", 120)
        channels = self.fiber3.get_client().list_channels({})
        assert channels["channels"][0]["local_balance"] == hex(20 * 100000000)

        # 3. 最终节点重启 send_payment
        self.fiber3.stop()
        self.fiber3.start()
        self.fiber3.connect_peer(self.fiber2)
        self.fiber3.connect_peer(self.fiber1)
        time.sleep(5)
        node_info = self.fiber3.get_client().node_info()
        assert int(node_info["peers_count"], 16) >= 1
        payment = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": fiber3_pub,
                "amount": hex(10 * 100000000),
                "keysend": True,
                # "invoice": "0x123",
            }
        )
        self.wait_payment_state(self.fiber1, payment["payment_hash"], "Success", 120)
        channels = self.fiber3.get_client().list_channels({})
        assert channels["channels"][0]["local_balance"] == hex(30 * 100000000)

    def test_restart_node_send_payment_invoice(self):
        """
        1. 发送端重启，send_payment
        2. 中间节点重启 send_payment
        3. 最终节点重启 send_payment
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
        # N1N2_CHANNEL_ID = channels["channels"][0]["channel_id"]
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

        # 1. 发送端重启，send_payment
        self.fiber1.stop()
        self.fiber1.start()
        self.fiber1.connect_peer(self.fiber2)
        self.fiber1.connect_peer(self.fiber3)
        time.sleep(3)
        node_info = self.fiber1.get_client().node_info()
        assert int(node_info["peers_count"], 16) >= 1
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
        # 2. 中间节点2重启 send_payment
        self.fiber2.stop()
        self.fiber2.start()
        self.fiber2.connect_peer(self.fiber1)
        self.fiber2.connect_peer(self.fiber3)
        time.sleep(3)
        node_info = self.fiber2.get_client().node_info()
        assert node_info["peers_count"] == "0x2"
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
        assert channels["channels"][0]["local_balance"] == hex(20 * 100000000)

        # 3. 最终节点重启 send_payment
        self.fiber3.stop()
        self.fiber3.start()
        self.fiber3.connect_peer(self.fiber2)
        self.fiber3.connect_peer(self.fiber1)
        time.sleep(3)
        node_info = self.fiber3.get_client().node_info()
        assert int(node_info["peers_count"], 16) >= 1
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
        assert channels["channels"][0]["local_balance"] == hex(30 * 100000000)

    @pytest.mark.skip("https://github.com/nervosnetwork/fiber/issues/696")
    def test_send_stop(self):
        account3_private = self.generate_account(1000)
        self.fiber3 = self.start_new_fiber(account3_private)
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1000 * 100000000)
        self.open_channel(self.fiber2, self.fiber3, 1000 * 100000000, 1000 * 100000000)

        self.send_payment(self.fiber1, self.fiber3, 1)
        self.fiber3.stop()
        self.fiber3.start()
        self.fiber3.connect_peer(self.fiber2)
        self.fiber3.connect_peer(self.fiber1)
        self.send_payment(self.fiber1, self.fiber3, 2)

    @pytest.mark.skip("https://github.com/nervosnetwork/fiber/issues/363")
    def test_restart_when_node_send_payment(self):
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
        # N1N2_CHANNEL_ID = channels["channels"][0]["channel_id"]
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

        # 1. 发送端重启，send_payment

        node_info = self.fiber1.get_client().node_info()
        assert int(node_info["peers_count"], 16) >= 1
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
        self.fiber1.stop()
        invoice = self.fiber3.get_client().get_invoice(
            {"payment_hash": payment["payment_hash"]}
        )
        print("self.fiber1.stop() payment:", invoice)
        channels = self.fiber2.get_client().list_channels({})
        print("channels:", channels)
        channels = self.fiber3.get_client().list_channels({})
        print("channels:", channels)
        # todo 如果 fiber1 不启动了
        # todo check fiber2 钱是否会丢
        self.fiber1.start()
        self.fiber1.connect_peer(self.fiber2)
        self.fiber1.connect_peer(self.fiber3)

        self.wait_payment_state(self.fiber1, payment["payment_hash"], "Success", 120)
        channels = self.fiber3.get_client().list_channels({})
        assert channels["channels"][0]["local_balance"] == hex(10 * 100000000)
        # 2. 中间节点2重启 send_payment

        node_info = self.fiber2.get_client().node_info()
        assert node_info["peers_count"] == "0x2"
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
        time.sleep(5)
        payment = self.fiber1.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
            }
        )
        self.fiber2.stop()
        payment = self.fiber1.get_client().get_payment(
            {"payment_hash": payment["payment_hash"]}
        )
        invoice = self.fiber3.get_client().get_invoice(
            {"payment_hash": payment["payment_hash"]}
        )
        print("self.fiber2.stop() payment:", payment)
        print("self.fiber2.stop() invoice:", invoice)
        channelsN12 = self.fiber1.get_client().list_channels({})
        channelsN23 = self.fiber3.get_client().list_channels({})
        print("fiber1 list channels:", channelsN12)
        print("fiber2 list channels:", channelsN23)

        self.fiber2.start()
        self.fiber2.connect_peer(self.fiber1)
        self.fiber2.connect_peer(self.fiber3)
        self.wait_payment_state(self.fiber1, payment["payment_hash"], "Success", 120)
        channels = self.fiber3.get_client().list_channels({})
        assert channels["channels"][0]["local_balance"] == hex(20 * 100000000)

        # 3. 最终节点重启 send_payment

        node_info = self.fiber3.get_client().node_info()
        assert int(node_info["peers_count"], 16) >= 1
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
        time.sleep(5)
        payment = self.fiber1.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
            }
        )
        self.fiber3.stop()
        payment = self.fiber1.get_client().get_payment(
            {"payment_hash": payment["payment_hash"]}
        )
        print("self.fiber3.stop() payment:", payment)
        self.fiber3.start()
        self.fiber3.connect_peer(self.fiber2)
        self.fiber3.connect_peer(self.fiber1)
        self.wait_payment_finished(self.fiber1, payment["payment_hash"], 120)
        self.send_payment(self.fiber1, self.fiber2, 1)
