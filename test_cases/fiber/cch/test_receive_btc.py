import time

import pytest

from framework.basic_fiber_with_cch import FiberCchTest
from framework.test_fiber import FiberConfigPath, Fiber


class TestReceiveBtc(FiberCchTest):

    def test_receive_btc_amount(self):
        """
        amount 为0，预期：报错
        amount 为1，预期：成功
        amount 为最大值10wbtc，预期：成功
        amount 为uint.max ,预期：失败
        Returns:

        """
        self.faucet(
            self.fiber2.account_private,
            0,
            self.fiber1.account_private,
            300000 * 100000000,
        )
        self.open_channel(
            self.fiber2,
            self.fiber1,
            110000 * 100000000,
            110000 * 100000000,
            udt=self.get_account_udt_script(self.fiber1.account_private),
        )
        # amount 为0，预期：报错
        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(0),
                "currency": "Fibd",
                "description": "test invoice",
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                "payment_preimage": self.generate_random_preimage(),
                "hash_algorithm": "sha256",
            }
        )
        # todo https://github.com/nervosnetwork/fiber/issues/1215
        # with pytest.raises(Exception) as exc_info:
        #     self.fiber1.get_client().receive_btc(
        #         {"fiber_pay_req": invoice["invoice_address"]}
        #     )
        # expected_error_message = "ReceiveBTC order payment amount is too small"
        # assert expected_error_message in exc_info.value.args[0], (
        #     f"Expected substring '{expected_error_message}' "
        #     f"not found in actual string '{exc_info.value.args[0]}'"
        # )
        # amount 为1，预期：成功
        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(1),
                "currency": "Fibd",
                "description": "test invoice",
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                "payment_preimage": self.generate_random_preimage(),
                "hash_algorithm": "sha256",
            }
        )
        receive_btc_result = self.fiber1.get_client().receive_btc(
            {"fiber_pay_req": invoice["invoice_address"]}
        )
        decodepayreq_result = self.LNDs[0].ln_cli_with_cmd(
            f"decodepayreq {receive_btc_result['incoming_invoice']['Lightning']}"
        )
        print(f"decodepayreq_result:{decodepayreq_result}")
        assert int(decodepayreq_result["num_satoshis"]) == 1
        # todo wait fix receive
        # ret = self.LNDs[1].payinvoice(receive_btc_result['incoming_invoice']['Lightning'])

        # amount 为uint.max ,预期：失败
        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(2**128 - 1),
                "currency": "Fibd",
                "description": "test invoice",
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                "payment_preimage": self.generate_random_preimage(),
                "hash_algorithm": "sha256",
            }
        )

        with pytest.raises(Exception) as exc_info:
            self.fiber1.get_client().receive_btc(
                {"fiber_pay_req": invoice["invoice_address"]}
            )
        expected_error_message = "ReceiveBTC order payment amount is too large"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )
        # amount 为 btc通道最大值
        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(10000 * 100000000),
                "currency": "Fibd",
                "description": "test invoice",
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                "payment_preimage": self.generate_random_preimage(),
                "hash_algorithm": "sha256",
            }
        )
        receive_btc_result = self.fiber1.get_client().receive_btc(
            {"fiber_pay_req": invoice["invoice_address"]}
        )
        decodepayreq_result = self.LNDs[0].ln_cli_with_cmd(
            f"decodepayreq {receive_btc_result['incoming_invoice']['Lightning']}"
        )
        print(f"decodepayreq_result:{decodepayreq_result}")
        assert int(decodepayreq_result["num_satoshis"]) == 1000001000000

        # 最大值+1
        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(100000 * 100000000 + 1),
                "currency": "Fibd",
                "description": "test invoice",
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                "payment_preimage": self.generate_random_preimage(),
                "hash_algorithm": "sha256",
            }
        )

        with pytest.raises(Exception) as exc_info:
            self.fiber1.get_client().receive_btc(
                {"fiber_pay_req": invoice["invoice_address"]}
            )
        expected_error_message = "invoice amount 100000.10000001 BTC is too large"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

    def test_attrs_description(self):
        """
        Description长度为1，预期：成功
        Description长度为特别长639，预期：成功
        Returns:
        """
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
        # Description长度为1，预期：成功
        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(1),
                "currency": "Fibd",
                "description": self.generate_random_str(1),
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                "payment_preimage": self.generate_random_preimage(),
                "hash_algorithm": "sha256",
            }
        )
        receive_btc_result = self.fiber1.get_client().receive_btc(
            {"fiber_pay_req": invoice["invoice_address"]}
        )
        # Description特别长，预期：成功
        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(1),
                "currency": "Fibd",
                "description": self.generate_random_str(637),
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                "payment_preimage": self.generate_random_preimage(),
                "hash_algorithm": "sha256",
            }
        )
        receive_btc_result = self.fiber1.get_client().receive_btc(
            {"fiber_pay_req": invoice["invoice_address"]}
        )

    # @pytest.mark.skip("https://github.com/nervosnetwork/fiber/issues/983")
    def test_invoice_is_ckb(self):
        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(1000000),
                "currency": "Fibd",
                "description": "test invoice",
                # "udt_type_script": self.get_account_udt_script(
                #     self.fiber1.account_private
                # ),
                "payment_preimage": self.generate_random_preimage(),
                "hash_algorithm": "sha256",
            }
        )
        with pytest.raises(Exception) as exc_info:

            self.fiber1.get_client().receive_btc(
                {"fiber_pay_req": invoice["invoice_address"]}
            )
        expected_error_message = "Wrapped BTC type script mismatch"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(1000000),
                "currency": "Fibd",
                "description": "test invoice",
                "udt_type_script": self.get_account_udt_script(
                    self.fiber2.account_private
                ),
                "payment_preimage": self.generate_random_preimage(),
                "hash_algorithm": "sha256",
            }
        )
        with pytest.raises(Exception) as exc_info:
            self.fiber1.get_client().receive_btc(
                {"fiber_pay_req": invoice["invoice_address"]}
            )
        expected_error_message = "Wrapped BTC type script mismatch"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

    # @pytest.mark.skip("https://github.com/nervosnetwork/fiber/issues/982")
    def test_currency_not_eq_invoice(self):
        fiber = Fiber.init_by_port(
            FiberConfigPath.CURRENT_TESTNET,
            self.Config.ACCOUNT_PRIVATE_2,
            "fiber/node_new",
            "8351",
            "8502",
        )
        fiber.prepare()
        fiber.start()
        try:
            invoice = fiber.get_client().new_invoice(
                {
                    "amount": hex(1000000),
                    "currency": "Fibt",
                    "description": "test invoice",
                    "udt_type_script": self.get_account_udt_script(
                        self.fiber1.account_private
                    ),
                    "payment_preimage": self.generate_random_preimage(),
                    "hash_algorithm": "sha256",
                }
            )
            with pytest.raises(Exception) as exc_info:
                self.fiber1.get_client().receive_btc(
                    {"fiber_pay_req": invoice["invoice_address"]}
                )
            expected_error_message = "expected fibd, got fibt"
            assert expected_error_message in exc_info.value.args[0], (
                f"Expected substring '{expected_error_message}' "
                f"not found in actual string '{exc_info.value.args[0]}'"
            )
        finally:
            fiber.stop()
            fiber.clean()

    # @pytest.mark.skip(
    #     "fiber 发送交易失败，btc这边需要回滚 https://github.com/nervosnetwork/fiber/issues/1216"
    # )
    def test_PayeePublicKey_not_found(self):
        self.fiber3 = self.start_new_fiber(self.generate_account(10000))
        invoice = self.fiber3.get_client().new_invoice(
            {
                "amount": hex(100),
                "currency": "Fibd",
                "description": "test invoice",
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                "payment_preimage": self.generate_random_preimage(),
                "hash_algorithm": "sha256",
            }
        )

        with pytest.raises(Exception) as exc_info:
            receive_btc_result = self.fiber1.get_client().receive_btc(
                {"fiber_pay_req": invoice["invoice_address"]}
            )
        expected_error_message = "Insufficient balance"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

    def test_HashAlgorithm_sha256(self):
        """
            sha256
            ckbhash
        Returns:
        """
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
        before_balances = self.get_fibers_balance()
        before_lnd1_balance = self.LNDs[1].ln_cli_with_cmd("channelbalance")
        # amount 为100，预期：报错
        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(100),
                "currency": "Fibd",
                "description": "test invoice",
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                "payment_preimage": self.generate_random_preimage(),
                "hash_algorithm": "sha256",
            }
        )
        receive_btc_result = self.fiber1.get_client().receive_btc(
            {"fiber_pay_req": invoice["invoice_address"]}
        )
        self.LNDs[1].payinvoice(receive_btc_result["incoming_invoice"]["Lightning"])
        self.wait_cch_order_state(
            self.fiber1, receive_btc_result["payment_hash"], "Success"
        )
        time.sleep(1)
        # check balance
        after_balances = self.get_fibers_balance()
        after_lnd1_balance = self.LNDs[1].ln_cli_with_cmd("channelbalance")
        print(before_balances)
        print(after_balances)
        assert (
            before_balances[0][
                "0x32e555f3ff8e135cece1351a6a2971518392c1e30375c1e006ad0ce8eac07947"
            ]["local_balance"]
            - after_balances[0][
                "0x32e555f3ff8e135cece1351a6a2971518392c1e30375c1e006ad0ce8eac07947"
            ]["local_balance"]
            == 100
        )
        print("before lnd1 balance:", before_lnd1_balance)
        print("after lnd1 balance:", after_lnd1_balance)
        assert (
            int(before_lnd1_balance["balance"]) - int(after_lnd1_balance["balance"])
            == 100
        )

    # @pytest.mark.skip("https://github.com/nervosnetwork/fiber/issues/985")
    def test_HashAlgorithm_ckbhash(self):
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
        before_balances = self.get_fibers_balance()
        before_lnd1_balance = self.LNDs[1].ln_cli_with_cmd("channelbalance")
        # ckbhash
        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(100),
                "currency": "Fibd",
                "description": "test invoice",
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                "payment_preimage": self.generate_random_preimage(),
                "hash_algorithm": "ckb_hash",
            }
        )

        with pytest.raises(Exception) as exc_info:
            receive_btc_result = self.fiber1.get_client().receive_btc(
                {"fiber_pay_req": invoice["invoice_address"]}
            )
        expected_error_message = "CKB invoice hash algorithm is not SHA256"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

    def test_receive_response_check(self):
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
        before_balances = self.get_fibers_balance()
        before_lnd1_balance = self.LNDs[1].ln_cli_with_cmd("channelbalance")
        # ckbhash
        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(100),
                "currency": "Fibd",
                "description": "test invoice",
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                "payment_preimage": self.generate_random_preimage(),
                "hash_algorithm": "sha256",
            }
        )
        receive_btc_result = self.fiber1.get_client().receive_btc(
            {"fiber_pay_req": invoice["invoice_address"]}
        )
        print("receive_btc_result:", receive_btc_result)
        decodepayreq_result = self.LNDs[0].ln_cli_with_cmd(
            f"decodepayreq {receive_btc_result['incoming_invoice']['Lightning']}"
        )
        print(f"decodepayreq_result:{decodepayreq_result}")

    def test_expiry(self):
        """
        fiber.expiry = 6* 60*60
            btc.invoice.expiry = 6* 60*60
            todo expected :receive_btc.expiry_delta_seconds == 6* 60*60
        Returns:

        """
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
        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(100),
                "currency": "Fibd",
                "description": "test invoice",
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                "payment_preimage": self.generate_random_preimage(),
                "hash_algorithm": "sha256",
                "expiry": hex(6 * 60 * 60 + 1),
            }
        )
        receive_btc_result = self.fiber1.get_client().receive_btc(
            {"fiber_pay_req": invoice["invoice_address"]}
        )
        print("receive_btc_result:", receive_btc_result)
        decodepayreq_result = self.LNDs[0].ln_cli_with_cmd(
            f"decodepayreq {receive_btc_result['incoming_invoice']['Lightning']}"
        )
        print(f"decodepayreq_result:{decodepayreq_result}")
        assert decodepayreq_result["expiry"] == str(6 * 60 * 60 + 1)
        # todo expiry_delta_seconds 应该 == expiry
        # assert receive_btc_result['expiry_delta_seconds'] == hex(6* 60*60)

    def test_receive_btc_twice(self):
        self.faucet(
            self.fiber2.account_private,
            0,
            self.fiber1.account_private,
            300000 * 100000000,
        )
        self.open_channel(
            self.fiber2,
            self.fiber1,
            10000 * 100000000,
            10000 * 100000000,
            udt=self.get_account_udt_script(self.fiber1.account_private),
        )
        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(1),
                "currency": "Fibd",
                "description": "test invoice",
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                "payment_preimage": self.generate_random_preimage(),
                "hash_algorithm": "sha256",
            }
        )
        receive_btc_result1 = self.fiber1.get_client().receive_btc(
            {"fiber_pay_req": invoice["invoice_address"]}
        )

        with pytest.raises(Exception) as exc_info:
            receive_btc_result2 = self.fiber1.get_client().receive_btc(
                {"fiber_pay_req": invoice["invoice_address"]}
            )
        expected_error_message = "LND invoice Hash256"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )
