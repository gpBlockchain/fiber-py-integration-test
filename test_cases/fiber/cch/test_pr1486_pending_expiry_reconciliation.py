import hashlib
import time

from framework.basic_fiber_with_cch import FiberCchTest


def sha256_hex(preimage_hex):
    raw = bytes.fromhex(preimage_hex.replace("0x", ""))
    return "0x" + hashlib.sha256(raw).hexdigest()


class TestPR1486PendingExpiryReconciliation(FiberCchTest):
    """PR-1486: only Pending CCH orders should expire from the original quote TTL."""

    def _restart_cch_with_order_expiry(self, expiry_seconds):
        self.fiber1.stop()
        self.fiber1.prepare(
            {
                "cch": True,
                "cch_lnd_cert_path": f"{self.LNDs[0].tmp_path}/tls.cert",
                "cch_lnd_rpc_url": f"https://localhost:{self.LNDs[0].rpc_port}",
                "cch_order_expiry_delta_seconds": expiry_seconds,
            }
        )
        self.fiber1.start()

    def _open_udt_channel_to_cch(self):
        self.faucet(
            self.fiber2.account_private,
            0,
            self.fiber1.account_private,
            10000 * 100000000,
        )
        self.open_channel(
            self.fiber2,
            self.fiber1,
            1000 * 100000000,
            1000 * 100000000,
            udt=self.get_account_udt_script(self.fiber1.account_private),
        )

    def _assert_order_stays_active_after_quote_ttl(self, payment_hash, expiry_seconds):
        time.sleep(expiry_seconds + 2)
        order = self.fiber1.get_client().get_cch_order({"payment_hash": payment_hash})
        assert order["status"] != "Failed", (
            "active CCH order must not be failed by the original quote TTL; "
            f"order={order}"
        )
        assert order["status"] in (
            "IncomingAccepted",
            "OutgoingInFlight",
            "OutgoingSuccess",
        ), order
        return order

    def test_send_btc_outgoing_inflight_survives_original_quote_ttl(self):
        expiry_seconds = 10
        self._restart_cch_with_order_expiry(expiry_seconds)
        self._open_udt_channel_to_cch()

        preimage = self.generate_random_preimage()
        payment_hash = sha256_hex(preimage)
        lnd_invoice = self.LNDs[1].addholdinvoice(
            payment_hash.replace("0x", ""), 1000, "PR-1486 send_btc hold"
        )
        order = self.fiber1.get_client().send_btc(
            {
                "btc_pay_req": lnd_invoice["payment_request"],
                "currency": "Fibd",
            }
        )
        payment = self.fiber2.get_client().send_payment(
            {"invoice": order["incoming_invoice"]["Fiber"]}
        )

        self.wait_cch_order_state(self.fiber1, payment_hash, "OutgoingInFlight")
        self._assert_order_stays_active_after_quote_ttl(payment_hash, expiry_seconds)

        self.LNDs[1].ln_cli_with_cmd(f"settleinvoice {preimage.replace('0x', '')}")
        self.wait_cch_order_state(self.fiber1, payment_hash, "Success")
        self.wait_payment_state(self.fiber2, payment["payment_hash"], "Success")

    def test_receive_btc_outgoing_inflight_survives_original_quote_ttl(self):
        expiry_seconds = 10
        self._restart_cch_with_order_expiry(expiry_seconds)
        self._open_udt_channel_to_cch()

        preimage = self.generate_random_preimage()
        payment_hash = sha256_hex(preimage)
        fiber_invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(1000),
                "currency": "Fibd",
                "description": "PR-1486 receive_btc hold",
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                "payment_hash": payment_hash,
                "hash_algorithm": "sha256",
                "expiry": hex(21610),
                "final_cltv": "0x28",
            }
        )
        order = self.fiber1.get_client().receive_btc(
            {"fiber_pay_req": fiber_invoice["invoice_address"]}
        )

        self.LNDs[1].ln_cli_with_cmd_without_json(
            f"payinvoice {order['incoming_invoice']['Lightning']} --force &"
        )
        self.wait_payment_state(self.fiber1, payment_hash, "Inflight")
        self.wait_invoice_state(self.fiber2, payment_hash, "Received")
        self.wait_cch_order_state(
            self.fiber1, payment_hash, "OutgoingInFlight", timeout=10
        )
        self._assert_order_stays_active_after_quote_ttl(payment_hash, expiry_seconds)

        self.fiber2.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )
        self.wait_cch_order_state(self.fiber1, payment_hash, "Success")
