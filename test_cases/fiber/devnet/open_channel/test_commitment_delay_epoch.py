import time

from framework.basic_fiber import FiberTest


class TestCommitmentDelayEpoch(FiberTest):
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 5}

    def test_epoch(self):
        self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
                "commitment_delay_epoch": "0x20001000001",
            }
        )
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY", 120
        )
        self.fiber1.get_client().shutdown_channel(
            {
                "channel_id": self.fiber1.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "force": True,
            }
        )
        tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        self.node.getClient().generate_epochs("0x20001000001")

        tx_hash = self.wait_and_check_tx_pool_fee(1000, False, 1200)
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        first_tx_message = self.get_tx_message(tx_hash)
        self.node.getClient().generate_epochs("0x20001000001")
        tx_hash = self.wait_and_check_tx_pool_fee(1000, False, 1200)
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        second_tx_message = self.get_tx_message(tx_hash)
        print("first tx message:", first_tx_message)
        assert first_tx_message["input_cells"][0]["capacity"] == 109899999544
        assert (
            first_tx_message["input_cells"][0]["capacity"]
            - second_tx_message["input_cells"][0]["capacity"]
            == 100000000000
        )

    def test_self_shutdown(self):
        before_balance = self.get_fibers_balance()
        self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
                "tlc_expiry_delta": hex(57600000),
                "commitment_delay_epoch": "0x6",
            }
        )
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY", 120
        )

        self.fiber1.get_client().shutdown_channel(
            {
                "channel_id": self.fiber1.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "force": True,
            }
        )
        tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        self.node.getClient().generate_epochs("0x1", 0)
        time.sleep(10)
        status = self.node.getClient().get_live_cell("0x0", tx_hash)
        assert status["status"] == "live"
        self.node.getClient().generate_epochs("0x5", 0)
        while len(self.get_commit_cells()) > 0:
            time.sleep(5)
        after_balance = self.get_fibers_balance()
        result = self.get_balance_change(before_balance, after_balance)
        assert 0 < result[0]["ckb"] < 2000

        assert 0 < result[1]["ckb"] < 2000

    def test_remote_shutdown(self):
        before_balance = self.get_fibers_balance()
        self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
                "tlc_expiry_delta": hex(57600000),
                "commitment_delay_epoch": "0x6",
            }
        )
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY", 120
        )

        self.fiber2.get_client().shutdown_channel(
            {
                "channel_id": self.fiber1.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "force": True,
            }
        )
        tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        self.node.getClient().generate_epochs("0x1", 0)
        time.sleep(10)
        status = self.node.getClient().get_live_cell("0x0", tx_hash)
        assert status["status"] == "live"
        self.node.getClient().generate_epochs("0x5", 0)
        while len(self.get_commit_cells()) > 0:
            time.sleep(5)
        after_balance = self.get_fibers_balance()
        result = self.get_balance_change(before_balance, after_balance)
        assert 0 < result[0]["ckb"] < 2000
        assert 0 < result[1]["ckb"] < 2000
