"""Regression coverage for fiber PR #1371.

PR #1371 extends the pending-channel limit so manually pending and already
auto-accepted-but-not-ready channels count against the same per-peer cap.
The public integration surface can observe the manual-pending side through
``list_channels(only_pending=true)``.
"""

import time

from framework.basic_fiber import FiberTest
from framework.config import DEFAULT_MIN_DEPOSIT_CKB


class TestAutoAcceptPendingLimits(FiberTest):
    __test__ = True

    start_fiber_config = {
        "fiber_open_channel_auto_accept_min_ckb_funding_amount": 500 * 100000000,
        "fiber_to_be_accepted_channels_number_limit": 2,
    }

    def _pending_channels_from_fiber1(self):
        return self.fiber2.get_client().list_channels(
            {
                "pubkey": self.fiber1.get_pubkey(),
                "only_pending": True,
            }
        )["channels"]

    def test_per_peer_pending_channel_number_limit_is_enforced(self):
        funding_amount = DEFAULT_MIN_DEPOSIT_CKB

        for _ in range(2):
            self.fiber1.get_client().open_channel(
                {
                    "pubkey": self.fiber2.get_pubkey(),
                    "funding_amount": hex(funding_amount),
                    "public": True,
                }
            )
            time.sleep(1)

        pending = self._pending_channels_from_fiber1()
        assert len(pending) == 2

        try:
            self.fiber1.get_client().open_channel(
                {
                    "pubkey": self.fiber2.get_pubkey(),
                    "funding_amount": hex(funding_amount),
                    "public": True,
                }
            )
        except Exception:
            pass

        time.sleep(2)
        pending = self._pending_channels_from_fiber1()
        assert len(pending) == 2
