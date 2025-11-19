import time

import pytest

from framework.basic_fiber import FiberTest
from framework.config import DEFAULT_MIN_DEPOSIT_CKB


class TestHopHint(FiberTest):  # a-b
    # FiberTest.debug = True

    def test_not_hophit_simple(self):
        """
        b-c-d-私-a
        1.b-a(不通过hophit应该发送失败)
        Returns:

        """
        self.start_new_fiber(self.generate_account(10000))  # c
        self.start_new_fiber(self.generate_account(10000))  # d

        fiber1_balance = 1000 * 100000000
        fiber1_fee = 1000
        self.open_channel(self.fibers[1], self.fibers[2], 1000 * 100000000, 1)  # b-c
        self.open_channel(
            self.fibers[2], self.fibers[3], 1000 * 100000000, 1
        )  # c-d == b-c-d

        self.fibers[3].connect_peer(self.fibers[0])  # d-a
        time.sleep(1)
        self.fibers[3].get_client().open_channel(  # d -a private channel
            {
                "peer_id": self.fibers[0].get_peer_id(),
                "funding_amount": hex(fiber1_balance + DEFAULT_MIN_DEPOSIT_CKB),
                "tlc_fee_proportional_millionths": hex(fiber1_fee),
                "public": False,
            }
        )
        time.sleep(1)
        self.wait_for_channel_state(
            self.fibers[3].get_client(), self.fibers[0].get_peer_id(), "CHANNEL_READY"
        )

        # b-a(不通过hophit应该发送失败)

        try:
            self.send_payment(self.fibers[1], self.fibers[0], 1 * 100000000)
        except Exception as e:
            error_message = str(e)
            assert (
                error_message
                == "Error: Send payment error: Failed to build route, PathFind error: no path found"
            ), f"Unexpected error message: {error_message}"

    def test_not_hophit(self):
        """
        a-私-b-c-d-私-a
        路径选择
        1. b-a，预期是能成功
        2. 如果走的是b-c-d-a，则需要走hint才可以成功，预期是失败(大概率走这个路径)
        Returns:

        """
        self.start_new_fiber(self.generate_account(10000))  # c
        self.start_new_fiber(self.generate_account(10000))  # d

        fiber1_balance = 1000 * 100000000
        fiber1_fee = 1000
        self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(fiber1_balance + DEFAULT_MIN_DEPOSIT_CKB),
                "tlc_fee_proportional_millionths": hex(fiber1_fee),
                "public": False,
            }
        )  # a-b private channel
        time.sleep(1)
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY"
        )
        self.open_channel(self.fibers[1], self.fibers[2], 1000 * 100000000, 1)  # b-c
        self.open_channel(
            self.fibers[2], self.fibers[3], 1000 * 100000000, 1
        )  # c-d == b-c-d

        self.fibers[3].connect_peer(self.fibers[0])  # d-a
        time.sleep(1)
        self.fibers[3].get_client().open_channel(  # d -a private channel
            {
                "peer_id": self.fibers[0].get_peer_id(),
                "funding_amount": hex(fiber1_balance + DEFAULT_MIN_DEPOSIT_CKB),
                "tlc_fee_proportional_millionths": hex(fiber1_fee),
                "public": False,
            }
        )
        time.sleep(1)
        self.wait_for_channel_state(
            self.fibers[3].get_client(), self.fibers[0].get_peer_id(), "CHANNEL_READY"
        )

        try:
            # 走b-a会成功
            payment = (
                self.fibers[1]
                .get_client()
                .send_payment(  # b
                    {
                        "target_pubkey": self.fibers[0]
                        .get_client()
                        .node_info()["node_id"],
                        "amount": hex(10 * 100000000),
                        "keysend": True,
                    }
                )
            )
            print(f"debug payment content:{payment}")

        except Exception as e:
            # 如果走的是b-c-d-a，不通过hophit应该发送失败
            error_message = str(e)
            assert (
                error_message
                == "Error: Send payment error: Failed to build route, PathFind error: no path found"
            ), f"Unexpected error message: {error_message}"

    def test_use_hophit(self):
        """
        a-私-b-c-d-私-a
        1. b-a(通过hophit应该发送成功)
        Returns:
        """
        self.start_new_fiber(self.generate_account(10000))  # c
        self.start_new_fiber(self.generate_account(10000))  # d

        fiber1_balance = 1000 * 100000000
        fiber1_fee = 1000
        self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(fiber1_balance + DEFAULT_MIN_DEPOSIT_CKB),
                "tlc_fee_proportional_millionths": hex(fiber1_fee),
                "public": False,
            }
        )  # a-b private channel
        time.sleep(1)
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY"
        )
        self.open_channel(self.fibers[1], self.fibers[2], 1000 * 100000000, 1)  # b-c
        self.open_channel(
            self.fibers[2], self.fibers[3], 1000 * 100000000, 1
        )  # c-d == b-c-d

        self.fibers[3].connect_peer(self.fibers[0])  # d-a
        time.sleep(1)
        self.fibers[3].get_client().open_channel(  # d -a private channel
            {
                "peer_id": self.fibers[0].get_peer_id(),
                "funding_amount": hex(fiber1_balance + DEFAULT_MIN_DEPOSIT_CKB),
                "tlc_fee_proportional_millionths": hex(fiber1_fee),
                "public": False,
            }
        )
        time.sleep(1)
        self.wait_for_channel_state(
            self.fibers[3].get_client(), self.fibers[0].get_peer_id(), "CHANNEL_READY"
        )
        # 查看d-a的channeloutpoint
        print(f"a peer_id:{self.fibers[0].get_peer_id()}")
        print(f"d peer_id:{self.fibers[3].get_peer_id()}")
        channels = (
            self.fibers[3]
            .get_client()
            .list_channels({"peer_id": self.fibers[0].get_peer_id()})
        )
        print(
            f"d-a,channel:{channels}"
        )  # {'channels': [{'channel_id': '0xe59fc475a5e32bfd4130f5d7b73e2c77e94e40a1c4de0f4c4f7cb65a23cfa808', 'is_public': False, 'channel_outpoint': '0x7f2fc106cbc01d25e9682826ec131e67be8e9868fbb37edd6591bb7423feb21000000000', 'peer_id': 'QmT5SaY3CSSY9XvgoqJ521TXSUQ5DBZ58DTdafPKFBEcWf', 'funding_udt_type_script': None, 'state': {'state_name': 'CHANNEL_READY'}, 'local_balance': '0x174876e800', 'offered_tlc_balance': '0x0', 'remote_balance': '0x0', 'received_tlc_balance': '0x0', 'latest_commitment_transaction_hash': '0x93e75ade9b0016dbca0a56698fbf04f4a4ca5d8bc3c40fa781769e37ebb9fba2', 'created_at': '0x195d58690aa', 'enabled': True, 'tlc_expiry_delta': '0x5265c00', 'tlc_fee_proportional_millionths': '0x3e8'}, {'channel_id': '0xab117469b812d64410e1f4a6429475908f8672eda1a492772387880fd9046f07', 'is_public': False, 'channel_outpoint': '0xfe4f6fbfd2fb31ec9ca6dc7a4e43efa4e864002fae281c7c5e38fc51cd89465f00000000', 'peer_id': 'QmT5SaY3CSSY9XvgoqJ521TXSUQ5DBZ58DTdafPKFBEcWf', 'funding_udt_type_script': None, 'state': {'state_name': 'CHANNEL_READY'}, 'local_balance': '0x174876e800', 'offered_tlc_balance': '0x0', 'remote_balance': '0x0', 'received_tlc_balance': '0x0', 'latest_commitment_transaction_hash': '0xc6f38fe84030eba95376c63e76ccfa9605c07f5ca407e395ca53db609b305787', 'created_at': '0x195d2e09a4d', 'enabled': True, 'tlc_expiry_delta': '0x5265c00', 'tlc_fee_proportional_millionths': '0x3e8'}]}
        da_channel_outpoint = channels["channels"][0]["channel_outpoint"]
        print(f"d-a, channel_outpoint:{da_channel_outpoint}")
        # b-a,怎么填d-私-a的信息

        payment = (
            self.fibers[1]
            .get_client()
            .send_payment(  # b
                {
                    "target_pubkey": self.fibers[0].get_client().node_info()["node_id"],
                    "amount": hex(10 * 100000000),
                    "keysend": True,
                    "hop_hints": [
                        {
                            "pubkey": self.fibers[3]
                            .get_client()
                            .node_info()["node_id"],
                            # 填的是 d 的 pubkey，表示在 d 节点使用 channel_outpoint 到 a
                            "channel_outpoint": da_channel_outpoint,
                            "fee_rate": hex(1000),
                            "tlc_expiry_delta": hex(86400000),
                        }
                    ],
                }
            )
        )
        self.wait_payment_state(self.fibers[1], payment["payment_hash"], "Success", 120)

    def test_use_hophit_simple(self):
        """
        b-c-d-私-a
        1. b-a(通过hophit应该发送成功)
        Returns:
        """
        self.start_new_fiber(self.generate_account(10000))  # c
        self.start_new_fiber(self.generate_account(10000))  # d

        fiber1_balance = 1000 * 100000000
        fiber1_fee = 1000

        self.open_channel(self.fibers[1], self.fibers[2], 1000 * 100000000, 1)  # b-c
        self.open_channel(
            self.fibers[2], self.fibers[3], 1000 * 100000000, 1
        )  # c-d == b-c-d

        self.fibers[3].connect_peer(self.fibers[0])  # d-a
        time.sleep(1)
        self.fibers[3].get_client().open_channel(  # d -a private channel
            {
                "peer_id": self.fibers[0].get_peer_id(),
                "funding_amount": hex(fiber1_balance + DEFAULT_MIN_DEPOSIT_CKB),
                "tlc_fee_proportional_millionths": hex(fiber1_fee),
                "public": False,
            }
        )
        time.sleep(1)
        self.wait_for_channel_state(
            self.fibers[3].get_client(), self.fibers[0].get_peer_id(), "CHANNEL_READY"
        )
        # 查看d-a的channeloutpoint
        print(f"a peer_id:{self.fibers[0].get_peer_id()}")
        print(f"d peer_id:{self.fibers[3].get_peer_id()}")
        channels = (
            self.fibers[3]
            .get_client()
            .list_channels({"peer_id": self.fibers[0].get_peer_id()})
        )
        print(
            f"d-a,channel:{channels}"
        )  # {'channels': [{'channel_id': '0xe59fc475a5e32bfd4130f5d7b73e2c77e94e40a1c4de0f4c4f7cb65a23cfa808', 'is_public': False, 'channel_outpoint': '0x7f2fc106cbc01d25e9682826ec131e67be8e9868fbb37edd6591bb7423feb21000000000', 'peer_id': 'QmT5SaY3CSSY9XvgoqJ521TXSUQ5DBZ58DTdafPKFBEcWf', 'funding_udt_type_script': None, 'state': {'state_name': 'CHANNEL_READY'}, 'local_balance': '0x174876e800', 'offered_tlc_balance': '0x0', 'remote_balance': '0x0', 'received_tlc_balance': '0x0', 'latest_commitment_transaction_hash': '0x93e75ade9b0016dbca0a56698fbf04f4a4ca5d8bc3c40fa781769e37ebb9fba2', 'created_at': '0x195d58690aa', 'enabled': True, 'tlc_expiry_delta': '0x5265c00', 'tlc_fee_proportional_millionths': '0x3e8'}, {'channel_id': '0xab117469b812d64410e1f4a6429475908f8672eda1a492772387880fd9046f07', 'is_public': False, 'channel_outpoint': '0xfe4f6fbfd2fb31ec9ca6dc7a4e43efa4e864002fae281c7c5e38fc51cd89465f00000000', 'peer_id': 'QmT5SaY3CSSY9XvgoqJ521TXSUQ5DBZ58DTdafPKFBEcWf', 'funding_udt_type_script': None, 'state': {'state_name': 'CHANNEL_READY'}, 'local_balance': '0x174876e800', 'offered_tlc_balance': '0x0', 'remote_balance': '0x0', 'received_tlc_balance': '0x0', 'latest_commitment_transaction_hash': '0xc6f38fe84030eba95376c63e76ccfa9605c07f5ca407e395ca53db609b305787', 'created_at': '0x195d2e09a4d', 'enabled': True, 'tlc_expiry_delta': '0x5265c00', 'tlc_fee_proportional_millionths': '0x3e8'}]}
        da_channel_outpoint = channels["channels"][0]["channel_outpoint"]
        print(f"d-a, channel_outpoint:{da_channel_outpoint}")
        # b-a,怎么填d-私-a的信息

        payment = (
            self.fibers[1]
            .get_client()
            .send_payment(  # b
                {
                    "target_pubkey": self.fibers[0].get_client().node_info()["node_id"],
                    "amount": hex(10 * 100000000),
                    "keysend": True,
                    "hop_hints": [
                        {
                            "pubkey": self.fibers[3]
                            .get_client()
                            .node_info()[
                                "node_id"
                            ],  # 填的是 d 的 pubkey，表示在 d 节点使用 channel_outpoint 到 a
                            "channel_outpoint": da_channel_outpoint,
                            "fee_rate": hex(1000),
                            "tlc_expiry_delta": hex(86400000),
                        }
                    ],
                }
            )
        )
        self.wait_payment_state(self.fibers[1], payment["payment_hash"], "Success", 120)
