"""CCH boundary coverage for Fiber PR #1521.

LND reserves SHA256(32 zero bytes) when creating its own invoices, but a CCH
can still receive an external BOLT11 invoice committed to that preimage.  The
signed fixture below represents that external-payee boundary for send_btc.
"""

from hashlib import sha256

import pytest

from framework.basic_fiber_with_cch import FiberCchTest
from framework.util import H256_ZEROS

CKB_SHANNONS = 100000000
CHANNEL_BALANCE = 1000 * CKB_SHANNONS
PAYMENT_AMOUNT_SATS = 1000
EXPECTED_ERROR = "cannot use hash of all-zeroes preimage"
ZERO_PREIMAGE_BTC_INVOICE = (
    "lnbcrt10u1p4x0ugqdpqwperzdfjxys85etjdus8qun9d9kkzem9pp5ve584t0cv27hwmy0c"
    "x9ca8uwyqyfw9y9dm3r8vus9fv36r2l9yjssp5qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"
    "qqqqqqqqqqqqqqqqqq9qrsgqxqxjespsqcqpjv5t3myl2hkx3028zxd6t0m8unffuzkf6n28"
    "wzeclyecz73qszq9k2qach3rseswrvvvcp27tkfncjk4lchgxzqn709z4akqm37xmddgqdpx"
    "9da"
)


class TestPr1521ZeroLndPreimage(FiberCchTest):
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

    def test_send_btc_accepts_external_invoice_committed_to_zero_preimage(self):
        """Fiber creates a send_btc order for an external zero-preimage invoice."""
        zero_preimage = bytes.fromhex(H256_ZEROS.removeprefix("0x"))
        payment_hash = f"0x{sha256(zero_preimage).hexdigest()}"

        order = self.fiber1.get_client().send_btc(
            {"btc_pay_req": ZERO_PREIMAGE_BTC_INVOICE, "currency": "Fibd"}
        )

        assert order["status"] == "Pending"
        assert order["payment_hash"] == payment_hash
        assert "Fiber" in order["incoming_invoice"]
        invoice = self.fiber1.get_client().get_invoice({"payment_hash": payment_hash})
        assert invoice["status"] == "Open"

    def test_receive_btc_rejects_fiber_hold_invoice_with_zero_preimage(self):
        """LND rejects the incoming hold invoice before the zero preimage settles."""
        self._open_wrapped_btc_channel_to_cch()

        zero_preimage = bytes.fromhex(H256_ZEROS.removeprefix("0x"))
        payment_hash = f"0x{sha256(zero_preimage).hexdigest()}"
        fiber_invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(PAYMENT_AMOUNT_SATS),
                "currency": "Fibd",
                "description": "pr1521-zero-preimage-hold",
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                "payment_hash": payment_hash,
                "hash_algorithm": "sha256",
            }
        )

        with pytest.raises(Exception) as error:
            self.fiber1.get_client().receive_btc(
                {"fiber_pay_req": fiber_invoice["invoice_address"]}
            )
        assert EXPECTED_ERROR in str(error.value)

        invoice = self.fiber2.get_client().get_invoice({"payment_hash": payment_hash})
        assert invoice["status"] == "Open"
        with pytest.raises(Exception):
            self.fiber1.get_client().get_cch_order({"payment_hash": payment_hash})

    def test_send_btc_accepts_all_zero_payment_hash(self):
        """A real LND hold invoice with payment_hash=0 creates a send_btc order."""
        lnd_invoice = self.LNDs[1].addholdinvoice(
            H256_ZEROS.removeprefix("0x"),
            PAYMENT_AMOUNT_SATS,
            "pr1521-send-btc-zero-payment-hash",
        )

        order = self.fiber1.get_client().send_btc(
            {"btc_pay_req": lnd_invoice["payment_request"], "currency": "Fibd"}
        )

        assert order["status"] == "Pending"
        assert order["payment_hash"] == H256_ZEROS
        assert "Fiber" in order["incoming_invoice"]
        invoice = self.fiber1.get_client().get_invoice({"payment_hash": H256_ZEROS})
        assert invoice["status"] == "Open"
        lnd_invoice = self.LNDs[1].ln_cli_with_cmd(
            f"lookupinvoice {H256_ZEROS.removeprefix('0x')}"
        )
        assert lnd_invoice["state"] == "OPEN"

        self.LNDs[1].ln_cli_with_cmd(f"cancelinvoice {H256_ZEROS.removeprefix('0x')}")

    def test_receive_btc_accepts_all_zero_payment_hash(self):
        """A Fiber hold invoice with payment_hash=0 creates a receive_btc order."""
        self._open_wrapped_btc_channel_to_cch()
        fiber_invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(PAYMENT_AMOUNT_SATS),
                "currency": "Fibd",
                "description": "pr1521-receive-btc-zero-payment-hash",
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                "payment_hash": H256_ZEROS,
                "hash_algorithm": "sha256",
            }
        )

        order = self.fiber1.get_client().receive_btc(
            {"fiber_pay_req": fiber_invoice["invoice_address"]}
        )

        assert order["status"] == "Pending"
        assert order["payment_hash"] == H256_ZEROS
        assert "Lightning" in order["incoming_invoice"]
        invoice = self.LNDs[0].ln_cli_with_cmd(
            f"lookupinvoice {H256_ZEROS.removeprefix('0x')}"
        )
        assert invoice["state"] == "OPEN"

        self.LNDs[0].ln_cli_with_cmd(f"cancelinvoice {H256_ZEROS.removeprefix('0x')}")
        self.fiber2.get_client().cancel_invoice({"payment_hash": H256_ZEROS})
