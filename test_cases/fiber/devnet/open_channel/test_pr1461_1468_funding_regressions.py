"""Regression coverage for fiber PR #1461 and PR #1468.

The observable devnet contract is that normal funding still reaches
ChannelReady under a short timeout and after a restart, covering the signed
funding/tx tracer paths that these PRs touched.
"""

import time

from framework.basic_fiber import FiberTest


class TestPR1461PR1468FundingRegressions(FiberTest):
    start_fiber_config = {"fiber_funding_timeout_seconds": 3}

    def test_short_funding_timeout_does_not_abort_signed_channel(self):
        self.open_channel(self.fiber1, self.fiber2, 200 * 100000000, 0)

        channels = self.fiber1.get_client().list_channels(
            {"pubkey": self.fiber2.get_pubkey(), "include_closed": True}
        )["channels"]
        assert channels[0]["state"]["state_name"] == "ChannelReady"

    def test_funding_tracer_survives_restart_and_payment_still_works(self):
        self.fiber1.get_client().open_channel(
            {
                "pubkey": self.fiber2.get_pubkey(),
                "funding_amount": hex(300 * 100000000),
                "public": True,
            }
        )
        time.sleep(1)
        self.fiber1.stop()
        self.fiber1.start(fnn_log_level=self.fnn_log_level)
        self.fiber1.connect_peer(self.fiber2)

        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_pubkey(), "ChannelReady", 120
        )
        payment_hash = self.send_payment(self.fiber1, self.fiber2, 1 * 100000000)
        self.wait_payment_state(self.fiber1, payment_hash, "Success")
