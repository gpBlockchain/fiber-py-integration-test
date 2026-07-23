"""PR #1546: recover CCH LND payments after tracker and daemon restarts."""
import pytest

from cch_restart_helpers import CchRestartBase, sha256_hex, wait_lnd_invoice_state


def _payment_hash_without_prefix(payment_hash):
    return payment_hash.removeprefix("0x").lower()


def _lnd_payments_for_hash(lnd, payment_hash):
    target_hash = _payment_hash_without_prefix(payment_hash)
    payments = lnd.ln_cli_with_cmd("listpayments --include_incomplete").get(
        "payments", []
    )
    return [
        payment
        for payment in payments
        if payment.get("payment_hash", "").lower() == target_hash
    ]


class TestPr1546TrackPaymentRecovery(CchRestartBase):
    def _create_outgoing_inflight_payment(self, description):
        self.open_wrapped_btc_channel_to_cch()

        preimage = self.generate_random_preimage()
        payment_hash = sha256_hex(preimage)
        lnd_invoice = self.LNDs[1].addholdinvoice(
            _payment_hash_without_prefix(payment_hash), 1000, description
        )
        order = self.fiber1.get_client().send_btc(
            {
                "btc_pay_req": lnd_invoice["payment_request"],
                "currency": "Fibd",
            }
        )
        assert order["payment_hash"] == payment_hash

        fiber_payment = self.fiber2.get_client().send_payment(
            {"invoice": order["incoming_invoice"]["Fiber"]}
        )
        self.wait_cch_order_state(
            self.fiber1, payment_hash, "OutgoingInFlight", timeout=120
        )
        return preimage, payment_hash, fiber_payment

    def _assert_successful_swap(self, payment_hash, fiber_payment):
        self.wait_cch_order_state(self.fiber1, payment_hash, "Success", timeout=180)
        self.wait_payment_state(self.fiber2, fiber_payment["payment_hash"], "Success")

        order = self.fiber1.get_client().get_cch_order(
            {"payment_hash": payment_hash}
        )
        assert order["status"] == "Success"
        incoming_invoice = self.fiber1.get_client().get_invoice(
            {"payment_hash": payment_hash}
        )
        assert incoming_invoice["status"] == "Paid"

    @pytest.mark.skip("https://github.com/nervosnetwork/fiber/pull/1546")
    def test_cch_r202_recovers_terminal_payment_after_cch_and_lnd_restart(self):
        """TP-CCH-SEND-RECOVERY-001 [P0]: recover a persisted LND success."""
        preimage, payment_hash, fiber_payment = self._create_outgoing_inflight_payment(
            "CCH-R202 CCH and LND restart after success"
        )

        # The payment reaches its terminal state while CCH is down. Restarting
        # LND before CCH comes back ensures recovery queries LND's persisted
        # per-payment state rather than relying on an already-missed live stream.
        self.fiber1.stop()
        self.LNDs[1].ln_cli_with_cmd(
            f"settleinvoice {_payment_hash_without_prefix(preimage)}"
        )
        wait_lnd_invoice_state(self.LNDs[1], payment_hash, "SETTLED")
        self.LNDs[0].stop()
        self.LNDs[0].start()
        self.restart_cch()

        self._assert_successful_swap(payment_hash, fiber_payment)
        payments = _lnd_payments_for_hash(self.LNDs[0], payment_hash)
        assert len(payments) == 1
        assert payments[0]["status"] == "SUCCEEDED"

    @pytest.mark.skip("outgoing 需要回滚 ")
    def test_cch_r006_recovers_lnd_failure_without_settling_fiber(self):
        """TP-CCH-SEND-RECOVERY-002 [P1]: recover a missed LND failure."""
        _preimage, payment_hash, fiber_payment = self._create_outgoing_inflight_payment(
            "CCH-R006 failure while CCH is down"
        )

        self.fiber1.stop()
        self.LNDs[1].ln_cli_with_cmd(
            f"cancelinvoice {_payment_hash_without_prefix(payment_hash)}"
        )
        wait_lnd_invoice_state(self.LNDs[1], payment_hash, "CANCELED")
        self.restart_cch()

        self.wait_cch_order_state(self.fiber1, payment_hash, "Failed", timeout=180)
        self.wait_payment_state(self.fiber2, fiber_payment["payment_hash"], "Failed")
        incoming_invoice = self.fiber1.get_client().get_invoice(
            {"payment_hash": payment_hash}
        )
        assert incoming_invoice["status"] == "Cancelled"

        payments = _lnd_payments_for_hash(self.LNDs[0], payment_hash)
        assert len(payments) == 1
        assert payments[0]["status"] == "FAILED"

    @pytest.mark.skip("https://github.com/nervosnetwork/fiber/pull/1546")
    def test_cch_r201_recovers_terminal_payment_after_lnd_restart(self):
        """TP-CCH-LND-RECOVERY-001 [P1]: LND restarts before terminal replay."""
        preimage, payment_hash, fiber_payment = self._create_outgoing_inflight_payment(
            "CCH-R201 LND restart before terminal replay"
        )

        # The remote hold invoice is settled while CCH's LND is unavailable.
        # When LND returns, CCH must obtain the current terminal payment state.
        self.LNDs[0].stop()
        self.LNDs[1].ln_cli_with_cmd(
            f"settleinvoice {_payment_hash_without_prefix(preimage)}"
        )
        wait_lnd_invoice_state(self.LNDs[1], payment_hash, "SETTLED")
        self.LNDs[0].start()

        self._assert_successful_swap(payment_hash, fiber_payment)

    def test_cch_r008_restarts_keep_one_lnd_payment_record(self):
        """TP-CCH-SEND-RECOVERY-003 [P1]: restart does not resend payment."""
        preimage, payment_hash, fiber_payment = self._create_outgoing_inflight_payment(
            "CCH-R008 duplicate payment prevention"
        )

        self.restart_cch()
        self.restart_cch()
        payments = _lnd_payments_for_hash(self.LNDs[0], payment_hash)
        assert len(payments) == 1
        assert payments[0]["status"] == "IN_FLIGHT"

        self.LNDs[1].ln_cli_with_cmd(
            f"settleinvoice {_payment_hash_without_prefix(preimage)}"
        )
        wait_lnd_invoice_state(self.LNDs[1], payment_hash, "SETTLED")
        self._assert_successful_swap(payment_hash, fiber_payment)

        payments = _lnd_payments_for_hash(self.LNDs[0], payment_hash)
        assert len(payments) == 1
        assert payments[0]["status"] == "SUCCEEDED"
