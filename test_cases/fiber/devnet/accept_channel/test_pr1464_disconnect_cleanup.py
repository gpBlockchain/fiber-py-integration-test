"""Regression coverage for fiber PR #1464.

When an inbound manually accepted channel is still pending, peer disconnect
should delete the stale ChannelOpenRecord instead of leaving it visible forever.
"""

import time

from framework.basic_fiber import FiberTest
from framework.config import DEFAULT_MIN_DEPOSIT_CKB


class TestPR1464DisconnectCleanup(FiberTest):
    start_fiber_config = {
        "fiber_open_channel_auto_accept_min_ckb_funding_amount": 500 * 100000000,
        "fiber_to_be_accepted_channels_number_limit": 1,
    }

    def test_pending_inbound_open_record_removed_on_peer_disconnect(self):
        peer_pubkey = self.fiber1.get_pubkey()

        self.fiber1.get_client().open_channel(
            {
                "pubkey": self.fiber2.get_pubkey(),
                "funding_amount": hex(DEFAULT_MIN_DEPOSIT_CKB),
                "public": True,
            }
        )
        time.sleep(1)

        pending = self.fiber2.get_client().list_channels(
            {"pubkey": peer_pubkey, "only_pending": True}
        )["channels"]
        assert len(pending) == 1

        self.fiber1.get_client().disconnect_peer({"pubkey": self.fiber2.get_pubkey()})

        for _ in range(20):
            pending = self.fiber2.get_client().list_channels(
                {"pubkey": peer_pubkey, "only_pending": True}
            )["channels"]
            if len(pending) == 0:
                break
            time.sleep(1)
        else:
            assert False, f"stale pending channel was not removed: {pending}"

        self.fiber1.connect_peer(self.fiber2)
        self.open_channel(self.fiber1, self.fiber2, 100 * 100000000, 0)
