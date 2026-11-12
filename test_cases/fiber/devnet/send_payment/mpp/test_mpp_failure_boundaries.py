"""MPP：未知结果、已失败一片与确定超时的外部资金边界。"""

import time

from framework.config import DEFAULT_MIN_DEPOSIT_CKB
from test_cases.fiber.devnet.send_payment.mpp.mpp_fixture import (
    MppFixture,
    CKB,
    AMOUNT,
    RESULT_WAIT,
)
from framework.helper.fiber_ops import (
    calculate_payment_hash,
    wait_for_result,
    start_fiber,
    restart_fiber,
)


class TestMppFailureBoundaries(MppFixture):
    # TEST-MAP: MPP1705-06
    def test_unknown_chain_results_do_not_refund_upstream(self):
        """
        1. 建立无原像的 trampoline 两片持单，停止 R 并强关两条下游。
        2. 到期前观察完整 420 秒：链上分片未消费，上游原 TLC 全额锁定。
        3. 另一笔独立健康付款成功，原付款仍未成功且上游锁定未释放。
        """
        # 1. Received/Committed 不等于领取，原像只保留在测试进程。
        self.prepare_payment("trampoline")
        self.receiver.stop()
        chains = [
            self.force_close_channel(edge, self.relay) for edge in self.split_channels
        ]
        self.node.getClient().generate_epochs("0x1", wait_time=0)
        self.assert_expiry_margin(RESULT_WAIT + 120)

        # 2. 每次采样同时检查原 outpoint 和开放上游，禁止以强关退款代替传播。
        end = time.monotonic() + RESULT_WAIT
        while time.monotonic() < end:
            assert self.assert_no_preimage(self.sender)["status"] == "Inflight"
            self.settlement.assert_payment_locked(
                self.upstream_channel, self.payment_hash
            )
            assert all(
                self.settlement.find_spending_transaction(c["outpoint"]) is None
                for c in chains
            )
            time.sleep(3)

        # 3. 新付款只证明健康通道可用，不替代原付款的持单断言。
        self.assert_healthy_payment(self.sender, self.relay, self.upstream_channel)
        assert self.assert_no_preimage(self.sender)["status"] == "Inflight"
        rows = self.settlement.assert_channel_open(self.upstream_channel)
        assert int(rows[0]["offered_tlc_balance"], 16) == self.upstream_channel["sent"]
        assert int(rows[1]["received_tlc_balance"], 16) == self.upstream_channel["sent"]

    # TEST-MAP: MPP1705-14
    def test_first_failed_split_waits_for_isolated_split_result(self):
        """
        1. 建立 T–R 与 T–M–R 两路，逐跳两端提交后停 M 并确认断连。
        2. R 取消整张发票，核实 A 已完成失败握手；B 及上游原 TLC 保持锁定 420 秒。
        3. 原目录恢复 M，所有原通道保持开放，原付款全部失败并退回含预算的全部金额。
        """
        # 1. 停稳定节点形成宽隔离窗口，不控制内部消息先后顺序。
        self.prepare_payment("trampoline_with_bridge")
        bridge_key = self.bridge.get_pubkey()
        self.bridge.stop()
        wait_for_result(
            "T/R 均确认 M 离线",
            lambda: all(
                bridge_key
                not in {p["pubkey"] for p in f.get_client().list_peers()["peers"]}
                for f in (self.relay, self.receiver)
            ),
        )
        self.receiver.get_client().cancel_invoice({"payment_hash": self.payment_hash})
        self.wait_invoice_state(
            self.receiver, self.payment_hash, "Cancelled", timeout=120
        )

        # 2. A 两端余额恢复且锁定为零是送达证据，不用聚合 Failed 冒充。
        wait_for_result(
            "A 两端失败握手结束",
            lambda: self.settlement.payment_is_settled(
                self.split_channels[0], self.payment_hash, success=False
            ),
            RESULT_WAIT,
        )
        end = time.monotonic() + RESULT_WAIT
        while time.monotonic() < end:
            assert self.settlement.payment_is_settled(
                self.split_channels[0], self.payment_hash, success=False
            )
            self.settlement.assert_payment_locked(
                self.split_channels[1], self.payment_hash, offline=(self.bridge,)
            )
            self.settlement.assert_payment_locked(
                self.upstream_channel, self.payment_hash
            )
            assert self.assert_no_preimage(self.sender)["status"] == "Inflight"
            for edge in self.channels:
                self.settlement.assert_channel_open(edge, offline=(self.bridge,))
            assert all(
                bridge_key
                not in {p["pubkey"] for p in f.get_client().list_peers()["peers"]}
                for f in (self.relay, self.receiver)
            )
            time.sleep(3)

        # 3. 重连起持续检查原 funding 未消费；无需强关或新付款来收尾。
        for edge in self.channels:
            self.settlement.assert_channel_open(edge, offline=(self.bridge,))
        start_fiber(self.bridge, peers=(self.relay, self.receiver))
        self.assert_expiry_margin(RESULT_WAIT)

        def refunded():
            for edge in self.channels:
                self.settlement.assert_channel_open(edge)
            payment = self.assert_no_preimage(self.sender)
            cleared = [
                self.settlement.payment_is_settled(
                    edge, self.payment_hash, success=False
                )
                for edge in self.channels
            ]
            return payment["status"] == "Failed" and all(cleared)

        wait_for_result("最后一片失败后全额退款且通道开放", refunded, RESULT_WAIT)
        assert (
            self.upstream_channel["sent"] > AMOUNT
        ), "必须覆盖正 trampoline 预算的完整退款"

    # TEST-MAP: MPP1705-09
    def test_direct_timeout_refund_then_sender_restart_and_new_payment(self):
        """
        1. 普通 P–R 无原像 hold 付款提交后停止 R，P 强关原通道。
        2. 按公开 TLC 真实到期等待，核对准确消费链无原像且本金回到 P 的 lock。
        3. 重启 P，旧付款失败且无原像，通过另一健康通道完成新付款。
        """
        # 1. 不使用 trampoline；后续健康通道不属于原付款路径。
        self.sender, self.receiver = self.fiber1, self.fiber2
        healthy = self.start_new_fiber(self.generate_account(10000))
        old = self.open_payment_channel(self.sender, self.receiver, 1000 * CKB)
        probe = self.open_payment_channel(self.sender, healthy, 1000 * CKB)
        self.channels = [old]
        self.hash_algorithm = "ckb_hash"
        self.preimage = self.generate_random_preimage()
        self.payment_hash = calculate_payment_hash(self.preimage, self.hash_algorithm)
        invoice = self.receiver.get_client().new_invoice(
            {
                "amount": hex(100 * CKB),
                "currency": "Fibd",
                "payment_hash": self.payment_hash,
                "hash_algorithm": self.hash_algorithm,
                "final_expiry_delta": hex(10_800_000),
                "expiry": hex(86400),
            }
        )
        self.sender.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "tlc_expiry_limit": hex(172_800_000),
            }
        )
        self.wait_invoice_state(
            self.receiver, self.payment_hash, "Received", timeout=120
        )
        self.settlement.record_committed_tlcs(old, self.payment_hash)
        self.assert_no_preimage(self.sender)
        self.receiver.stop()
        chain = self.force_close_channel(old, self.sender)

        # 2. 真实到期加链上结算预算；公开查询中的名义到期并不等于已退款。
        expiry = max(int(t["expiry"], 16) / 1000 for t in old["tlcs"])
        deadline = time.monotonic() + max(0, expiry - time.time()) + 1800
        while True:
            assert time.monotonic() < deadline, f"退款未在真实到期+1800秒完成: {chain}"
            self.settlement.advance(
                chain, self.payment_hash, old["sent"], self.preimage, allow_claim=False
            )
            principal = (
                int(old["before"][0]["local_balance"], 16) + DEFAULT_MIN_DEPOSIT_CKB
            )
            # R 离线时其保证金可留在派生 cell，P 的全部本金才是本例退款 oracle。
            if principal - chain["fee"] <= chain["net"][0] <= principal:
                break
            self.node.getClient().generate_epochs("0x1", wait_time=0)
            time.sleep(5 if time.time() >= expiry else min(30, expiry - time.time()))
        assert not chain["claimed"] and chain["seen"]
        assert 0 <= chain["fee"] < CKB // 100

        # 3. 不重新提交旧付款；检查旧结果和独立健康付款。
        restart_fiber(self.sender, peers=(healthy,))
        wait_for_result(
            "普通原付款 Failed 且无原像",
            lambda: self.assert_no_preimage(self.sender)["status"] == "Failed",
            RESULT_WAIT,
        )
        self.assert_healthy_payment(self.sender, healthy, probe)
        assert self.assert_no_preimage(self.sender)["status"] == "Failed"
