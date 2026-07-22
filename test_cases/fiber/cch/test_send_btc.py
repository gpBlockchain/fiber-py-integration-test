import time

import pytest

from framework.basic_fiber_with_cch import FiberCchTest
from framework.util import hex_timestamp_to_datetime


class TestSendBtc(FiberCchTest):
    """
    btc_pay_req 参数校验
    btc_pay_req 参数校验
    Invoice.light_network 为连接的btc网络,预期成功
    Invoice.light_network 和连接的btc网络不一致，预期失败
    Invoice.amount 为lnd 能设置的最大值，预期：成功
    Invoice.amount 为 0 , 预期: 失败
    Invoice.amount 为 lnd 能设置的最小值，预期：成功
    Invoice.amount 大于 btc channel的amount，预期：成功
    Invoice.timestamp 为最近生成的invoice，预期：成功
    Invoice.description 为空，预期：成功
    Invoice.description 描述特别长，测试最大值，预期：成功
    Invoice.min_final_cltv_expiry
        todo
    Invoice.exipry 还没过期，预期：成功
    Invoice.exipry 过期了，预期:失败
    Invoice.feature 包含mpp,预期: 成功
    Invoice.feature 包含 amp，预期:失败
    …
    Invoice.Payee Pub Key 为能够连接到的节点，预期：成功
    Invoice.Payee Pub Key 为连接不到的节点，预期：失败
    """

    def test_btc_pay_req_light_network_is_ok(self):
        lndInvoice = self.LNDs[0].addinvoice(1000)
        invoice = self.fiber1.get_client().send_btc(
            {
                "btc_pay_req": lndInvoice["payment_request"],
                "currency": "Fibd",
            }
        )
        assert int(invoice["amount_sats"], 16) == 1000

    # @pytest.mark.skip(
    #     "网络不一致，预期失败:https://github.com/nervosnetwork/fiber/issues/978"
    # )
    def test_btc_pay_req_light_network_not_eq(self):
        #  Invoice.light_network 和连接的btc网络不一致，预期失败
        # Chain
        # bitcoin
        # Amount (Millisatoshis)
        # 1000000000
        # Payee Pub Key
        # 03a1f3afd646d77bdaf545cceaf079bab6057eae52c6319b63b5803d0989d6a72f
        # Invoice
        # lnbc10m1p5s8q0rpp5s0pajatykzues6kgtj39624pavmxum8mgg47t57j706xmcsqxnwqdqqcqzysxqrrsssp5r8a74ej8ez8ckh7f4z4q9td8en6yqcd0fm98ln4kzd98nl5zk9aq9qxpqysgq246rs7trdy5y75fhxtfunjge0cuh33t38rzdwam973zjfws2tna9kjwvtz0vrvfwenhqjd458hp0ump50e3wdwggjfs7p0fdntmag6cqy6cjwt
        # Prefix
        # lnbc10m
        # Recovery Flag
        # 0
        # Amount (Satoshis)
        # 1000000
        # Transaction Signature
        # 557438796369284f513732d3c9c9197e3978c57138c4d77765f44524ba0a5cfa5b49cc589ec1b12eccee0936b43dc2fe6c347e62e6b9089261e0bd2d9af7d46b
        # Payment Hash
        # 83c3d97564b0b9986ac85ca25d2aa1eb366e6cfb422be5d3d2f3f46de20034dc
        # Description
        # --
        # Minimum Final CLTV Expiry
        # 144
        # Expire Time
        # 3600
        # Unknown Tag
        # Tag Code
        # 16
        # Tag Words
        # unknown1r8a74ej8ez8ckh7f4z4q9td8en6yqcd0fm98ln4kzd98nl5zk9aqdl0m0q
        # Unknown Tag
        # Tag Code
        # 5
        # Tag Words
        # unknown1pqysgq2n0z9y
        # Time Expire Date
        # 1761841139
        # Time Expire Date String
        # 2025-10-30T16:18:59.000Z
        # Timestamp
        # 1761837539
        # Timestamp String
        # 2025-10-30T15:18:59.000Z
        # Words Temp
        # temp1p5s8q0rpp5s0pajatykzues6kgtj39624pavmxum8mgg47t57j706xmcsqxnwqdqqcqzysxqrrsssp5r8a74ej8ez8ckh7f4z4q9td8en6yqcd0fm98ln4kzd98nl5zk9aq9qxpqysgq246rs7trdy5y75fhxtfunjge0cuh33t38rzdwam973zjfws2tna9kjwvtz0vrvfwenhqjd458hp0ump50e3wdwggjfs7p0fdntmag6cqe5jyvl
        # todo 预期失败
        # self.fiber1.get_client().send_btc(
        #     {
        #         "btc_pay_req": "lnbc5m1p5jlhv8pp5m2u64ql80ca460hu5tfdr6ryhsz4p222yentf2yhq2w3glmvt09qdqqcqzysxqrrsssp5suyluult7xgm8ft2n9p0kz5ugj86zfr65v6ng450kq0vf6swj5xs9qxpqysgq5rp46mvnmcd2xgctaaqk3t8zfexnazf9m6w409fc3xk5pvavrkahuu6rxm3nm3cgxx27z8t53qwv45l0ls3jqezs5kd36j9kx3cplzqqy3r02f",
        #         "currency": "Fibd",
        #     }
        # )
        invoice = self.LNDs[0].addinvoice(1000)
        self.fiber1.get_client().send_btc(
            {
                "btc_pay_req": invoice["payment_request"],
                "currency": "Fibd",
            }
        )
        # self.LNDs[0].ln_cli_with_cmd(f"decodepayreq lnbc10m1p5s8q0rpp5s0pajatykzues6kgtj39624pavmxum8mgg47t57j706xmcsqxnwqdqqcqzysxqrrsssp5r8a74ej8ez8ckh7f4z4q9td8en6yqcd0fm98ln4kzd98nl5zk9aq9qxpqysgq246rs7trdy5y75fhxtfunjge0cuh33t38rzdwam973zjfws2tna9kjwvtz0vrvfwenhqjd458hp0ump50e3wdwggjfs7p0fdntmag6cqy6cjwt" )

    def test_btc_pay_req_light_network_amount(self):
        """
        max: 100 btc
            成功
        amount is 0
            返回报错：BTC invoice missing amount

        Invoice.description 为空
            预期：成功

        description 最大长度
            成功

        expiry 过期
            失败: BTC invoice expired
        expiry 还没过期，但是时间短
            失败: Outgoing invoice expiry time is too short
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
        lndInvoice = self.LNDs[0].addinvoice(1000000 * 10000000)
        btcResult = self.fiber1.get_client().send_btc(
            {
                "btc_pay_req": lndInvoice["payment_request"],
                "currency": "Fibd",
            }
        )
        assert int(btcResult["amount_sats"], 16) == 10000010000000
        lndInvoice = self.LNDs[1].addinvoice(0)
        with pytest.raises(Exception) as exc_info:
            btcResult = self.fiber1.get_client().send_btc(
                {
                    "btc_pay_req": lndInvoice["payment_request"],
                    "currency": "Fibd",
                }
            )
        expected_error_message = "BTC invoice missing amount"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )
        # 1
        lndInvoice = self.LNDs[1].addinvoice(1)
        send_payment_response = self.fiber1.get_client().send_btc(
            {
                "btc_pay_req": lndInvoice["payment_request"],
                "currency": "Fibd",
            }
        )
        payment = self.fiber2.get_client().send_payment(
            {"invoice": send_payment_response["incoming_invoice"]["Fiber"]}
        )
        self.wait_payment_state(self.fiber2, payment["payment_hash"], "Success")
        cch_order = self.fiber1.get_client().get_cch_order(
            {"payment_hash": payment["payment_hash"]}
        )
        assert cch_order["status"] == "Success"
        assert cch_order["amount_sats"] == "0x1"
        invoice = self.LNDs[1].ln_cli_with_cmd(
            f"lookupinvoice {cch_order['payment_hash'].replace('0x', '')}",
        )
        assert invoice["state"] == "SETTLED"

        # Invoice.description 为空，预期：成功
        lndInvoice = self.LNDs[1].ln_cli_with_cmd(f"addinvoice --amt 1")
        send_payment_response = self.fiber1.get_client().send_btc(
            {
                "btc_pay_req": lndInvoice["payment_request"],
                "currency": "Fibd",
            }
        )
        payment = self.fiber2.get_client().send_payment(
            {"invoice": send_payment_response["incoming_invoice"]["Fiber"]}
        )
        self.wait_payment_state(self.fiber2, payment["payment_hash"], "Success")
        cch_order = self.fiber1.get_client().get_cch_order(
            {"payment_hash": payment["payment_hash"]}
        )
        assert cch_order["status"] == "Success"
        assert cch_order["amount_sats"] == "0x1"
        invoice = self.LNDs[1].ln_cli_with_cmd(
            f"lookupinvoice {cch_order['payment_hash'].replace('0x', '')}",
        )
        assert invoice["state"] == "SETTLED"

        # description 最大长度
        lndInvoice = self.LNDs[1].ln_cli_with_cmd(
            f"addinvoice --amt 1 --memo {self.generate_random_str(637)}"
        )
        send_payment_response = self.fiber1.get_client().send_btc(
            {
                "btc_pay_req": lndInvoice["payment_request"],
                "currency": "Fibd",
            }
        )
        payment = self.fiber2.get_client().send_payment(
            {"invoice": send_payment_response["incoming_invoice"]["Fiber"]}
        )
        self.wait_payment_state(self.fiber2, payment["payment_hash"], "Success")
        cch_order = self.fiber1.get_client().get_cch_order(
            {"payment_hash": payment["payment_hash"]}
        )
        assert cch_order["status"] == "Success"
        assert cch_order["amount_sats"] == "0x1"
        invoice = self.LNDs[1].ln_cli_with_cmd(
            f"lookupinvoice {cch_order['payment_hash'].replace('0x', '')}",
        )
        assert invoice["state"] == "SETTLED"

        # expiry timeout
        lndInvoice = self.LNDs[1].ln_cli_with_cmd(f"addinvoice --amt 1  --expiry 1")
        time.sleep(5)
        with pytest.raises(Exception) as exc_info:
            send_payment_response = self.fiber1.get_client().send_btc(
                {
                    "btc_pay_req": lndInvoice["payment_request"],
                    "currency": "Fibd",
                }
            )
        expected_error_message = "BTC invoice expired"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

        # expiry  发送的时候 timeout
        lndInvoice = self.LNDs[1].ln_cli_with_cmd(f"addinvoice --amt 1  --expiry 5")
        with pytest.raises(Exception) as exc_info:
            send_payment_response = self.fiber1.get_client().send_btc(
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

    @pytest.mark.skip(
        "https://github.com/nervosnetwork/fiber/issues/979 https://github.com/nervosnetwork/fiber/issues/1488"
    )
    def test_payee_pub_key_is_self(self):
        """
        应该会失败
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
        lndInvoice = self.LNDs[0].addinvoice(100)
        send_payment_response = self.fiber1.get_client().send_btc(
            {
                "btc_pay_req": lndInvoice["payment_request"],
                "currency": "Fibd",
            }
        )
        payment = self.fiber2.get_client().send_payment(
            {"invoice": send_payment_response["incoming_invoice"]["Fiber"]}
        )
        self.wait_cch_order_state(self.fiber1, payment["payment_hash"], "Failed")
        # todo  应该要失败才对，outgoing端没发出去, inbound 端应该回滚报错才对
        self.wait_payment_state(self.fiber2, payment["payment_hash"], "Faild")

    @pytest.mark.skip(
        "https://github.com/nervosnetwork/fiber/issues/979 https://github.com/nervosnetwork/fiber/issues/1488"
    )
    def test_payee_pub_key_not_exist(self):
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
        lnd = self.start_new_lnd()
        lndInvoice = lnd.addinvoice(100)
        send_payment_response = self.fiber1.get_client().send_btc(
            {
                "btc_pay_req": lndInvoice["payment_request"],
                "currency": "Fibd",
            }
        )
        payment = self.fiber2.get_client().send_payment(
            {"invoice": send_payment_response["incoming_invoice"]["Fiber"]}
        )
        # todo  应该要失败才对，outgoing端没发出去, inbound 端应该回滚报错才对
        self.wait_cch_order_state(self.fiber1, payment["payment_hash"], "Failed")
        self.wait_payment_state(self.fiber2, payment["payment_hash"], "Failed")

    # @pytest.mark.skip("https://github.com/nervosnetwork/fiber/issues/978")
    def test_currency_not_eq(self):
        lndInvoice = self.LNDs[1].addinvoice(100)
        # 网络不一致，应该报错
        with pytest.raises(Exception) as exc_info:
            self.fiber1.get_client().send_btc(
                {
                    "btc_pay_req": lndInvoice["payment_request"],
                    "currency": "Fibb",
                }
            )
        expected_error_message = "fibd"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

    def test_expiry(self):
        """
        btc.expiry = 5s
            Outgoing invoice expiry time is too short
        btc.expiry = 86400
            fiber.expiry = 86399
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
        invoice = self.LNDs[1].addinvoice(100, "demo --expiry 5")
        with pytest.raises(Exception) as exc_info:
            send_btc = self.fiber1.get_client().send_btc(
                {
                    "btc_pay_req": invoice["payment_request"],
                    "currency": "Fibd",
                }
            )
        expected_error_message = "Outgoing invoice expiry time is too short"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )
        invoice = self.LNDs[1].addinvoice(100, "demo")
        time.sleep(10)
        send_btc = self.fiber1.get_client().send_btc(
            {
                "btc_pay_req": invoice["payment_request"],
                "currency": "Fibd",
            }
        )
        btc_req = self.LNDs[1].ln_cli_with_cmd(
            f"decodepayreq {send_btc['outgoing_pay_req']}"
        )
        assert btc_req["expiry"] == "86400"
        invoice = self.fiber2.get_client().parse_invoice(
            {"invoice": send_btc["incoming_invoice"]["Fiber"]}
        )
        # assert invoice["invoice"]["data"]["attrs"][1]["expiry_time"] == hex()
        time_cost = (
            int(int(invoice["invoice"]["data"]["timestamp"], 16) / 1000)
            + int(invoice["invoice"]["data"]["attrs"][1]["expiry_time"], 16)
            - int(btc_req["expiry"])
            - int(btc_req["timestamp"])
        )
        print("time_cost:", time_cost)
        assert time_cost <= 0
        assert abs(time_cost) <= 1
        time.sleep(5)
        payment = self.fiber2.get_client().send_payment(
            {"invoice": send_btc["incoming_invoice"]["Fiber"]}
        )
        self.wait_payment_state(self.fiber2, payment["payment_hash"], "Success")
        self.wait_cch_order_state(self.fiber1, payment["payment_hash"], "Success")

    def test_send_btc_ckb_final_tlc_expiry_delta(self):
        """
           finalHtlcMinimumExpiryDelta 大于 fiber.invoice.finalHtlcExpiry
             报错：BTC invoice final TLC expiry delta exceeds safe limit for cross-chain swap
        Returns:
        """
        # invoice = self.LNDs[1].addholdinvoice(self.generate_random_preimage().replace("0x", ""), 1000,
        #                                       "demo --expiry 0 --cltv_expiry_delta 2000")
        invoice = self.LNDs[1].addholdinvoice(
            self.generate_random_preimage().replace("0x", ""),
            1000,
            "demo --cltv_expiry_delta 2000",
        )
        with pytest.raises(Exception) as exc_info:
            self.fiber1.get_client().send_btc(
                {
                    "btc_pay_req": invoice["payment_request"],
                    "currency": "Fibd",
                }
            )
        expected_error_message = (
            "BTC invoice final TLC expiry delta exceeds safe limit for cross-chain swap"
        )
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

        # self.fiber2.get_client().parse_invoice({
        #     "invoice": send_btc['incoming_invoice']['Fiber']
        # })
        # self.LNDs[0].ln_cli_with_cmd(f"decodepayreq {send_btc['outgoing_pay_req']}")
        # self.fiber2.get_client().send_payment({
        #     "invoice": send_btc['incoming_invoice']['Fiber'],
        # })

    def test_931(self):
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
        lndInvoice = self.LNDs[1].addinvoice(800)
        send_payment_response = self.fiber1.get_client().send_btc(
            {
                "btc_pay_req": lndInvoice["payment_request"],
                "currency": "Fibd",
            }
        )

        self.LNDs[1].ln_cli_with_cmd(f"cancelinvoice {lndInvoice['r_hash']}")
        time.sleep(10)
        cch_order = self.fiber1.get_client().get_cch_order(
            {"payment_hash": f"0x{lndInvoice['r_hash']}"}
        )
        payment = self.fiber2.get_client().send_payment(
            {"invoice": send_payment_response["incoming_invoice"]["Fiber"]}
        )
        time.sleep(1)
        btc_invoice = self.fiber1.get_client().get_invoice(
            {"payment_hash": payment["payment_hash"]}
        )
        print("btc invoice:", btc_invoice)
        cch_order = self.fiber1.get_client().get_cch_order(
            {"payment_hash": payment["payment_hash"]}
        )
        print("cch order:", cch_order)
        self.wait_cch_order_state(self.fiber1, cch_order["payment_hash"], "Failed")
        time.sleep(10)
        self.fiber1.get_client().cancel_invoice(
            {"payment_hash": payment["payment_hash"]}
        )
        # time.sleep(10)
        # self.fiber2.get_client().get_payment({
        #     "payment_hash": payment['payment_hash']
        # })
        self.wait_payment_state(self.fiber2, payment["payment_hash"], "Failed")
