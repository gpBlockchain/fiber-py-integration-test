"""
PR-1334: Improve funding abort error details.

Regression coverage for detailed funding abort reasons flowing into
list_channels(only_pending=true).  The important contract is that a concrete
funding failure is visible to both peers instead of being overwritten by the
generic "Funding transaction aborted" fallback.
"""

import time

from framework.basic_fiber import FiberTest
from framework.fiber_rpc import FiberRPCClient

CKB = 100000000


class TestFundingAbortDetail(FiberTest):
    def _list_pending_once(self, fiber):
        client = FiberRPCClient(f"http://127.0.0.1:{fiber.rpc_port}", try_count=1)
        return client.list_channels({"only_pending": True})

    def _failed_opening_with_detail(self, fiber, expected_parts, timeout=30):
        for _ in range(timeout):
            try:
                pending = self._list_pending_once(fiber)
            except Exception:
                time.sleep(1)
                continue

            failed = [
                channel
                for channel in pending["channels"]
                if channel["state"]["state_name"] == "Closed"
                and channel["state"]["state_flags"] == "FUNDING_ABORTED"
                and isinstance(channel.get("failure_detail"), str)
            ]
            for channel in failed:
                detail = channel["failure_detail"]
                if all(part in detail for part in expected_parts):
                    return channel
            time.sleep(1)

        assert False, (
            f"failed funding opening with detail containing {expected_parts} "
            f"not found on {fiber.tmp_path}"
        )

    def _open_channel_to_same_funding_key_peer(self):
        same_funding_key_peer = self.start_new_fiber(self.fiber1.account_private)
        self.fiber1.connect_peer(same_funding_key_peer)
        time.sleep(1)

        self.fiber1.get_client().open_channel(
            {
                "pubkey": same_funding_key_peer.get_pubkey(),
                "funding_amount": hex(200 * CKB),
                "public": True,
            }
        )
        return same_funding_key_peer

    def _restart_fiber(self, fiber):
        fiber.force_stop()
        fiber.start(fnn_log_level=self.fnn_log_level)
        time.sleep(3)

    def test_peer_input_uses_our_funding_lock_detail_propagates(self):
        """
        If the peer adds a funding input locked by our own funding source lock,
        the opener should abort with the specific PeerInputUsesOurFundingLock
        detail, and the peer should receive the same detail via TxAbort.
        """
        same_funding_key_peer = self._open_channel_to_same_funding_key_peer()

        expected_detail = [
            "Funding tx rejected: peer-added input #",
            "local funding source lock args",
        ]
        peer_failed = self._failed_opening_with_detail(
            same_funding_key_peer, expected_detail
        )

        self._restart_fiber(self.fiber1)
        opener_failed = self._failed_opening_with_detail(self.fiber1, expected_detail)
        assert peer_failed["failure_detail"] == opener_failed["failure_detail"]

    def test_detailed_funding_abort_failure_detail_survives_restart(self):
        """
        The transient ChannelActorState funding_abort_detail is skip_store, so
        the persisted contract is the channel opening record exposed by
        only_pending=true.  Restart must keep that record's specific detail.
        """
        same_funding_key_peer = self._open_channel_to_same_funding_key_peer()

        expected_detail = [
            "Funding tx rejected: peer-added input #",
            "local funding source lock args",
        ]
        failed_before = self._failed_opening_with_detail(
            same_funding_key_peer, expected_detail
        )

        self._restart_fiber(same_funding_key_peer)

        failed_after = self._failed_opening_with_detail(
            same_funding_key_peer, expected_detail
        )
        assert failed_after["channel_id"] == failed_before["channel_id"]
        assert failed_after["failure_detail"] == failed_before["failure_detail"]
