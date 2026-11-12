"""终态付款历史在节点重启后保持完整，余额不被重放，新付款仍可完成。"""

import math
import time

from test_cases.fiber.devnet.send_payment.mpp.mpp_fixture import MppFixture, CKB
from framework.helper.fiber_ops import (
    calculate_payment_hash,
    wait_for_result,
    restart_fiber,
)


class TestMppPaymentHistory(MppFixture):
    # TEST-MAP: MPP1705-12
    def test_restart_without_payment_history(self):
        """
        1. 用当前版本建立通道，准备无付款历史并核对本金。
        2. 保留原数据正常重启付款节点，重连原接收节点。
        3. 检查历史 hash/原像和余额不变，新付款成功且只扣付一次。
        """
        # 1. 全部历史来自公开付款接口，预期金额由固定付款数计算。
        self._prepare_history(0)

        # 2. 使用框架原有生命周期，不重建数据库或更换二进制。
        restart_fiber(self.sender, peers=(self.receiver,))

        # 3. 同时检查旧历史、新付款、两端本金与锁定释放。
        self._assert_history_and_new_payment()

    # TEST-MAP: MPP1705-12
    def test_restart_with_thousand_payments(self):
        """
        1. 用当前版本建立通道，准备1,000 笔付款历史并核对本金。
        2. 保留原数据正常重启付款节点，重连原接收节点。
        3. 检查历史 hash/原像和余额不变，新付款成功且只扣付一次。
        """
        # 1. 全部历史来自公开付款接口，预期金额由固定付款数计算。
        self._prepare_history(1000)

        # 2. 使用框架原有生命周期，不重建数据库或更换二进制。
        restart_fiber(self.sender, peers=(self.receiver,))

        # 3. 同时检查旧历史、新付款、两端本金与锁定释放。
        self._assert_history_and_new_payment()

    # TEST-MAP: MPP1705-12
    def test_restart_with_large_payment_history(self):
        """
        1. 用当前版本建立通道，准备8,000 笔付款历史并核对本金。
        2. 保留原数据正常重启付款节点，重连原接收节点。
        3. 检查历史 hash/原像和余额不变，新付款成功且只扣付一次。
        """
        # 1. 全部历史来自公开付款接口，预期金额由固定付款数计算。
        self._prepare_history(8000)

        # 2. 使用框架原有生命周期，不重建数据库或更换二进制。
        restart_fiber(self.sender, peers=(self.receiver,))

        # 3. 同时检查旧历史、新付款、两端本金与锁定释放。
        self._assert_history_and_new_payment()

    def _prepare_history(self, count):
        self.sender, self.receiver = self.fiber1, self.fiber2
        self.edge = self.open_payment_channel(self.sender, self.receiver, 1000 * CKB)
        initial = self._balances(self.edge)
        self.expected_history = {}
        for _ in range(count):
            result = self._pay(self.sender, self.receiver)
            payment_hash = result["payment_hash"]
            assert payment_hash not in self.expected_history
            self.expected_history[payment_hash] = result["payment_preimage"]
        self.expected_balances = [
            initial[0] - count * 1_000_000,
            initial[1] + count * 1_000_000,
        ]
        assert self._history(self.sender) == self.expected_history
        assert self._balances(self.edge) == self.expected_balances

    def _assert_history_and_new_payment(self):
        assert self._history(self.sender) == self.expected_history
        assert self._balances(self.edge) == self.expected_balances
        result = self._pay(self.sender, self.receiver)
        assert result["payment_hash"] not in self.expected_history
        assert self._history(self.sender) == {
            **self.expected_history,
            result["payment_hash"]: result["payment_preimage"],
        }
        assert self._balances(self.edge) == [
            self.expected_balances[0] - 1_000_000,
            self.expected_balances[1] + 1_000_000,
        ]

    def _pay(self, sender, receiver, deadline=None):
        deadline = deadline or time.monotonic() + 120

        def remaining():
            value = deadline - time.monotonic()
            assert value > 0, "真实新付款耗尽观察窗口"
            # 框架 wait_invoice_state / wait_payment_state 用 range(timeout)。
            return math.ceil(value)

        preimage = self.generate_random_preimage()
        payment_hash = calculate_payment_hash(preimage, "ckb_hash")
        invoice = receiver.get_client().new_invoice(
            {
                "amount": hex(1000000),
                "currency": "Fibd",
                "payment_hash": payment_hash,
                "hash_algorithm": "ckb_hash",
            }
        )
        row = sender.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "max_parts": "0x1",
                "max_fee_amount": "0x0",
            }
        )
        assert row["payment_hash"] == payment_hash
        self.wait_invoice_state(receiver, payment_hash, "Received", timeout=remaining())

        def single_committed():
            tlcs = [
                t
                for c in sender.get_client().list_channels({})["channels"]
                for t in c["pending_tlcs"]
                if t["payment_hash"] == payment_hash
            ]
            return len(tlcs) == 1 and tlcs[0]["status"] == {"Outbound": "Committed"}

        wait_for_result("历史付款实际单片提交", single_committed, remaining())
        receiver.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )
        self.wait_payment_state(sender, payment_hash, "Success", timeout=remaining())
        result = sender.get_client().get_payment({"payment_hash": payment_hash})
        assert (
            result["status"] == "Success" and result.get("payment_preimage") == preimage
        )
        assert not result.get("failed_error")
        wait_for_result(
            "历史付款单通道无锁定",
            lambda: all(
                int(c[key], 16) == 0
                for f in (sender, receiver)
                for c in f.get_client().list_channels({})["channels"]
                for key in ("offered_tlc_balance", "received_tlc_balance")
            ),
            remaining(),
        )
        return result

    def _history(self, payer):
        found, cursor = {}, None
        for _ in range(1000):
            params = {"limit": hex(100)}
            if cursor:
                params["after"] = cursor
            page = payer.get_client().list_payments(params)
            if not page["payments"]:
                return found
            for row in page["payments"]:
                assert row["payment_hash"] not in found and row["status"] == "Success"
                assert not row.get("failed_error") and row.get("payment_preimage")
                found[row["payment_hash"]] = row["payment_preimage"]
            assert page["last_cursor"] != cursor
            cursor = page["last_cursor"]
        raise AssertionError("历史遍历未结束")

    def _balances(self, edge):
        rows = self.settlement.assert_channel_open(edge)
        assert all(
            int(c[k], 16) == 0
            for c in rows
            for k in ("offered_tlc_balance", "received_tlc_balance")
        )
        return [int(c["local_balance"], 16) for c in rows]
