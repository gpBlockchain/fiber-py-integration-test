import time

from framework.basic_fiber import FiberTest

CKB = 100000000


class TestPR1440FundingRestart(FiberTest):
    """PR-1440: funding tx committed during opener restart must not abort."""

    def _wait_for_pending_pool_tx(self, timeout=30):
        for _ in range(timeout * 5):
            pool = self.node.getClient().get_raw_tx_pool()
            pending_txs = pool.get("pending", [])
            if pending_txs:
                tx_hash = pending_txs[0]
                tx = self.node.getClient().get_transaction(tx_hash)
                status = tx["tx_status"]["status"]
                if status in ("pending", "proposed"):
                    return tx_hash
            time.sleep(0.2)
        assert False, "funding transaction did not enter the CKB tx pool"

    def _wait_channel_ready_on_both_sides(self):
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_pubkey(), "ChannelReady", 120
        )
        self.wait_for_channel_state(
            self.fiber2.get_client(), self.fiber1.get_pubkey(), "ChannelReady", 120
        )

    def _assert_no_closed_channel(self, fiber, peer):
        channels = fiber.get_client().list_channels(
            {"pubkey": peer.get_pubkey(), "include_closed": True}
        )
        assert len(channels["channels"]) == 1
        assert channels["channels"][0]["state"]["state_name"] == "ChannelReady"

    def test_restart_after_funding_tx_committed_during_downtime(self):
        self.node.stop_miner()

        self.fiber1.get_client().open_channel(
            {
                "pubkey": self.fiber2.get_pubkey(),
                "funding_amount": hex(200 * CKB),
                "public": True,
            }
        )
        funding_tx_hash = self._wait_for_pending_pool_tx()

        self.fiber1.stop()
        tx = self.Miner.miner_until_tx_committed(self.node, funding_tx_hash)
        for i in range(5):
            self.Miner.miner_with_version(self.node, "0x0")
        assert tx["tx_status"]["status"] == "committed"

        self.fiber1.start(fnn_log_level=self.fnn_log_level)
        self.fiber1.connect_peer(self.fiber2)

        self._wait_channel_ready_on_both_sides()
        self._assert_no_closed_channel(self.fiber1, self.fiber2)
        self._assert_no_closed_channel(self.fiber2, self.fiber1)

        payment_hash = self.send_payment(self.fiber1, self.fiber2, 1 * CKB)
        payment = self.fiber1.get_client().get_payment({"payment_hash": payment_hash})
        assert payment["status"] == "Success"
