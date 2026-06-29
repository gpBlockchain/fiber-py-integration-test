"""Regression coverage for the June 2026 external-funding fixes.

Covered PRs:
* #1388: peer TxUpdate must not bypass external signed submission.
* #1390: stale post-submit TxComplete must not stall the handshake.
* #1391/#1394/#1404: stale funding exclusions and external lock-script
  verification must not leave externally-funded channels stuck.
"""

from test_cases.fiber.devnet.open_channel_with_external_funding.external_funding_base import (
    ExternalFundingBase,
)

EXTERNAL_FUNDING_AMOUNT = 500 * 100000000


class TestExternalFundingRegressions(ExternalFundingBase):
    __test__ = True

    def test_external_funding_does_not_progress_before_signed_submit(self):
        context = self._open_external_funding_channel(
            funding_amount=EXTERNAL_FUNDING_AMOUNT,
            public=True,
        )

        self.fiber1.connect_peer(self.fiber2)
        channel = self._wait_until_channel_condition(
            self.fiber1.get_client(),
            self.fiber2.get_pubkey(),
            context["channel_id"],
            lambda ch: ch["state"]["state_name"]
            in (
                "AwaitingExternalFunding",
                "NegotiatingFunding",
            ),
            timeout=20,
        )
        assert channel["state"]["state_name"] in (
            "AwaitingExternalFunding",
            "NegotiatingFunding",
        )

        pending = self.fiber1.get_client().list_channels(
            {"pubkey": self.fiber2.get_pubkey(), "only_pending": True}
        )["channels"]
        assert any(ch["channel_id"] == context["channel_id"] for ch in pending)

    def test_external_funding_submit_then_restart_reaches_ready(self):
        context = self._open_sign_submit_external_channel(
            funding_amount=EXTERNAL_FUNDING_AMOUNT,
            public=True,
        )

        self.fiber1.stop()
        self.fiber1.start(fnn_log_level=self.fnn_log_level)
        self.fiber1.connect_peer(self.fiber2)

        self._wait_both_channel_ready(context["channel_id"], timeout=180)

    def test_external_funding_uses_external_wallet_and_remains_payable(self):
        context = self._open_sign_submit_external_channel(
            funding_amount=EXTERNAL_FUNDING_AMOUNT,
            public=True,
        )
        self._wait_both_channel_ready(context["channel_id"], timeout=180)

        payment_hash = self.send_payment(self.fiber1, self.fiber2, 1 * 100000000)
        payment = self.fiber1.get_client().get_payment({"payment_hash": payment_hash})
        assert payment["status"] == "Success"

        external_lock_script = context["external_lock_script"]
        assert external_lock_script != self.get_account_script(
            self.fiber1.account_private
        )
