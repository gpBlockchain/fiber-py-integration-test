"""
todo:
https://github.com/nervosnetwork/fiber/pull/1335
A. 当前 PR 已覆盖：MPP + 一个 split force-close
B. MPP + 两个 split 都 force-close
C. MPP + 一个 split force-close，一个 split off-chain settle，节点重启
D. 非 MPP 普通 forwarding 支付回归
E. 非 MPP + force-close forwarding channel
F. 同 payment_hash 多 channel 混合状态
    1. 多channel 有相同的payment_hash tlc
    2. 多channel ,一个channel里有N个相同的payment_hash
"""

import time

from framework.basic_fiber import FiberTest
from framework.util import ckb_hash


class TestMPPWithSamePaymentHash(FiberTest):

    def _wait_force_close_unlock(self, timeout=600):
        if len(self.get_commit_cells()) == 0:
            raise Exception("No commit cells found")
        self.node.getClient().generate_epochs("0x1", wait_time=0)
        for _ in range(timeout // 10):
            if len(self.get_commit_cells()) == 0:
                return
            time.sleep(10)
        assert len(self.get_commit_cells()) == 0

    def test_mpp_with_same_payment_hash(self):
        """
        Test that the forwarding node correctly handles multiple TLCs with the same payment_hash across channels.

        Steps:
        1. Open multiple channels between the same two nodes.
        2. Create multiple MPP payments with the same payment_hash across these channels.
        3. Force-close one of the channels and ensure that the preimage is retained until the on-chain split is settled.
        4. Assert that the other splits can still be fulfilled off-chain without issues.

        Returns:
        """
        fiber3 = self.start_new_fiber(self.generate_account(10000))
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0)
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0)
        self.open_channel(self.fiber2, fiber3, 3000 * 100000000, 0)
        self.wait_graph_channels_sync(self.fiber1, 3)
        self.wait_graph_channels_sync(self.fiber2, 3)
        self.wait_graph_channels_sync(fiber3, 3)
        time.sleep(2)

        preimage = self.generate_random_preimage()
        payment_hash = ckb_hash(preimage)
        invoice = fiber3.get_client().new_invoice(
            {
                "amount": hex(1500 * 100000000),
                "currency": "Fibd",
                "description": "mpp force close hold invoice",
                "payment_hash": payment_hash,
                "allow_mpp": True,
            }
        )
        payment = self.fiber1.get_client().send_payment(
            {"invoice": invoice["invoice_address"], "max_parts": hex(2)}
        )
        assert payment["payment_hash"] == payment_hash
        self.wait_payment_state(self.fiber1, payment_hash, "Inflight")
        self.wait_invoice_state(fiber3, payment_hash, "Received")
        channels = self.fiber1.get_client().list_channels({})
        force_closed_channel = channels["channels"][0]["channel_id"]
        offchain_channel = channels["channels"][1]["channel_id"]

        self.fiber2.get_client().shutdown_channel(
            {"channel_id": force_closed_channel, "force": True}
        )
        pending_tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, pending_tx_hash)
        fiber3.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )
        self._wait_force_close_unlock()

    def test_mpp_with_same_payment_hash_2(self):
        """
        Test that the forwarding node correctly handles multiple TLCs with the same payment_hash across channels.

        Steps:
        1. Open multiple channels between the same two nodes.
        2. Create multiple MPP payments with the same payment_hash across these channels.
        3. Force-close one of the channels and ensure that the preimage is retained until the on-chain split is settled.
        4. Assert that the other splits can still be fulfilled off-chain without issues.

        Returns:
        """
        fiber3 = self.start_new_fiber(self.generate_account(10000))
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0)
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0)
        self.open_channel(self.fiber2, fiber3, 3000 * 100000000, 0)
        self.wait_graph_channels_sync(self.fiber1, 3)
        self.wait_graph_channels_sync(self.fiber2, 3)
        self.wait_graph_channels_sync(fiber3, 3)
        time.sleep(2)

        preimage = self.generate_random_preimage()
        payment_hash = ckb_hash(preimage)
        invoice = fiber3.get_client().new_invoice(
            {
                "amount": hex(1500 * 100000000),
                "currency": "Fibd",
                "description": "mpp force close hold invoice",
                "payment_hash": payment_hash,
                "allow_mpp": True,
            }
        )
        payment = self.fiber1.get_client().send_payment(
            {"invoice": invoice["invoice_address"], "max_parts": hex(2)}
        )
        assert payment["payment_hash"] == payment_hash
        self.wait_payment_state(self.fiber1, payment_hash, "Inflight")
        self.wait_invoice_state(fiber3, payment_hash, "Received")
        channels = self.fiber1.get_client().list_channels({})
        force_closed_channel = channels["channels"][0]["channel_id"]
        offchain_channel = channels["channels"][1]["channel_id"]

        self.fiber1.get_client().shutdown_channel(
            {"channel_id": force_closed_channel, "force": True}
        )
        pending_tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, pending_tx_hash)
        fiber3.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )
        self._wait_force_close_unlock()
