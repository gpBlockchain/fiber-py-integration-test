import time

import pytest

from framework.basic_fiber_with_cch import FiberCchTest


class TestCCHConfig(FiberCchTest):
    """

    - 配置
        - CCH_BASE_DIR
            - 更换目录，预期: cch存储目录会更改
        - CCH_LND_RPC_URL
            - 配置lnd url ，预期: 能够使用
        - CCH_LND_CERT_HEX
            - 配置配合lnd url ，预期: 能够使用
        - CCH_LND_CERT_PATH
            - 配置配合lnd url ，预期: 能够使用
        - CCH_LND_MACAROON_HEX
            - 配置配合lnd url ，预期: 能够使用
        - CCH_WRAPPED_BTC_TYPE_SCRIPT
            - 配置为白名单里的udt script, 预期：该udt 为btc 映射在ckb上的封装代币
        - CCH_ORDER_EXPIRY_DELTA_SECONDS
            - 没有配置，预期: 使用默认的订单过期时长（36小时）
            - 配置改值（秒），预期：使用配置的秒级过期时长
        - CCH_BASE_FEE_SATS
            - 测试收取手续费 数值是否准确
            - 没有配置，预期 ：cch转发交易过程中不收取额外手续费
            - 配置该值，预期：cch转发过程中，收取该值对应的手续费
        - CCH_FEE_RATE_PER_MILLION_SATS
            - 测试收取手续费 数值是否准确
            - 没有配置，预期 ：cch转发交易过程中不收取额外手续费
            - 配置该值，预期：cch转发过程中，收取该值对应的手续费
        - CCH_BTC_FINAL_TLC_EXPIRY_DELTA_BLOCKS
            - 没有配置，预期: 默认180区块（约30小时）
            - 配置改值（区块数），预期：使用配置的区块数
        - CCH_CKB_FINAL_TLC_EXPIRY_DELTA_SECONDS
            - 没有配置，预期: 默认30小时
            - 配置改值（秒），预期：使用配置的秒级过期时长
        - CCH_MIN_OUTGOING_INVOICE_EXPIRY_DELTA_SECONDS
            - 没有配置，预期: 默认6小时
            - 配置改值（秒），预期：send_btc 和 receive_btc 的expiry 小于 EXPIRY_DELTA_SECONDS，报错
        - CCH_FIBER_RPC_URL
            - 配置改值，预期：能够使用

    """

    @pytest.mark.skip("更改失败")
    def test_cch_config_CCH_BASE_DIR(self):
        """
        - CCH_BASE_DIR
        - 更换目录，预期: cch存储目录会更改
        """
        # self.fiber1.prepare(update_config={
        #     "cch": True,
        #     "cch_lnd_cert_path": f"{self.LNDs[0].tmp_path}/tls.cert",
        #     "cch_lnd_rpc_url": f"https://localhost:{self.LNDs[0].rpc_port}",
        #     "cch_base_dir": f"{self.fiber1.tmp_path}/custom_cch_dir",
        # })
        self.fiber1.stop()
        # self.fiber1.start()
        # self.fiber1.get_client().node_info()
        # todo check dir exist
        pass

    def test_cch_order_expiry_delta_seconds(self):
        """
        - CCH_ORDER_EXPIRY_DELTA_SECONDS
        - 配置改值（秒），预期：新建订单记录该值，并在超时后失败
        todo: 过期后 incoming_invoice的订单是不是应该取消
        """
        expiry_seconds = 10
        self.fiber1.stop()
        self.fiber1.prepare(
            {
                "cch": True,
                "cch_lnd_cert_path": f"{self.LNDs[0].tmp_path}/tls.cert",
                "cch_lnd_rpc_url": f"https://localhost:{self.LNDs[0].rpc_port}",
                "cch_order_expiry_delta_seconds": expiry_seconds,
            }
        )
        with open(self.fiber1.fiber_config_path, "r") as f:
            config_text = f.read()
        assert (
            f"order_expiry_delta_seconds: {expiry_seconds}" in config_text
        ), config_text

        self.fiber1.start()
        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(1000),
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
            {
                "fiber_pay_req": invoice["invoice_address"],
            }
        )
        payment_hash = receive_btc_result["payment_hash"]
        cch_order = self.fiber1.get_client().get_cch_order(
            {"payment_hash": payment_hash}
        )

        assert "expiry_delta_seconds" in cch_order
        expiry_delta_seconds = cch_order["expiry_delta_seconds"]
        if isinstance(expiry_delta_seconds, str):
            expiry_delta_seconds = int(expiry_delta_seconds, 16)
        assert expiry_delta_seconds == expiry_seconds

        start = time.time()
        timeout = expiry_seconds + 30
        while time.time() - start < timeout:
            cch_order = self.fiber1.get_client().get_cch_order(
                {"payment_hash": payment_hash}
            )
            if cch_order["status"] == "Failed":
                break
            time.sleep(1)

        assert cch_order["status"] == "Failed", cch_order
        assert time.time() - start >= expiry_seconds - 1
        assert time.time() - start < timeout
        ret = self.LNDs[0].ln_cli_with_cmd(
            f"decodepayreq {cch_order['incoming_invoice']['Lightning']}"
        )
        print("ret:", ret)

    # @pytest.mark.skip("https://github.com/nervosnetwork/fiber/issues/976")
    def test_CCH_FEE_RATE_PER_MILLION_SATS(self):
        """
        不填，默认为1

        填 1000

        填 最大值
            btc invoice max:100000 btc
            cch_fee_rate_per_million_sats max:u64 max
            CCH_BASE_FEE_SATS : u64 max

        fiber invoice max value:
        """
        self.fiber1.stop()
        self.fiber1.prepare(
            {
                "cch": True,
                "cch_lnd_cert_path": f"{self.LNDs[0].tmp_path}/tls.cert",
                "cch_lnd_rpc_url": f"https://localhost:{self.LNDs[0].rpc_port}",
                # "cch_fee_rate_per_million_sats": 18446744073709551615,
                # "cch_base_fee_sats": 18446744073709551615,
            }
        )
        self.fiber1.start()
        lndInvoice = self.LNDs[0].addinvoice(10000000 * 1000000)
        btcResult = self.fiber1.get_client().send_btc(
            {
                "btc_pay_req": lndInvoice["payment_request"],
                "currency": "Fibd",
            }
        )
        assert int(btcResult["amount_sats"], 16) == 10000010000000
        assert int(btcResult["fee_sats"], 16) == 10000000
        invoice = self.fiber1.get_client().parse_invoice(
            {"invoice": btcResult["incoming_invoice"]["Fiber"]}
        )
        assert int(invoice["invoice"]["amount"], 16) == 10000010000000

        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": "0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF",
                "currency": "Fibd",
                "description": "test invoice",
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                "hash_algorithm": "sha256",
                "payment_preimage": self.generate_random_preimage(),
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
        with pytest.raises(Exception) as exc_info:
            invoice = self.fiber2.get_client().new_invoice(
                {
                    "amount": hex(10000000000000),
                    "currency": "Fibd",
                    "description": "test invoice",
                    "udt_type_script": self.get_account_udt_script(
                        self.fiber1.account_private
                    ),
                    "hash_algorithm": "sha256",
                    "payment_preimage": self.generate_random_preimage(),
                }
            )
            receive_btc = self.fiber1.get_client().receive_btc(
                {"fiber_pay_req": invoice["invoice_address"]}
            )
        expected_error_message = "max is 100000 BTC"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )
        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(1000000000000),
                "currency": "Fibd",
                "description": "test invoice",
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                "hash_algorithm": "sha256",
                "payment_preimage": self.generate_random_preimage(),
            }
        )
        receive_btc = self.fiber1.get_client().receive_btc(
            {"fiber_pay_req": invoice["invoice_address"]}
        )
        # todo 查看btc pay
        payreq = self.LNDs[0].ln_cli_with_cmd(
            f"decodepayreq {receive_btc["incoming_invoice"]["Lightning"]}",
        )
        print("payreq:", payreq)
        assert payreq["num_satoshis"] == "1000001000000"

        # 填 1000
        # 填 1000
        self.fiber1.stop()
        self.fiber1.prepare(
            {
                "cch": True,
                "cch_lnd_cert_path": f"{self.LNDs[0].tmp_path}/tls.cert",
                "cch_lnd_rpc_url": f"https://localhost:{self.LNDs[0].rpc_port}",
                "cch_fee_rate_per_million_sats": 1000,
                "cch_base_fee_sats": 1000,
            }
        )
        self.fiber1.start()
        # send_btc
        lndInvoice = self.LNDs[0].addinvoice(1000000)
        btcResult = self.fiber1.get_client().send_btc(
            {
                "btc_pay_req": lndInvoice["payment_request"],
                "currency": "Fibd",
            }
        )
        print("btc_pay_req:", btcResult)
        invoice = self.fiber1.get_client().parse_invoice(
            {"invoice": btcResult["incoming_invoice"]["Fiber"]}
        )
        assert int(invoice["invoice"]["amount"], 16) == 1002000
        assert int(btcResult["fee_sats"], 16) == 2000

        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(1000000),
                "currency": "Fibd",
                "description": "test invoice",
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                "hash_algorithm": "sha256",
                "payment_preimage": self.generate_random_preimage(),
            }
        )
        receive_btc = self.fiber1.get_client().receive_btc(
            {"fiber_pay_req": invoice["invoice_address"]}
        )
        payreq = self.LNDs[0].ln_cli_with_cmd(
            f"decodepayreq {receive_btc["incoming_invoice"]["Lightning"]}",
        )
        print("payreq:", payreq)
        assert payreq["num_satoshis"] == "1002000"

        self.fiber1.stop()
        self.fiber1.prepare(
            {
                "cch": True,
                "cch_lnd_cert_path": f"{self.LNDs[0].tmp_path}/tls.cert",
                "cch_lnd_rpc_url": f"https://localhost:{self.LNDs[0].rpc_port}",
                "cch_fee_rate_per_million_sats": 18446744073709551615,
                "cch_base_fee_sats": 18446744073709551615,
            }
        )
        self.fiber1.start()
        lndInvoice = self.LNDs[0].addinvoice(10000000000000)
        btcResult = self.fiber1.get_client().send_btc(
            {
                "btc_pay_req": lndInvoice["payment_request"],
                "currency": "Fibd",
            }
        )
        self.fiber1.get_client().parse_invoice(
            {"invoice": btcResult["incoming_invoice"]["Fiber"]}
        )

    # @pytest.mark.skip("https://github.com/nervosnetwork/fiber/issues/977")
    def test_CCH_FEE_RATE_PER_MILLION_SATS_voerflow(self):
        self.fiber1.stop()
        self.fiber1.prepare(
            {
                "cch": True,
                "cch_lnd_cert_path": f"{self.LNDs[0].tmp_path}/tls.cert",
                "cch_lnd_rpc_url": f"https://localhost:{self.LNDs[0].rpc_port}",
                "cch_fee_rate_per_million_sats": 18446744073709551615,
                "cch_base_fee_sats": 18446744073709551615,
            }
        )
        self.fiber1.start()
        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(340282366920938463463374607431768211455),
                "currency": "Fibd",
                "description": "test invoice",
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                "payment_preimage": self.generate_random_preimage(),
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

    def test_CCH_BTC_FINAL_TLC_EXPIRY_DELTA_BLOCKS(self):
        self.fiber1.stop()
        self.fiber1.prepare(
            {
                "cch": True,
                "cch_lnd_cert_path": f"{self.LNDs[0].tmp_path}/tls.cert",
                "cch_lnd_rpc_url": f"https://localhost:{self.LNDs[0].rpc_port}",
                "cch_btc_final_tlc_expiry_delta_blocks": 200,
            }
        )
        self.fiber1.start()
        # receive_btc
        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(1000000),
                "currency": "Fibd",
                "description": "test invoice",
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                "payment_preimage": self.generate_random_preimage(),
                "final_expiry_delta": hex(9600000),
                "hash_algorithm": "sha256",
            }
        )
        receive_btc = self.fiber1.get_client().receive_btc(
            {"fiber_pay_req": invoice["invoice_address"]}
        )
        payreq = self.LNDs[0].ln_cli_with_cmd(
            f"decodepayreq {receive_btc["incoming_invoice"]["Lightning"]}",
        )
        print("receive_btc:", receive_btc)
        print("payreq:", payreq)
        assert payreq["cltv_expiry"] == "200"

    # @pytest.mark.skip("https://github.com/nervosnetwork/fiber/issues/977")
    def test_CCH_BTC_FINAL_TLC_EXPIRY_overflow(self):
        self.fiber1.stop()
        time.sleep(2)
        self.fiber1.prepare(
            {
                "cch": True,
                "cch_lnd_cert_path": f"{self.LNDs[0].tmp_path}/tls.cert",
                "cch_lnd_rpc_url": f"https://localhost:{self.LNDs[0].rpc_port}",
                "cch_btc_final_tlc_expiry_delta_blocks": 18446744073709551615,
            }
        )
        self.fiber1.start()
        # receive_btc
        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(1000000),
                "currency": "Fibd",
                "description": "test invoice",
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                "payment_preimage": self.generate_random_preimage(),
                "final_expiry_delta": hex(9600000),
                "hash_algorithm": "sha256",
            }
        )
        with pytest.raises(Exception) as exc_info:
            receive_btc = self.fiber1.get_client().receive_btc(
                {"fiber_pay_req": invoice["invoice_address"]}
            )
        expected_error_message = (
            "too large and causes overflow when converting to milliseconds"
        )
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )
        # payreq = self.LNDs[0].ln_cli_with_cmd(
        #     f"decodepayreq {receive_btc["incoming_invoice"]["Lightning"]}",
        # )
        # print("receive_btc:", receive_btc)
        # print("payreq:", payreq)

    def test_CCH_CKB_FINAL_TLC_EXPIRY_DELTA_SECONDS(self):
        self.fiber1.stop()
        self.fiber1.prepare(
            {
                "cch": True,
                "cch_lnd_cert_path": f"{self.LNDs[0].tmp_path}/tls.cert",
                "cch_lnd_rpc_url": f"https://localhost:{self.LNDs[0].rpc_port}",
                "cch_ckb_final_tlc_expiry_delta_seconds": 2 * 24 * 60 * 60 + 1,
            }
        )
        self.fiber1.start()
        # send_btc
        lndInvoice = self.LNDs[0].addinvoice(1000000)
        btcResult = self.fiber1.get_client().send_btc(
            {
                "btc_pay_req": lndInvoice["payment_request"],
                "currency": "Fibd",
            }
        )
        print("send_btc:", btcResult)
        invoice = self.fiber1.get_client().parse_invoice(
            {"invoice": btcResult["incoming_invoice"]["Fiber"]}
        )
        assert invoice["invoice"]["data"]["attrs"][2][
            "final_htlc_minimum_expiry_delta"
        ] == hex((2 * 24 * 60 * 60 + 1) * 1000)
        self.LNDs[0].ln_cli_with_cmd(f"decodepayreq {lndInvoice["payment_request"]}")
        print("invoice:", invoice)

    @pytest.mark.skip("todo 不知道如何使用")
    def test_CCH_FIBER_RPC_URL(self):
        # self.fiber1.stop()
        #
        # self.fiber1.prepare({
        #     "cch": True,
        #     "cch_lnd_cert_path": f"{self.LNDs[0].tmp_path}/tls.cert",
        #     "cch_lnd_rpc_url": f"https://localhost:{self.LNDs[0].rpc_port}",
        # })
        # self.fiber1.start()

        self.fiber2.stop()

        # self.fiber2.prepare({
        #     "cch": True,
        #     # "cch_lnd_cert_path": f"{self.LNDs[0].tmp_path}/tls.cert",
        #     # "cch_lnd_rpc_url": f"https://localhost:{self.LNDs[0].rpc_port}",
        #     "cch_fiber_rpc_url": self.fiber1.get_client().url
        # })
        self.fiber2.start()
        invoice = self.fiber1.get_client().new_invoice(
            {
                "amount": hex(1000000),
                "currency": "Fibd",
                "description": "test invoice",
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                "payment_preimage": self.generate_random_preimage(),
            }
        )
        self.fiber2.get_client().receive_btc(
            {"fiber_pay_req": invoice["invoice_address"]}
        )

    def test_CCH_MIN_OUTGOING_INVOICE_EXPIRY_DELTA_SECONDS(self):
        """
        - 没有配置，预期: 默认6小时
        - 配置改值（秒），预期：send_btc 和 receive_btc 的expiry 小于 EXPIRY_DELTA_SECONDS，报错
        Returns:
        """
        # - 没有配置，预期: 默认6小时

        # send_btc
        lndInvoice = self.LNDs[1].addinvoice(1000, "demo  --expiry 21610")
        payreq = self.LNDs[0].ln_cli_with_cmd(
            f"decodepayreq {lndInvoice["payment_request"]}",
        )
        print("payreq:", payreq)
        # lndInvoice = self.LNDs[0].addinvoice(1000000 * 10000000)
        send_payment_response = self.fiber1.get_client().send_btc(
            {
                "btc_pay_req": lndInvoice["payment_request"],
                "currency": "Fibd",
            }
        )
        invoice = self.fiber2.get_client().parse_invoice(
            {"invoice": send_payment_response["incoming_invoice"]["Fiber"]}
        )
        print("invoice:", invoice)
        assert (
            abs(int(invoice["invoice"]["data"]["attrs"][1]["expiry_time"], 16) - 21600)
            <= 10
        )

        lndInvoice = self.LNDs[1].addinvoice(1000, "demo  --expiry 21599")
        with pytest.raises(Exception) as exc_info:
            self.fiber1.get_client().send_btc(
                {
                    "btc_pay_req": lndInvoice["payment_request"],
                    "currency": "Fibd",
                }
            )
        expected_error_message = "Outgoing invoice expiry time is too short"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

        # receive_btc
        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(1000000),
                "currency": "Fibd",
                "description": "test invoice",
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                "payment_preimage": self.generate_random_preimage(),
                "expiry": hex(21610),
                "hash_algorithm": "sha256",
            }
        )
        receive_btc_result = self.fiber1.get_client().receive_btc(
            {"fiber_pay_req": invoice["invoice_address"]}
        )
        payreq = self.LNDs[0].ln_cli_with_cmd(
            f"decodepayreq {receive_btc_result["incoming_invoice"]['Lightning']}",
        )
        print("payreq:", payreq)
        assert abs(int(payreq["expiry"]) - 21610) <= 10

        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(1000000),
                "currency": "Fibd",
                "description": "test invoice",
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                "payment_preimage": self.generate_random_preimage(),
                "expiry": hex(21599),
                "hash_algorithm": "sha256",
            }
        )
        with pytest.raises(Exception) as exc_info:
            self.fiber1.get_client().receive_btc(
                {"fiber_pay_req": invoice["invoice_address"]}
            )
        expected_error_message = "Outgoing invoice expiry time is too short"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

        # 配置10s
        self.fiber1.stop()
        self.fiber1.prepare(
            {
                "cch": True,
                "cch_lnd_cert_path": f"{self.LNDs[0].tmp_path}/tls.cert",
                "cch_lnd_rpc_url": f"https://localhost:{self.LNDs[0].rpc_port}",
                "cch_min_outgoing_invoice_expiry_delta_seconds": 10,
            }
        )
        self.fiber1.start()
        lndInvoice = self.LNDs[1].addinvoice(1000, "demo  --expiry 20")

        send_payment_response = self.fiber1.get_client().send_btc(
            {
                "btc_pay_req": lndInvoice["payment_request"],
                "currency": "Fibd",
            }
        )

        lndInvoice = self.LNDs[1].addinvoice(1000, "demo  --expiry 9")

        with pytest.raises(Exception) as exc_info:
            self.fiber1.get_client().send_btc(
                {
                    "btc_pay_req": lndInvoice["payment_request"],
                    "currency": "Fibd",
                }
            )
        expected_error_message = "Outgoing invoice expiry time is too short"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )
