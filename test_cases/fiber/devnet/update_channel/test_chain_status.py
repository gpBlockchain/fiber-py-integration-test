import time

import pytest

from framework.basic_fiber import FiberTest


class TestChainStatus(FiberTest):

    def test_chain_status_pending(self):
        self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(500 * 100000000),
                "public": True,
            }
        )
        # // AWAITING_TX_SIGNATURES
        self.wait_for_channel_state(
            self.fiber1.get_client(),
            self.fiber2.get_peer_id(),
            "AWAITING_TX_SIGNATURES",
        )

        self.fiber1.get_client().update_channel(
            {
                "channel_id": self.fiber1.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "tlc_fee_proportional_millionths": hex(2000),
            }
        )
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY"
        )
        channels = self.fiber1.get_client().list_channels({})
        assert channels["channels"][0]["tlc_fee_proportional_millionths"] == hex(2000)
        # node2 add channel with node3
        self.fiber1.get_client().update_channel(
            {
                "channel_id": self.fiber1.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "tlc_fee_proportional_millionths": hex(3000),
            }
        )
        channels = self.fiber1.get_client().list_channels({})
        assert channels["channels"][0]["tlc_fee_proportional_millionths"] == hex(3000)

        # shutdown
        self.fiber1.get_client().shutdown_channel(
            {
                "channel_id": self.fiber1.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "close_script": {
                    "code_hash": "0x9bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8",
                    "hash_type": "type",
                    "args": self.account1["lock_arg"],
                },
                "fee_rate": "0x3FC",
            }
        )
        self.fiber1.get_client().update_channel(
            {
                "channel_id": self.fiber1.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "tlc_fee_proportional_millionths": hex(4000),
            }
        )
        channels = self.fiber1.get_client().list_channels({"include_closed": True})
        assert channels["channels"][0]["tlc_fee_proportional_millionths"] == hex(4000)
