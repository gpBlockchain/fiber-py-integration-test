import time

import pytest

from cch_restart_helpers import CchRestartBase, sha256_hex, wait_lnd_invoice_state


class TestCchLndRestart(CchRestartBase):
    start_fiber_config = {
        "cch_base_fee_sats": 0,
        "cch_fee_rate_per_million_sats": 5000,
    }

    # @pytest.mark.skip("https://github.com/nervosnetwork/fiber/issues/1501")
    def test_cch_r201_lnd_sender_restarts_during_outgoing_inflight(self):
        """CCH-R201.

        CCH's LND node restarts while multiple send_btc outgoing payments are
        in-flight and receive_btc orders are pending. After LND comes back,
        CCH should recover both directions and finish the Fiber side.
        """
        self.open_wrapped_btc_channel_to_cch()
        for i in range(3):
            send_btc_transactions = []
            receive_btc_transactions = []

            for index in range(3):
                fiber_invoice = self.new_wrapped_btc_fiber_invoice(
                    1000,
                    description=f"CCH-R201 receive_btc LND restart {i}-{index}",
                )
                order = self.fiber1.get_client().receive_btc(
                    {"fiber_pay_req": fiber_invoice["invoice_address"]}
                )
                assert order["status"] == "Pending"
                receive_btc_transactions.append(order)

            for index in range(3):
                preimage = self.generate_random_preimage()
                payment_hash = sha256_hex(preimage)
                lnd_invoice = self.LNDs[1].addholdinvoice(
                    payment_hash.replace("0x", ""),
                    1000,
                    f"CCH-R201 send_btc LND restart {i}-{index}",
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
                send_btc_transactions.append(
                    {
                        "preimage": preimage,
                        "payment_hash": payment_hash,
                        "payment": payment,
                    }
                )

            for transaction in send_btc_transactions:
                self.wait_cch_order_state(
                    self.fiber1,
                    transaction["payment_hash"],
                    "OutgoingInFlight",
                    timeout=120,
                )

            self.LNDs[0].stop()
            self.LNDs[0].start()
            time.sleep(10)
            for transaction in send_btc_transactions:
                self.LNDs[1].ln_cli_with_cmd(
                    f"settleinvoice {transaction['preimage'].replace('0x', '')}"
                )

            for transaction in receive_btc_transactions:
                self.LNDs[1].payinvoice(transaction["incoming_invoice"]["Lightning"])

            for transaction in send_btc_transactions:
                wait_lnd_invoice_state(
                    self.LNDs[1], transaction["payment_hash"], "SETTLED"
                )
                self.wait_cch_order_state(
                    self.fiber1, transaction["payment_hash"], "Success", timeout=180
                )
                self.wait_payment_state(
                    self.fiber2, transaction["payment"]["payment_hash"], "Success"
                )

            for transaction in receive_btc_transactions:
                wait_lnd_invoice_state(
                    self.LNDs[0], transaction["payment_hash"], "SETTLED"
                )
                self.wait_cch_order_state(
                    self.fiber1, transaction["payment_hash"], "Success", timeout=180
                )
                fiber_invoice = self.fiber2.get_client().get_invoice(
                    {"payment_hash": transaction["payment_hash"]}
                )
                assert fiber_invoice["status"] == "Paid"
