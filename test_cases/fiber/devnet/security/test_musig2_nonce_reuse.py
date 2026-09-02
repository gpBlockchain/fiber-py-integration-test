"""PR 1628: empty CS after dropped RAA must not emit reciprocal CommitmentSigned.

Before the revert of `needs_nonce_rollover`, a stock victim would ACK an empty
CommitmentSigned and then send another empty CS (signature A). That CS reused
the funding pubnonce together with ClosingSigned (B) and the on-chain
aggregate (C), which recovered the victim funding key.

After PR 1628 the victim still ACKs, but must not send that extra CS.

    ./venv/bin/pytest test_cases/fiber/devnet/security/test_musig2_nonce_reuse.py -k test_no_raa_reuses_funding_nonce_and_recovers_key --tb=short
"""

from __future__ import annotations

import time

from framework.attack_fnn import requires_attack_fnn
from framework.basic_fiber import FiberTest
from framework.test_fiber import FiberConfigPath


@requires_attack_fnn
class TestMusig2NonceReuse(FiberTest):
    """Stock victim + attacker fnn. Only the attacker exposes attack Dev RPCs."""

    fiber_version = FiberConfigPath.CURRENT_DEV

    def test_no_raa_reuses_funding_nonce_and_recovers_key(self):
        victim = self.fiber1
        attacker = self.start_new_fiber(
            self.generate_account(10000),
            fiber_version=FiberConfigPath.ATTACK_DEV,
        )
        # Skip the helper's rebalance payment so the channel stays TLC-empty.
        channel_id = self.open_channel(victim, attacker, 200 * 100000000, 0)

        attacker_client = attacker.get_client()
        attacker_client.set_fiber_message_intercept(
            {
                "channel_id": channel_id,
                "suppress_outbound_revoke_and_ack": True,
                "capture_inbound": True,
            }
        )

        attacker_client.send_raw_channel_message(
            {
                "channel_id": channel_id,
                "kind": "commitment_signed",
            }
        )

        inbox = []
        raa = self._wait_captured(attacker_client, inbox, "RevokeAndAck", 10)
        assert raa is not None, "victim must ACK the empty CommitmentSigned"

        extra_cs = self._wait_captured(attacker_client, inbox, "CommitmentSigned", 5)
        assert extra_cs is None, (
            "victim sent a reciprocal empty CommitmentSigned (signature A); "
            "that CS reused the funding nonce and recovered the funding key"
        )

        channels = victim.get_client().list_channels({})["channels"]
        match = next(c for c in channels if c["channel_id"] == channel_id)
        assert match["state"]["state_name"] == "ChannelReady"

    def _wait_captured(self, client, inbox, kind, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = client.take_captured_fiber_messages() or {}
            inbox.extend(result.get("messages") or [])
            for index, message in enumerate(inbox):
                if message.get("kind") == kind:
                    return inbox.pop(index)
            time.sleep(0.2)
        return None
