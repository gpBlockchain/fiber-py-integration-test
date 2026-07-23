import pytest

from cch_restart_helpers import CchRestartBase, sha256_hex, wait_lnd_invoice_state


class TestCchSendBtcRestart(CchRestartBase):
    def test_cch_r001_pending_order_restart_recovers_invoice_tracking(self):
        """CCH-R001.

        Restart with a Pending send_btc order, then pay the Fiber incoming
        invoice. CCH must resume tracking and finish the swap.
        """
        self.open_wrapped_btc_channel_to_cch()

        preimage = self.generate_random_preimage()
        payment_hash = sha256_hex(preimage)
        lnd_invoice = self.LNDs[1].addholdinvoice(
            payment_hash.replace("0x", ""),
            1000,
            "CCH-R001 send_btc pending restart",
        )
        order = self.fiber1.get_client().send_btc(
            {
                "btc_pay_req": lnd_invoice["payment_request"],
                "currency": "Fibd",
            }
        )
        assert order["payment_hash"] == payment_hash
        assert order["status"] == "Pending"

        self.restart_cch()

        payment = self.fiber2.get_client().send_payment(
            {"invoice": order["incoming_invoice"]["Fiber"]}
        )
        self.wait_cch_order_state(
            self.fiber1, payment_hash, "OutgoingInFlight", timeout=120
        )
        self.LNDs[1].ln_cli_with_cmd(f"settleinvoice {preimage.replace('0x', '')}")
        wait_lnd_invoice_state(self.LNDs[1], payment_hash, "SETTLED")
        self.wait_cch_order_state(self.fiber1, payment_hash, "Success", timeout=180)
        self.wait_payment_state(self.fiber2, payment["payment_hash"], "Success")

    def test_cch_r003_restart_does_not_duplicate_inflight_lnd_payment(self):
        """CCH-R003 / CCH-R008 / CCH-T008."""
        self.open_wrapped_btc_channel_to_cch()

        preimage = self.generate_random_preimage()
        payment_hash = sha256_hex(preimage)
        lnd_invoice = self.LNDs[1].addholdinvoice(
            payment_hash.replace("0x", ""),
            1000,
            "CCH-R003 send_btc duplicate outgoing restart",
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
        self.wait_cch_order_state(
            self.fiber1, payment_hash, "OutgoingInFlight", timeout=120
        )

        self.restart_cch()

        order_after_restart = self.fiber1.get_client().get_cch_order(
            {"payment_hash": payment_hash}
        )
        assert order_after_restart["status"] in (
            "OutgoingInFlight",
            "OutgoingSuccess",
            "Success",
        ), order_after_restart

        self.LNDs[1].ln_cli_with_cmd(f"settleinvoice {preimage.replace('0x', '')}")
        wait_lnd_invoice_state(self.LNDs[1], payment_hash, "SETTLED")
        self.wait_cch_order_state(self.fiber1, payment_hash, "Success", timeout=180)
        self.wait_payment_state(self.fiber2, payment["payment_hash"], "Success")

    @pytest.mark.skip("https://github.com/nervosnetwork/fiber/pull/1546")
    def test_cch_r004_recovers_lnd_success_during_cch_downtime(self):
        """CCH-R004 / CCH-T006."""
        self.open_wrapped_btc_channel_to_cch()

        preimage = self.generate_random_preimage()
        payment_hash = sha256_hex(preimage)
        lnd_invoice = self.LNDs[1].addholdinvoice(
            payment_hash.replace("0x", ""),
            1000,
            "CCH-R004 send_btc restart recovery",
        )
        order = self.fiber1.get_client().send_btc(
            {
                "btc_pay_req": lnd_invoice["payment_request"],
                "currency": "Fibd",
            }
        )
        assert order["payment_hash"] == payment_hash

        payment = self.fiber2.get_client().send_payment(
            {"invoice": order["incoming_invoice"]["Fiber"]}
        )
        self.wait_cch_order_state(
            self.fiber1, payment_hash, "OutgoingInFlight", timeout=120
        )

        self.fiber1.stop()
        self.LNDs[1].ln_cli_with_cmd(f"settleinvoice {preimage.replace('0x', '')}")
        wait_lnd_invoice_state(self.LNDs[1], payment_hash, "SETTLED")
        self.restart_cch()

        self.wait_cch_order_state(self.fiber1, payment_hash, "Success", timeout=180)
        self.wait_payment_state(self.fiber2, payment["payment_hash"], "Success")
        incoming_invoice = self.fiber1.get_client().get_invoice(
            {"payment_hash": payment_hash}
        )
        assert incoming_invoice["status"] == "Paid"
