"""Regression tests for fiber PR #1289.

PR #1289 adds a dedicated path for hold invoices that are already Received
and later get a preimage. It also rejects duplicate same-invoice hold TLCs
while the invoice is already Received.
"""

import hashlib

import pytest

from framework.basic_fiber import FiberTest


def _sha256_hex(preimage_hex):
    raw = bytes.fromhex(preimage_hex.replace("0x", ""))
    return "0x" + hashlib.sha256(raw).hexdigest()


class TestReceivedHoldTlcSettlement(FiberTest):
    def _new_hold_invoice(self, amount):
        preimage = self.generate_random_preimage()
        payment_hash = _sha256_hex(preimage)
        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(amount),
                "currency": "Fibd",
                "description": "PR-1289 received hold TLC settlement",
                "expiry": "0xe10",
                "final_cltv": "0x28",
                "payment_hash": payment_hash,
                "hash_algorithm": "sha256",
            }
        )
        return preimage, payment_hash, invoice["invoice_address"]

    def test_received_hold_invoice_settles_after_preimage_is_added(self):
        self.open_channel(self.fiber1, self.fiber2, 500 * 100000000, 100 * 100000000)

        preimage, payment_hash, invoice_address = self._new_hold_invoice(1 * 100000000)
        payment = self.fiber1.get_client().send_payment({"invoice": invoice_address})
        self.wait_invoice_state(self.fiber2, payment_hash, "Received", 120, 1)

        self.fiber2.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )

        self.wait_invoice_state(self.fiber2, payment_hash, "Paid", 120, 1)
        self.wait_payment_state(self.fiber1, payment["payment_hash"], "Success", 120)
        invoice = self.fiber2.get_client().get_invoice({"payment_hash": payment_hash})
        payment_detail = self.fiber1.get_client().get_payment(
            {"payment_hash": payment["payment_hash"]}
        )
        assert invoice["status"] == "Paid"
        assert payment_detail["status"] == "Success"

    def test_duplicate_payment_to_received_hold_invoice_is_rejected(self):
        self.open_channel(self.fiber1, self.fiber2, 500 * 100000000, 100 * 100000000)

        preimage, payment_hash, invoice_address = self._new_hold_invoice(1 * 100000000)
        first = self.fiber1.get_client().send_payment({"invoice": invoice_address})
        self.wait_invoice_state(self.fiber2, payment_hash, "Received", 120, 1)
        with pytest.raises(Exception) as exc_info:
            duplicate = self.fiber1.get_client().send_payment(
                {"invoice": invoice_address}
            )
        # Step 2: Verify that the expected error message is raised
        expected_error_message = "Payment session already exists"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )
        assert (
            self.fiber2.get_client().get_invoice({"payment_hash": payment_hash})[
                "status"
            ]
            == "Received"
        )

        self.fiber2.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )
        self.wait_payment_state(self.fiber1, first["payment_hash"], "Success", 120)
        self.wait_invoice_state(self.fiber2, payment_hash, "Paid", 120, 1)
