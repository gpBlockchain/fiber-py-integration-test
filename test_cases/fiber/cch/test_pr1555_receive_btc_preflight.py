"""Integration coverage for PR #1555 receive_btc Fiber-payment preflight.

TP-CCH-1555-001 [P0]: an otherwise valid but unroutable Fiber invoice is
rejected before CCH creates an externally payable LND hold invoice or stores a
CCH order.

TP-CCH-1555-002 [P1]: a routable Fiber invoice still creates the LND hold
invoice and completes the normal receive_btc swap after the Lightning payment.
"""

import pytest

from framework.basic_fiber_with_cch import FiberCchTest


CKB_SHANNONS = 100000000
CHANNEL_BALANCE = 1000 * CKB_SHANNONS
PAYMENT_AMOUNT_SATS = 1000


class TestPr1555ReceiveBtcPreflight(FiberCchTest):
    """Exercise the real CCH/LND boundary before a receive_btc order exists."""

    def _new_wrapped_btc_invoice(self, description):
        return self.fiber2.get_client().new_invoice(
            {
                "amount": hex(PAYMENT_AMOUNT_SATS),
                "currency": "Fibd",
                "description": description,
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                "payment_preimage": self.generate_random_preimage(),
                "hash_algorithm": "sha256",
            }
        )

    def _invoice_payment_hash(self, invoice_address):
        parsed = self.fiber2.get_client().parse_invoice({"invoice": invoice_address})
        payment_hash = parsed["invoice"]["data"]["payment_hash"]
        return payment_hash if payment_hash.startswith("0x") else f"0x{payment_hash}"

    def _assert_lnd_hold_invoice_absent(self, payment_hash):
        with pytest.raises(Exception):
            self.LNDs[0].ln_cli_with_cmd(
                f"lookupinvoice {payment_hash.removeprefix('0x')}"
            )

    def _open_wrapped_btc_channel_to_cch(self):
        self.faucet(
            self.fiber2.account_private,
            0,
            self.fiber1.account_private,
            10000 * CKB_SHANNONS,
        )
        self.open_channel(
            self.fiber2,
            self.fiber1,
            CHANNEL_BALANCE,
            CHANNEL_BALANCE,
            udt=self.get_account_udt_script(self.fiber1.account_private),
        )

    def test_unroutable_fiber_invoice_creates_no_lnd_hold_invoice(self):
        """TP-CCH-1555-001: preflight failure leaves no payable BTC-side state."""
        # The default CCH test setup connects the peers but creates no Fiber channel,
        # making the invoice valid yet currently unroutable from CCH to fiber2.
        invoice = self._new_wrapped_btc_invoice("pr1555-unroutable-preflight")
        payment_hash = self._invoice_payment_hash(invoice["invoice_address"])

        with pytest.raises(Exception) as exc_info:
            self.fiber1.get_client().receive_btc(
                {"fiber_pay_req": invoice["invoice_address"]}
            )

        error = str(exc_info.value).lower()
        assert "route" in error or "path" in error, exc_info.value
        self._assert_lnd_hold_invoice_absent(payment_hash)
        with pytest.raises(Exception):
            self.fiber1.get_client().get_cch_order({"payment_hash": payment_hash})

    def test_routable_fiber_invoice_still_completes_receive_btc_swap(self):
        """TP-CCH-1555-002: a successful preflight preserves the normal swap flow."""
        self._open_wrapped_btc_channel_to_cch()
        invoice = self._new_wrapped_btc_invoice("pr1555-routable-preflight")
        payment_hash = self._invoice_payment_hash(invoice["invoice_address"])

        order = self.fiber1.get_client().receive_btc(
            {"fiber_pay_req": invoice["invoice_address"]}
        )

        assert order["status"] == "Pending"
        assert order["payment_hash"] == payment_hash
        lightning_invoice = order["incoming_invoice"]["Lightning"]
        lnd_invoice = self.LNDs[0].ln_cli_with_cmd(
            f"lookupinvoice {payment_hash.removeprefix('0x')}"
        )
        assert lnd_invoice["state"] == "OPEN"

        self.LNDs[1].payinvoice(lightning_invoice)
        self.wait_cch_order_state(self.fiber1, payment_hash, "Success", timeout=120)
        self.wait_invoice_state(self.fiber2, payment_hash, "Paid", timeout=120)

        lnd_invoice = self.LNDs[0].ln_cli_with_cmd(
            f"lookupinvoice {payment_hash.removeprefix('0x')}"
        )
        assert lnd_invoice["state"] == "SETTLED"
