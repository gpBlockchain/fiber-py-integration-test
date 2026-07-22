import pytest

from framework.basic_fiber_with_cch import FiberCchTest


class CchAnalysisBase(FiberCchTest):
    def restart_cch(self, extra_config=None):
        config = {
            "cch": True,
            "cch_lnd_cert_path": f"{self.LNDs[0].tmp_path}/tls.cert",
            "cch_lnd_rpc_url": f"https://localhost:{self.LNDs[0].rpc_port}",
        }
        if extra_config:
            config.update(extra_config)
        self.fiber1.stop()
        self.fiber1.prepare(update_config=config)
        self.fiber1.start()


class TestCchAmountContractFromAnalysis(CchAnalysisBase):
    def _create_receive_btc_order_with_base_fee(self, amount_sats=1000, fee_sats=500):
        self.restart_cch(
            {
                "cch_base_fee_sats": fee_sats,
                "cch_fee_rate_per_million_sats": 0,
            }
        )
        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(amount_sats),
                "currency": "Fibd",
                "description": "CCH-T004 receive_btc fee observable",
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                "payment_preimage": self.generate_random_preimage(),
                "hash_algorithm": "sha256",
            }
        )
        order = self.fiber1.get_client().receive_btc(
            {"fiber_pay_req": invoice["invoice_address"]}
        )
        decoded = self.LNDs[0].ln_cli_with_cmd(
            f"decodepayreq {order['incoming_invoice']['Lightning']}"
        )
        return order, decoded

    def test_cch_t004_receive_btc_lnd_invoice_charges_amount_plus_fee(self):
        """CCH-T004 positive observable for the real incoming LND amount."""
        amount_sats = 1000
        fee_sats = 500
        order, decoded = self._create_receive_btc_order_with_base_fee(
            amount_sats=amount_sats,
            fee_sats=fee_sats,
        )

        assert int(order["fee_sats"], 16) == fee_sats
        assert int(decoded["num_satoshis"]) == amount_sats + fee_sats

    # @pytest.mark.xfail(
    #     strict=True,
    #     reason=(
    #         "CCH-T004 documents that CchOrderResponse.amount_sats should include "
    #         "fee. receive_btc currently returns and stores the principal amount "
    #         "while the generated LND hold invoice charges amount + fee."
    #     ),
    # )

    @pytest.mark.skip("https://github.com/nervosnetwork/fiber/issues/1499")
    def test_cch_t004_receive_btc_response_and_get_order_amount_sats_include_fee(self):
        """Regression for receive_btc amount_sats excluding the charged fee.

        The schema documents amount_sats as including fee. This test checks both
        the direct receive_btc response and the stored value returned through
        get_cch_order, because clients commonly poll get_cch_order after order
        creation.
        """
        amount_sats = 1000
        fee_sats = 500
        expected_total_sats = amount_sats + fee_sats
        order, decoded = self._create_receive_btc_order_with_base_fee(
            amount_sats=amount_sats,
            fee_sats=fee_sats,
        )
        stored_order = self.fiber1.get_client().get_cch_order(
            {"payment_hash": order["payment_hash"]}
        )

        assert int(decoded["num_satoshis"]) == expected_total_sats
        assert {
            "receive_btc_response": int(order["amount_sats"], 16),
            "get_cch_order": int(stored_order["amount_sats"], 16),
        } == {
            "receive_btc_response": expected_total_sats,
            "get_cch_order": expected_total_sats,
        }
