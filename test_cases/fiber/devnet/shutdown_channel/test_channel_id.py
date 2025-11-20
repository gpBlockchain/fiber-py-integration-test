import time

import pytest

from framework.basic_fiber import FiberTest
from framework.config import DEFAULT_MIN_DEPOSIT_CKB


class TestChannelId(FiberTest):

    def test_channel_id_not_exist(self):
        with pytest.raises(Exception) as exc_info:
            self.fiber1.get_client().shutdown_channel(
                {
                    "channel_id": self.generate_random_preimage(),
                    "close_script": self.get_account_script(
                        self.fiber1.account_private
                    ),
                    "fee_rate": "0x3FC",
                }
            )
        expected_error_message = "Channel not found error"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

    # @pytest.mark.skip("https://github.com/nervosnetwork/fiber/issues/431")
    def test_channel_id_exist(self):
        temporary_channel_id = self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(200 * 100000000),
                "public": False,
                # "tlc_fee_proportional_millionths": "0x4B0",
            }
        )
        time.sleep(1)
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY", 120
        )

        channels = self.fiber1.get_client().list_channels(
            {"peer_id": self.fiber2.get_peer_id()}
        )
        N1N2_CHANNEL_ID = channels["channels"][0]["channel_id"]
        self.fiber1.get_client().graph_channels()

        before_balance1 = self.Ckb_cli.wallet_get_capacity(
            self.account1["address"]["testnet"]
        )
        before_balance2 = self.Ckb_cli.wallet_get_capacity(
            self.account2["address"]["testnet"]
        )
        # shut down
        self.fiber1.get_client().shutdown_channel(
            {
                "channel_id": N1N2_CHANNEL_ID,
                "close_script": {
                    "code_hash": "0x9bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8",
                    "hash_type": "type",
                    "args": self.account1["lock_arg"],
                },
                "fee_rate": "0x3FC",
            }
        )

        tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_peer_id(), "CLOSED", 120, True
        )
        node_info = self.fiber1.get_client().node_info()
        print("node info :", node_info)
        assert node_info["channel_count"] == "0x0"
        after_balance1 = self.Ckb_cli.wallet_get_capacity(
            self.account1["address"]["testnet"]
        )
        after_balance2 = self.Ckb_cli.wallet_get_capacity(
            self.account2["address"]["testnet"]
        )
        print("before_balance1:", before_balance1)
        print("before_balance2:", before_balance2)
        print("after_balance1:", after_balance1)
        print("after_balance2:", after_balance2)
        assert after_balance2 - before_balance2 == DEFAULT_MIN_DEPOSIT_CKB / 100000000
