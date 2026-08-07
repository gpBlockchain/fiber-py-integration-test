import time

import pytest

from framework.basic_fiber import FiberTest


class TestForceAwaitChannel(FiberTest):

    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 5}

    def test_force_shutdown_await_channel_ready(self):
        """
        https://github.com/nervosnetwork/fiber/pull/1613
        Returns:

        """
        self.fiber1.get_client().open_channel(
            {
                "pubkey": self.fiber2.get_pubkey(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
            }
        )
        self.wait_for_channel_state(
            self.fiber1.get_client(),
            self.fiber2.get_pubkey(),
            "AwaitingChannelReady",
            120,
        )
        self.fiber2.stop()
        tx_hash = self.fiber1.get_client().list_channels({})["channels"][0][
            "channel_outpoint"
        ][:-8]
        channel_id = self.fiber1.get_client().list_channels({})["channels"][0][
            "channel_id"
        ]
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        time.sleep(10)
        with pytest.raises(Exception) as exc_info:
            self.fiber1.get_client().shutdown_channel(
                {
                    "channel_id": channel_id,
                }
            )
        msg = str(exc_info.value.args[0]).lower()
        assert "peer is offline" in msg
        self.fiber1.get_client().shutdown_channel(
            {"channel_id": channel_id, "force": True}
        )
        shutdown_tx = self.wait_and_check_tx_pool_fee(1000, False, 100)
        self.Miner.miner_until_tx_committed(self.node, shutdown_tx)
        self.fiber2.start()
        before_balance = self.get_fibers_balance()
        self.node.getClient().generate_epochs("0x1")
        # for i in range(100):
        # if self.get_commit_cells() == 0
        for _ in range(100):
            commit_cells = self.get_commit_cells()
            if not commit_cells:
                break
            time.sleep(5)
        else:
            commit_cells = self.get_commit_cells()
            if commit_cells:
                raise TimeoutError(
                    "Commit cells were not consumed within 600 seconds: "
                    f"tip={self.node.getClient().get_tip_block_number()}, "
                    f"remaining_commit_cells={commit_cells}"
                )
        after_balance = self.get_fibers_balance()
        result = self.get_balance_change(before_balance, after_balance)
        print("get_balance_change result:", result)
        assert abs(1000 * 100000000 + result[0]["ckb"]) < 2000
        assert abs(99 * 100000000 + result[1]["ckb"]) < 2000

        # assert result
        # check channel status
        self.fiber1.get_client().list_channels({"include_closed": True})
        self.fiber2.get_client().list_channels({"include_closed": True})

        self.wait_for_channel_state(
            self.fiber1.get_client(),
            self.fiber2.get_pubkey(),
            "Closed",
            5 * 60,
            include_closed=True,
        )
        self.wait_for_channel_state(
            self.fiber2.get_client(),
            self.fiber1.get_pubkey(),
            "Closed",
            5 * 60,
            include_closed=True,
        )
