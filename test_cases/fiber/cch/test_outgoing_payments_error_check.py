"""
CCH 出站支付执行器对“永久失败”错误的识别（见 outgoing_payments_error_check.md）。

覆盖 SendLightningOutgoingPaymentExecutor / SendFiberOutgoingPaymentExecutor 集成路径，
断言订单与 Fiber 支付在有限时间内进入 Failed，而非长时间重试挂起。
"""

import time

import pytest

from framework.basic_fiber_with_cch import FiberCchTest


def _wait_payment_terminal(fiber, payment_hash, timeout=180):
    """轮询 get_payment，直到 Success 或 Failed。"""
    last = None
    for _ in range(timeout):
        last = fiber.get_client().get_payment({"payment_hash": payment_hash})
        if last["status"] in ("Success", "Failed"):
            return last
        time.sleep(1)
    raise TimeoutError(
        f"payment {payment_hash} did not finish, last status={last['status'] if last else None}"
    )


def _wait_cch_order_status(fiber1, payment_hash, expected, timeout=180):
    last = None
    for _ in range(timeout):
        last = fiber1.get_client().get_cch_order({"payment_hash": payment_hash})
        if last["status"] == expected:
            return last
        if expected == "Failed" and last["status"] == "Success":
            raise AssertionError(f"expected CCH Failed, got Success: {last}")
        if expected == "Success" and last["status"] == "Failed":
            raise AssertionError(f"expected CCH Success, got Failed: {last}")
        time.sleep(1)
    raise TimeoutError(
        f"CCH order {payment_hash} did not reach {expected}, last={last}"
    )


def _lnd_payment_hash_hex_for_lookup(decoded_payreq):
    """lncli lookupinvoice 使用的不带 0x 的小写 hex（与现有用例一致）。"""
    ph = decoded_payreq["payment_hash"]
    if ph.startswith("0x"):
        return ph[2:]
    return ph


class TestOutgoingPaymentsErrorCheck(FiberCchTest):
    def _open_udt_channel_fiber2_to_fiber1(self):
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

    def _assert_send_btc_failed_order_invoice_legs(
        self,
        order,
        payment_hash,
        *,
        lnd_for_outgoing_lookup,
        expected_outgoing_bolt11_state,
        fiber_incoming_allowed_statuses,
    ):
        """send_btc 失败路径：incoming 为 Fiber，outgoing_pay_req 为 BOLT11。"""
        assert "incoming_invoice" in order
        assert order["incoming_invoice"].get(
            "Fiber"
        ), "send_btc 订单应包含 incoming_invoice.Fiber"
        assert order.get(
            "outgoing_pay_req"
        ), "send_btc 订单应包含非空 outgoing_pay_req（BOLT11）"

        finv = self.fiber1.get_client().get_invoice({"payment_hash": payment_hash})
        assert finv["status"] in fiber_incoming_allowed_statuses, (
            f"枢纽侧 Fiber 入站发票状态异常: got {finv['status']}, "
            f"allowed {fiber_incoming_allowed_statuses}, invoice={finv}"
        )

        decoded = self.LNDs[0].ln_cli_with_cmd(
            f"decodepayreq {order['outgoing_pay_req']}"
        )
        assert decoded.get("payment_hash"), f"outgoing_pay_req 应可 decode: {decoded}"

        rhash = _lnd_payment_hash_hex_for_lookup(decoded)
        bolt11_state = lnd_for_outgoing_lookup.ln_cli_with_cmd(
            f"lookupinvoice {rhash}"
        )["state"]
        allowed_ln = (
            (expected_outgoing_bolt11_state,)
            if isinstance(expected_outgoing_bolt11_state, str)
            else expected_outgoing_bolt11_state
        )
        assert bolt11_state in allowed_ln, (
            f"outgoing BOLT11 lookup 状态 expected one of {allowed_ln}, "
            f"got {bolt11_state}, order={order}"
        )

    def _assert_receive_btc_failed_order_invoice_legs(
        self,
        order,
        payment_hash,
        *,
        expected_incoming_lightning_state,
        fiber_outgoing_allowed_statuses,
    ):
        """receive_btc 失败路径：incoming 为 Lightning，outgoing_pay_req 为 Fiber invoice string。"""
        assert "incoming_invoice" in order
        assert order["incoming_invoice"].get(
            "Lightning"
        ), "receive_btc 订单应包含 incoming_invoice.Lightning"
        out_req = order.get("outgoing_pay_req")
        assert out_req, "receive_btc 订单应包含非空 outgoing_pay_req（Fiber）"

        in_ln = self.LNDs[0].ln_cli_with_cmd(
            f"decodepayreq {order['incoming_invoice']['Lightning']}"
        )
        in_rhash = _lnd_payment_hash_hex_for_lookup(in_ln)
        in_state = self.LNDs[0].ln_cli_with_cmd(f"lookupinvoice {in_rhash}")["state"]
        assert (
            in_state == expected_incoming_lightning_state
        ), f"入站 Lightning 发票状态应为 {expected_incoming_lightning_state}, got {in_state}"

        # 出站 Fiber 发票挂在 fiber2（收款方），与订单 payment_hash 对齐
        fout = self.fiber2.get_client().get_invoice({"payment_hash": payment_hash})
        assert fout["status"] in fiber_outgoing_allowed_statuses, (
            f"出站 Fiber 发票（fiber2）状态异常: got {fout['status']}, "
            f"allowed {fiber_outgoing_allowed_statuses}, invoice={fout}"
        )
        parsed = self.fiber2.get_client().parse_invoice({"invoice": out_req})
        ph_parsed = parsed["invoice"]["data"]["payment_hash"]
        ph_norm = ph_parsed if str(ph_parsed).startswith("0x") else f"0x{ph_parsed}"
        assert ph_norm.lower() == payment_hash.lower(), (
            f"outgoing_pay_req 解析出的 payment_hash 应与 CCH 订单一致: "
            f"parsed={ph_norm}, order={payment_hash}"
        )

    @pytest.mark.skip("bug:https://github.com/nervosnetwork/fiber/issues/1222")
    @pytest.mark.skip("todo check")
    def test_lightning_outgoing_invoice_already_paid(self):
        """LND: invoice is already paid — 预先用 LNDs[0] 付清 BTC 发票再走 CCH，出站应永久失败。"""
        self._open_udt_channel_fiber2_to_fiber1()
        lnd_invoice = self.LNDs[1].addinvoice(1000)
        self.LNDs[0].payinvoice(lnd_invoice["payment_request"])

        send_btc = self.fiber1.get_client().send_btc(
            {
                "btc_pay_req": lnd_invoice["payment_request"],
                "currency": "Fibd",
            }
        )
        payment = self.fiber2.get_client().send_payment(
            {"invoice": send_btc["incoming_invoice"]["Fiber"]}
        )
        order = _wait_cch_order_status(
            self.fiber1, payment["payment_hash"], "Failed", timeout=180
        )
        assert order["status"] == "Failed"

        pay_result = _wait_payment_terminal(self.fiber2, payment["payment_hash"])
        assert pay_result["status"] == "Failed", pay_result

        # BOLT11 收款方为 LNDs[1]，且发货票已在建单前被付了
        self._assert_send_btc_failed_order_invoice_legs(
            order,
            payment["payment_hash"],
            lnd_for_outgoing_lookup=self.LNDs[1],
            expected_outgoing_bolt11_state="SETTLED",
            fiber_incoming_allowed_statuses=(
                "Cancelled",
                "Open",
                "Expired",
                "Received",
            ),
        )

    # @pytest.mark.skip("bug:https://github.com/nervosnetwork/fiber/issues/1222")
    def test_lightning_outgoing_self_payment_rejected(self):
        """LND: self-payments not allowed — 发票收款方为 CCH 所持 LNDs[0] 自身。"""
        self._open_udt_channel_fiber2_to_fiber1()
        lnd_invoice = self.LNDs[0].addinvoice(1000)
        send_btc = self.fiber1.get_client().send_btc(
            {
                "btc_pay_req": lnd_invoice["payment_request"],
                "currency": "Fibd",
            }
        )
        payment = self.fiber2.get_client().send_payment(
            {"invoice": send_btc["incoming_invoice"]["Fiber"]}
        )
        order = _wait_cch_order_status(
            self.fiber1, payment["payment_hash"], "Failed", timeout=180
        )
        assert order["status"] == "Failed"
        pay_result = _wait_payment_terminal(self.fiber2, payment["payment_hash"])
        assert pay_result["status"] == "Failed", pay_result
        self._assert_send_btc_failed_order_invoice_legs(
            order,
            payment["payment_hash"],
            lnd_for_outgoing_lookup=self.LNDs[0],
            expected_outgoing_bolt11_state=("CANCELED", "OPEN"),
            fiber_incoming_allowed_statuses=(
                "Cancelled",
                "Open",
                "Expired",
                "Received",
            ),
        )

    # @pytest.mark.skip("bug:https://github.com/nervosnetwork/fiber/issues/1222")
    def test_lightning_outgoing_no_route_to_isolated_lnd(self):
        """LND: no route / unable to find path — 发票来自未与 LNDs[0] 建链的孤立节点。"""
        self._open_udt_channel_fiber2_to_fiber1()
        isolated = self.start_new_lnd()
        lnd_invoice = isolated.addinvoice(1000)
        send_btc = self.fiber1.get_client().send_btc(
            {
                "btc_pay_req": lnd_invoice["payment_request"],
                "currency": "Fibd",
            }
        )
        payment = self.fiber2.get_client().send_payment(
            {"invoice": send_btc["incoming_invoice"]["Fiber"]}
        )

        order = _wait_cch_order_status(
            self.fiber1, payment["payment_hash"], "Failed", timeout=180
        )
        assert order["status"] == "Failed"
        pay_result = _wait_payment_terminal(self.fiber2, payment["payment_hash"])
        assert pay_result["status"] == "Failed", pay_result

        self._assert_send_btc_failed_order_invoice_legs(
            order,
            payment["payment_hash"],
            lnd_for_outgoing_lookup=isolated,
            expected_outgoing_bolt11_state="OPEN",
            fiber_incoming_allowed_statuses=(
                "Cancelled",
                "Open",
                "Expired",
                "Received",
            ),
        )

    def test_send_btc_invalid_btc_payment_request(self):
        """Fiber 侧解析/校验失败：无效 BTC payment request（与 invalid payment request 类永久错误相关）。"""
        with pytest.raises(Exception) as exc_info:
            self.fiber1.get_client().send_btc(
                {
                    "btc_pay_req": "lnbc1notavalidbolt11invoice",
                    "currency": "Fibd",
                }
            )
        msg = str(exc_info.value.args[0]).lower()
        assert "invalid" in msg or "decode" in msg or "payment" in msg
