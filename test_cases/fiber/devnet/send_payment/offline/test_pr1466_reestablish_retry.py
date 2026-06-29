"""Regression coverage for fiber PR #1466.

Payments with pending TLCs must still finish after rapid reconnect/reestablish.
"""

import time

from framework.basic_fiber import FiberTest


class TestPR1466ReestablishRetry(FiberTest):
    def _wait_for_pending_tlcs(self):
        for _ in range(30):
            channels = self.fiber1.get_client().list_channels(
                {"pubkey": self.fiber2.get_pubkey()}
            )["channels"]
            assert channels, "no channel with fiber2"
            if channels[0].get("pending_tlcs", []):
                return
            time.sleep(1)
        assert False, "payment batch never created pending TLCs"

    def _disconnect_and_reconnect(self):
        self.fiber1.get_client().disconnect_peer({"pubkey": self.fiber2.get_pubkey()})
        time.sleep(1)
        self.fiber1.connect_peer(self.fiber2)
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_pubkey(), "ChannelReady", 120
        )
        self.wait_for_channel_state(
            self.fiber2.get_client(), self.fiber1.get_pubkey(), "ChannelReady", 120
        )

    def test_pending_payments_finish_after_rapid_reconnects(self):
        self.open_channel(self.fiber1, self.fiber2, 500 * 100000000, 100 * 100000000)

        payment_hashes = []
        for _ in range(5):
            payment_hashes.append(
                self.send_payment(self.fiber1, self.fiber2, 1 * 100000000, False)
            )

        self._wait_for_pending_tlcs()
        self._disconnect_and_reconnect()
        self._disconnect_and_reconnect()

        for payment_hash in payment_hashes:
            self.wait_payment_state(self.fiber1, payment_hash, "Success", 120)

        channels = self.fiber1.get_client().list_channels(
            {"pubkey": self.fiber2.get_pubkey()}
        )["channels"]
        assert channels[0].get("pending_tlcs", []) == []
