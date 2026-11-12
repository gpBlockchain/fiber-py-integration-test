"""终态付款历史在节点重启后保持完整，余额不被重放，新付款仍可完成。"""

import math
import time

import pytest

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
        # 1. 拓扑：sender --1,000 CKB（public、右端 0）--> receiver。
        # 准备 0 笔历史；每笔 sender → receiver 为 1,000,000 Shannon，单片、零费用。
        self.sender, self.receiver = self.fiber1, self.fiber2
        delay = 0x10000000001  # 1 epoch；用于公开通道参数和到期保护核对。
        public = {"public": True, "commitment_delay_epoch": hex(delay)}
        channel_id = self.open_channel(
            self.sender, self.receiver, 1000 * CKB, 0, 0, 0, other_config=public
        )
        self.edge = self.settlement.snapshot_channel(
            self.sender, self.receiver, channel_id, commitment_delay_epoch=delay
        )
        initial = self._balances(self.edge)
        payment_amount = 1_000_000
        self.expected_history = {}
        self.expected_balances = initial.copy()
        assert self._history(self.sender) == self.expected_history
        assert self._balances(self.edge) == self.expected_balances

        # 2. 使用原数据重启 sender；不重开通道，也不重发旧付款。
        restart_fiber(self.sender, peers=(self.receiver,))
        assert self._history(self.sender) == self.expected_history
        assert self._balances(self.edge) == self.expected_balances

        # 3. 原通道再发送一笔 sender → receiver，仍为 1,000,000 Shannon。
        deadline = time.monotonic() + 120
        preimage = self.generate_random_preimage()
        payment_hash = calculate_payment_hash(preimage, "ckb_hash")
        invoice = self.receiver.get_client().new_invoice(
            {
                "amount": hex(payment_amount),
                "currency": "Fibd",
                "payment_hash": payment_hash,
                "hash_algorithm": "ckb_hash",
            }
        )
        response = self.sender.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "max_parts": "0x1",
                "max_fee_amount": "0x0",
            }
        )
        assert response["payment_hash"] == payment_hash
        self._wait_history_committed(payment_hash, deadline)
        self.receiver.get_client().settle_invoice(
            {
                "payment_hash": payment_hash,
                "payment_preimage": preimage,
            }
        )
        result = self._wait_history_paid(payment_hash, preimage, deadline)
        assert result["payment_hash"] not in self.expected_history
        assert self._history(self.sender) == {
            **self.expected_history,
            result["payment_hash"]: result["payment_preimage"],
        }
        assert self._balances(self.edge) == [
            self.expected_balances[0] - payment_amount,
            self.expected_balances[1] + payment_amount,
        ]

    # TEST-MAP: MPP1705-12
    @pytest.mark.skip(reason="慢测：串行准备 1,000 笔付款；待完成慢测验证后启用。")
    def test_restart_with_thousand_payments(self):
        """
        1. 用当前版本建立通道，准备1,000 笔付款历史并核对本金。
        2. 保留原数据正常重启付款节点，重连原接收节点。
        3. 检查历史 hash/原像和余额不变，新付款成功且只扣付一次。
        """
        # 1. 拓扑：sender --1,000 CKB（public、右端 0）--> receiver。
        # 准备 1,000 笔历史；每笔 sender → receiver 为 1,000,000 Shannon，单片、零费用。
        self.sender, self.receiver = self.fiber1, self.fiber2
        delay = 0x10000000001  # 1 epoch；用于公开通道参数和到期保护核对。
        public = {"public": True, "commitment_delay_epoch": hex(delay)}
        channel_id = self.open_channel(
            self.sender, self.receiver, 1000 * CKB, 0, 0, 0, other_config=public
        )
        self.edge = self.settlement.snapshot_channel(
            self.sender, self.receiver, channel_id, commitment_delay_epoch=delay
        )
        initial = self._balances(self.edge)
        payment_amount = 1_000_000
        self.expected_history = {}
        for _ in range(1000):
            deadline = time.monotonic() + 120
            preimage = self.generate_random_preimage()
            payment_hash = calculate_payment_hash(preimage, "ckb_hash")
            invoice = self.receiver.get_client().new_invoice(
                {
                    "amount": hex(payment_amount),
                    "currency": "Fibd",
                    "payment_hash": payment_hash,
                    "hash_algorithm": "ckb_hash",
                }
            )
            response = self.sender.get_client().send_payment(
                {
                    "invoice": invoice["invoice_address"],
                    "max_parts": "0x1",
                    "max_fee_amount": "0x0",
                }
            )
            assert response["payment_hash"] == payment_hash
            self._wait_history_committed(payment_hash, deadline)
            self.receiver.get_client().settle_invoice(
                {
                    "payment_hash": payment_hash,
                    "payment_preimage": preimage,
                }
            )
            result = self._wait_history_paid(payment_hash, preimage, deadline)
            assert result["payment_hash"] not in self.expected_history
            self.expected_history[result["payment_hash"]] = result["payment_preimage"]
        self.expected_balances = [
            initial[0] - 1000 * payment_amount,
            initial[1] + 1000 * payment_amount,
        ]
        assert self._history(self.sender) == self.expected_history
        assert self._balances(self.edge) == self.expected_balances

        # 2. 使用原数据重启 sender；不重开通道，也不重发旧付款。
        restart_fiber(self.sender, peers=(self.receiver,))
        assert self._history(self.sender) == self.expected_history
        assert self._balances(self.edge) == self.expected_balances

        # 3. 原通道再发送一笔 sender → receiver，仍为 1,000,000 Shannon。
        deadline = time.monotonic() + 120
        preimage = self.generate_random_preimage()
        payment_hash = calculate_payment_hash(preimage, "ckb_hash")
        invoice = self.receiver.get_client().new_invoice(
            {
                "amount": hex(payment_amount),
                "currency": "Fibd",
                "payment_hash": payment_hash,
                "hash_algorithm": "ckb_hash",
            }
        )
        response = self.sender.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "max_parts": "0x1",
                "max_fee_amount": "0x0",
            }
        )
        assert response["payment_hash"] == payment_hash
        self._wait_history_committed(payment_hash, deadline)
        self.receiver.get_client().settle_invoice(
            {
                "payment_hash": payment_hash,
                "payment_preimage": preimage,
            }
        )
        result = self._wait_history_paid(payment_hash, preimage, deadline)
        assert result["payment_hash"] not in self.expected_history
        assert self._history(self.sender) == {
            **self.expected_history,
            result["payment_hash"]: result["payment_preimage"],
        }
        assert self._balances(self.edge) == [
            self.expected_balances[0] - payment_amount,
            self.expected_balances[1] + payment_amount,
        ]

    # TEST-MAP: MPP1705-12
    @pytest.mark.skip(reason="慢测：串行准备 8,000 笔付款；待完成慢测验证后启用。")
    def test_restart_with_large_payment_history(self):
        """
        1. 用当前版本建立通道，准备8,000 笔付款历史并核对本金。
        2. 保留原数据正常重启付款节点，重连原接收节点。
        3. 检查历史 hash/原像和余额不变，新付款成功且只扣付一次。
        """
        # 1. 拓扑：sender --1,000 CKB（public、右端 0）--> receiver。
        # 准备 8,000 笔历史；每笔 sender → receiver 为 1,000,000 Shannon，单片、零费用。
        self.sender, self.receiver = self.fiber1, self.fiber2
        delay = 0x10000000001  # 1 epoch；用于公开通道参数和到期保护核对。
        public = {"public": True, "commitment_delay_epoch": hex(delay)}
        channel_id = self.open_channel(
            self.sender, self.receiver, 1000 * CKB, 0, 0, 0, other_config=public
        )
        self.edge = self.settlement.snapshot_channel(
            self.sender, self.receiver, channel_id, commitment_delay_epoch=delay
        )
        initial = self._balances(self.edge)
        payment_amount = 1_000_000
        self.expected_history = {}
        for _ in range(8000):
            deadline = time.monotonic() + 120
            preimage = self.generate_random_preimage()
            payment_hash = calculate_payment_hash(preimage, "ckb_hash")
            invoice = self.receiver.get_client().new_invoice(
                {
                    "amount": hex(payment_amount),
                    "currency": "Fibd",
                    "payment_hash": payment_hash,
                    "hash_algorithm": "ckb_hash",
                }
            )
            response = self.sender.get_client().send_payment(
                {
                    "invoice": invoice["invoice_address"],
                    "max_parts": "0x1",
                    "max_fee_amount": "0x0",
                }
            )
            assert response["payment_hash"] == payment_hash
            self._wait_history_committed(payment_hash, deadline)
            self.receiver.get_client().settle_invoice(
                {
                    "payment_hash": payment_hash,
                    "payment_preimage": preimage,
                }
            )
            result = self._wait_history_paid(payment_hash, preimage, deadline)
            assert result["payment_hash"] not in self.expected_history
            self.expected_history[result["payment_hash"]] = result["payment_preimage"]
        self.expected_balances = [
            initial[0] - 8000 * payment_amount,
            initial[1] + 8000 * payment_amount,
        ]
        assert self._history(self.sender) == self.expected_history
        assert self._balances(self.edge) == self.expected_balances

        # 2. 使用原数据重启 sender；不重开通道，也不重发旧付款。
        restart_fiber(self.sender, peers=(self.receiver,))
        assert self._history(self.sender) == self.expected_history
        assert self._balances(self.edge) == self.expected_balances

        # 3. 原通道再发送一笔 sender → receiver，仍为 1,000,000 Shannon。
        deadline = time.monotonic() + 120
        preimage = self.generate_random_preimage()
        payment_hash = calculate_payment_hash(preimage, "ckb_hash")
        invoice = self.receiver.get_client().new_invoice(
            {
                "amount": hex(payment_amount),
                "currency": "Fibd",
                "payment_hash": payment_hash,
                "hash_algorithm": "ckb_hash",
            }
        )
        response = self.sender.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "max_parts": "0x1",
                "max_fee_amount": "0x0",
            }
        )
        assert response["payment_hash"] == payment_hash
        self._wait_history_committed(payment_hash, deadline)
        self.receiver.get_client().settle_invoice(
            {
                "payment_hash": payment_hash,
                "payment_preimage": preimage,
            }
        )
        result = self._wait_history_paid(payment_hash, preimage, deadline)
        assert result["payment_hash"] not in self.expected_history
        assert self._history(self.sender) == {
            **self.expected_history,
            result["payment_hash"]: result["payment_preimage"],
        }
        assert self._balances(self.edge) == [
            self.expected_balances[0] - payment_amount,
            self.expected_balances[1] + payment_amount,
        ]

    @staticmethod
    def _remaining_payment_time(deadline):
        value = deadline - time.monotonic()
        assert value > 0, "真实新付款耗尽观察窗口"
        return math.ceil(value)

    def _wait_history_committed(self, payment_hash, deadline):
        """仅观察用例已发起的单片付款，不创建发票或发送付款。"""
        self.wait_invoice_state(
            self.receiver,
            payment_hash,
            "Received",
            timeout=self._remaining_payment_time(deadline),
        )

        def single_committed():
            tlcs = [
                t
                for c in self.sender.get_client().list_channels({})["channels"]
                for t in c["pending_tlcs"]
                if t["payment_hash"] == payment_hash
            ]
            return len(tlcs) == 1 and tlcs[0]["status"] == {"Outbound": "Committed"}

        wait_for_result(
            "历史付款实际单片提交",
            single_committed,
            self._remaining_payment_time(deadline),
        )

    def _wait_history_paid(self, payment_hash, preimage, deadline):
        """只核对原像、Success 与锁定释放，结算 RPC 留在用例中。"""
        self.wait_payment_state(
            self.sender,
            payment_hash,
            "Success",
            timeout=self._remaining_payment_time(deadline),
        )
        result = self.sender.get_client().get_payment({"payment_hash": payment_hash})
        assert (
            result["status"] == "Success" and result.get("payment_preimage") == preimage
        )
        assert not result.get("failed_error")
        wait_for_result(
            "历史付款单通道无锁定",
            lambda: all(
                int(c[key], 16) == 0
                for f in (self.sender, self.receiver)
                for c in f.get_client().list_channels({})["channels"]
                for key in ("offered_tlc_balance", "received_tlc_balance")
            ),
            self._remaining_payment_time(deadline),
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
