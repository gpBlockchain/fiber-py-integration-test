"""Integration coverage for PR #1608 receive_btc invoice expiry.

TP-CCH-1608-001 [P0]: a long-lived outgoing Fiber invoice must not make the
incoming LND hold invoice outlive the persisted CCH order deadline.

TP-CCH-1608-002 [P1]: after an abrupt CCH shutdown, LND must expire the capped
hold invoice without relying on the CCH scheduler to cancel it.
"""

import time

from framework.basic_fiber_with_cch import FiberCchTest


CKB_SHANNONS = 100000000
CHANNEL_BALANCE = 1000 * CKB_SHANNONS
FIBER_INVOICE_EXPIRY_SECONDS = 3600
MIN_OUTGOING_INVOICE_EXPIRY_SECONDS = 1


class TestPr1608ReceiveBtcOrderExpiry(FiberCchTest):
    """Exercise the receive_btc Fiber/order deadline at the real LND boundary."""

    def _restart_cch_with_order_expiry(self, order_expiry_seconds):
        self.fiber1.stop()
        self.fiber1.prepare(
            {
                "cch": True,
                "cch_lnd_cert_path": f"{self.LNDs[0].tmp_path}/tls.cert",
                "cch_lnd_rpc_url": f"https://localhost:{self.LNDs[0].rpc_port}",
                "cch_order_expiry_delta_seconds": order_expiry_seconds,
                "cch_min_outgoing_invoice_expiry_delta_seconds": (
                    MIN_OUTGOING_INVOICE_EXPIRY_SECONDS
                ),
            }
        )
        self.fiber1.start()

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

    def _create_receive_btc_order(self, description):
        fiber_invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(1000),
                "currency": "Fibd",
                "description": description,
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                "payment_preimage": self.generate_random_preimage(),
                "hash_algorithm": "sha256",
                "expiry": hex(FIBER_INVOICE_EXPIRY_SECONDS),
            }
        )
        return self.fiber1.get_client().receive_btc(
            {"fiber_pay_req": fiber_invoice["invoice_address"]}
        )

    def _decode_lightning_invoice(self, order):
        return self.LNDs[0].ln_cli_with_cmd(
            f"decodepayreq {order['incoming_invoice']['Lightning']}"
        )

    @staticmethod
    def _hex_or_int(value):
        return int(value, 16) if isinstance(value, str) else int(value)

    def test_lnd_hold_invoice_expiry_is_capped_by_order_deadline(self):
        """TP-CCH-1608-001: the order TTL caps a one-hour Fiber invoice."""
        order_expiry_seconds = 60
        self._restart_cch_with_order_expiry(order_expiry_seconds)
        self._open_wrapped_btc_channel_to_cch()

        order = self._create_receive_btc_order("pr1608-order-expiry-cap")
        decoded = self._decode_lightning_invoice(order)
        lnd_expiry_seconds = int(decoded["expiry"])

        assert order["status"] == "Pending"
        assert 0 < lnd_expiry_seconds <= order_expiry_seconds, decoded
        assert lnd_expiry_seconds < FIBER_INVOICE_EXPIRY_SECONDS

        stored_order = self.fiber1.get_client().get_cch_order(
            {"payment_hash": order["payment_hash"]}
        )
        assert (
            self._hex_or_int(stored_order["expiry_delta_seconds"])
            == order_expiry_seconds
        )
        assert (
            stored_order["incoming_invoice"]["Lightning"]
            == order["incoming_invoice"]["Lightning"]
        )

        lnd_invoice = self.LNDs[0].ln_cli_with_cmd(
            f"lookupinvoice {order['payment_hash'].removeprefix('0x')}"
        )
        assert lnd_invoice["state"] == "OPEN"

    def test_capped_lnd_invoice_expires_while_cch_is_offline(self):
        """TP-CCH-1608-002: LND enforces the deadline after a CCH crash."""
        order_expiry_seconds = 20
        self._restart_cch_with_order_expiry(order_expiry_seconds)
        self._open_wrapped_btc_channel_to_cch()

        order = self._create_receive_btc_order("pr1608-offline-expiry")
        decoded = self._decode_lightning_invoice(order)
        assert 0 < int(decoded["expiry"]) <= order_expiry_seconds, decoded

        # Avoid the CCH scheduler's normal CancelIncomingInvoice action. The LND
        # invoice must still expire from its own PR #1608-capped expiry value.
        self.fiber1.force_stop()

        deadline = time.time() + order_expiry_seconds + 30
        lnd_invoice = None
        while time.time() < deadline:
            lnd_invoice = self.LNDs[0].ln_cli_with_cmd(
                f"lookupinvoice {order['payment_hash'].removeprefix('0x')}"
            )
            if lnd_invoice["state"] == "CANCELED":
                break
            time.sleep(1)

        assert lnd_invoice is not None
        assert lnd_invoice["state"] == "CANCELED", lnd_invoice
