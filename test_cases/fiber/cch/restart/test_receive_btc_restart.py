import pytest

from cch_restart_helpers import CchRestartBase, sha256_hex, wait_lnd_invoice_state


class TestCchReceiveBtcRestart(CchRestartBase):

    # @pytest.mark.skip("https://github.com/nervosnetwork/fiber/pull/1498")
    def test_cch_r101_restart_after_multiple_lnd_hold_invoices_settle(self):
        """CCH-R101.

        Pay several CCH-created LND hold invoices into Fiber hold invoices.
        Restart CCH only after every LND invoice has settled, then verify that
        all settled orders and Fiber invoices are recovered from persistent state.
        """
        self.open_wrapped_btc_channel_to_cch()

        transactions = []
        for index in range(2):
            preimage = self.generate_random_preimage()
            payment_hash = sha256_hex(preimage)
            fiber_invoice = self.fiber2.get_client().new_invoice(
                {
                    "amount": hex(1000),
                    "currency": "Fibd",
                    "description": f"CCH-R101 receive_btc hold invoice {index}",
                    "udt_type_script": self.get_account_udt_script(
                        self.fiber1.account_private
                    ),
                    "payment_hash": payment_hash,
                    "hash_algorithm": "sha256",
                }
            )
            order = self.fiber1.get_client().receive_btc(
                {"fiber_pay_req": fiber_invoice["invoice_address"]}
            )
            assert order["status"] == "Pending"
            assert order["payment_hash"] == payment_hash
            assert "Lightning" in order["incoming_invoice"]
            transactions.append(
                {
                    "order": order,
                    "payment_hash": payment_hash,
                    "preimage": preimage,
                }
            )
        cancel_transactions = []
        for index in range(2):
            preimage = self.generate_random_preimage()
            payment_hash = sha256_hex(preimage)
            fiber_invoice = self.fiber2.get_client().new_invoice(
                {
                    "amount": hex(1000),
                    "currency": "Fibd",
                    "description": f"CCH-R101 receive_btc hold invoice {index}",
                    "udt_type_script": self.get_account_udt_script(
                        self.fiber1.account_private
                    ),
                    "payment_hash": payment_hash,
                    "hash_algorithm": "sha256",
                }
            )
            order = self.fiber1.get_client().receive_btc(
                {"fiber_pay_req": fiber_invoice["invoice_address"]}
            )
            assert order["status"] == "Pending"
            assert order["payment_hash"] == payment_hash
            assert "Lightning" in order["incoming_invoice"]
            cancel_transactions.append(
                {
                    "order": order,
                    "payment_hash": payment_hash,
                    "preimage": preimage,
                }
            )

        for transaction in transactions:
            self.LNDs[1].ln_cli_with_cmd_without_json(
                "payinvoice "
                f"{transaction['order']['incoming_invoice']['Lightning']} --force &"
            )
        for cancel_transaction in cancel_transactions:
            self.LNDs[1].ln_cli_with_cmd_without_json(
                "payinvoice "
                f"{cancel_transaction['order']['incoming_invoice']['Lightning']} --force &"
            )
        for transaction in transactions:
            wait_lnd_invoice_state(
                self.LNDs[0], transaction["payment_hash"], "ACCEPTED"
            )
            self.wait_invoice_state(
                self.fiber2, transaction["payment_hash"], "Received", timeout=120
            )
            self.wait_cch_order_state(
                self.fiber1,
                transaction["payment_hash"],
                "OutgoingInFlight",
                timeout=120,
            )
        for transaction in cancel_transactions:
            wait_lnd_invoice_state(
                self.LNDs[0], transaction["payment_hash"], "ACCEPTED"
            )
            self.wait_invoice_state(
                self.fiber2, transaction["payment_hash"], "Received", timeout=120
            )
            self.wait_cch_order_state(
                self.fiber1,
                transaction["payment_hash"],
                "OutgoingInFlight",
                timeout=120,
            )
        self.restart_cch()
        for transaction in transactions:
            self.fiber2.get_client().settle_invoice(
                {
                    "payment_hash": transaction["payment_hash"],
                    "payment_preimage": transaction["preimage"],
                }
            )
        for cancel_transaction in cancel_transactions:
            self.fiber2.get_client().cancel_invoice(
                {"payment_hash": cancel_transaction["payment_hash"]}
            )
        for transaction in transactions:
            self.wait_payment_state(self.fiber1, transaction["payment_hash"])
            wait_lnd_invoice_state(self.LNDs[0], transaction["payment_hash"], "SETTLED")
        for transaction in transactions:
            payment_hash = transaction["payment_hash"]
            self.wait_cch_order_state(self.fiber1, payment_hash, "Success", timeout=180)
            outgoing_invoice = self.fiber2.get_client().get_invoice(
                {"payment_hash": payment_hash}
            )
            assert outgoing_invoice["status"] == "Paid"
            wait_lnd_invoice_state(self.LNDs[0], payment_hash, "SETTLED")

        for transaction in cancel_transactions:
            self.wait_payment_state(self.fiber1, transaction["payment_hash"], "Failed")
            self.wait_cch_order_state(
                self.fiber1, transaction["payment_hash"], "Failed"
            )
            # todo lnd[0] 状态转为 canceled
            wait_lnd_invoice_state(
                self.LNDs[0], transaction["payment_hash"], "CANCELED"
            )
