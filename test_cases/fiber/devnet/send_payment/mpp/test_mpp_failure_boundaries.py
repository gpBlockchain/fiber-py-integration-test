"""Issue 1705：未知结果、已失败一片与确定超时的外部资金边界。"""

import time

from framework.config import DEFAULT_MIN_DEPOSIT_CKB
from test_cases.fiber.devnet.send_payment.mpp import mpp_fixture as fixture


class TestMppFailureBoundaries(fixture.MppFixture):
    # TEST-MAP: MPP1705-06
    def test_unknown_chain_results_do_not_refund_upstream(self):
        """
        1. 建立无原像的 trampoline 两片持单，停止 R 并强关两条下游。
        2. 到期前观察完整 420 秒：链上分片未消费，上游原 TLC 全额锁定。
        3. 另一笔独立健康付款成功，原付款仍未成功且上游锁定未释放。
        """
        # 1. Received/Committed 不等于领取，原像只保留在测试进程。
        self._topology("T")
        self.r.stop()
        chains = [self._close(edge, self.m) for edge in self.split]
        self.node.getClient().generate_epochs("0x1", wait_time=0)
        self._expiry_margin(fixture.RESULT_WAIT + 120)

        # 2. 每次采样同时检查原 outpoint 和开放上游，禁止以强关退款代替传播。
        end = time.monotonic() + fixture.RESULT_WAIT
        while time.monotonic() < end:
            assert self._no_preimage(self.p)["status"] == "Inflight"
            self._held(self.up)
            assert all(self._spend(c["outpoint"]) is None for c in chains)
            time.sleep(3)

        # 3. 新付款只证明健康通道可用，不替代原付款的持单断言。
        self._healthy_probe(self.p, self.m, self.up)
        assert self._no_preimage(self.p)["status"] == "Inflight"
        rows = self._open(self.up)
        assert int(rows[0]["offered_tlc_balance"], 16) == self.up["sent"]
        assert int(rows[1]["received_tlc_balance"], 16) == self.up["sent"]

    # TEST-MAP: MPP1705-14
    def test_first_failed_split_waits_for_isolated_split_result(self):
        """
        1. 建立 T–R 与 T–M–R 两路，逐跳两端提交后停 M 并确认断连。
        2. R 取消整张发票，核实 A 已完成失败握手；B 及上游原 TLC 保持锁定 420 秒。
        3. 原目录恢复 M，所有原通道保持开放，原付款全部失败并退回含预算的全部金额。
        """
        # 1. 停稳定节点形成宽隔离窗口，不控制内部消息先后顺序。
        self._topology("TF")
        bridge_key = self.bridge.get_pubkey()
        self.bridge.stop()
        self._wait(
            "T/R 均确认 M 离线",
            lambda: all(
                bridge_key
                not in {p["pubkey"] for p in f.get_client().list_peers()["peers"]}
                for f in (self.m, self.r)
            ),
        )
        self.r.get_client().cancel_invoice({"payment_hash": self.hash})
        self.wait_invoice_state(self.r, self.hash, "Cancelled", timeout=120)

        # 2. A 两端余额恢复且锁定为零是送达证据，不用聚合 Failed 冒充。
        self._wait(
            "A 两端失败握手结束",
            lambda: self._cleared(self.split[0], success=False),
            fixture.RESULT_WAIT,
        )
        end = time.monotonic() + fixture.RESULT_WAIT
        while time.monotonic() < end:
            assert self._cleared(self.split[0], success=False)
            self._held(self.split[1], offline=(self.bridge,))
            self._held(self.up)
            assert self._no_preimage(self.p)["status"] == "Inflight"
            for edge in self.edges:
                self._open(edge, offline=(self.bridge,))
            assert all(
                bridge_key
                not in {p["pubkey"] for p in f.get_client().list_peers()["peers"]}
                for f in (self.m, self.r)
            )
            time.sleep(3)

        # 3. 重连起持续检查原 funding 未消费；无需强关或新付款来收尾。
        for edge in self.edges:
            self._open(edge, offline=(self.bridge,))
        self._start(self.bridge, peers=(self.m, self.r))
        self._expiry_margin(fixture.RESULT_WAIT)

        def refunded():
            for edge in self.edges:
                self._open(edge)
            payment = self._no_preimage(self.p)
            cleared = [self._cleared(edge, success=False) for edge in self.edges]
            return payment["status"] == "Failed" and all(cleared)

        self._wait("最后一片失败后全额退款且通道开放", refunded, fixture.RESULT_WAIT)
        assert self.up["sent"] > fixture.AMOUNT, "必须覆盖正 trampoline 预算的完整退款"

    # TEST-MAP: MPP1705-09
    def test_direct_timeout_refund_then_sender_restart_and_new_payment(self):
        """
        1. 普通 P–R 无原像 hold 付款提交后停止 R，P 强关原通道。
        2. 按公开 TLC 真实到期等待，核对准确消费链无原像且本金回到 P 的 lock。
        3. 重启 P，旧付款失败且无原像，通过另一健康通道完成新付款。
        """
        # 1. 不使用 trampoline；后续健康通道不属于原付款路径。
        self.p, self.r = self.fiber1, self.fiber2
        healthy = self.start_new_fiber(self.generate_account(10000))
        old = self._edge(self.p, self.r, 1000 * fixture.CKB)
        probe = self._edge(self.p, healthy, 1000 * fixture.CKB)
        self.edges = [old]
        self.algorithm = "ckb_hash"
        self.preimage = self.generate_random_preimage()
        self.hash = fixture.payment_hash(self.preimage, self.algorithm)
        invoice = self.r.get_client().new_invoice(
            {
                "amount": hex(100 * fixture.CKB),
                "currency": "Fibd",
                "payment_hash": self.hash,
                "hash_algorithm": self.algorithm,
                "final_expiry_delta": hex(10_800_000),
                "expiry": hex(86400),
            }
        )
        self.p.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "tlc_expiry_limit": hex(172_800_000),
            }
        )
        self.wait_invoice_state(self.r, self.hash, "Received", timeout=120)
        self._capture_committed(old)
        self._no_preimage(self.p)
        self.r.stop()
        chain = self._close(old, self.p)

        # 2. 真实到期加链上结算预算；公开查询中的名义到期并不等于已退款。
        expiry = max(int(t["expiry"], 16) / 1000 for t in old["tlcs"])
        deadline = time.monotonic() + max(0, expiry - time.time()) + 1800
        while True:
            assert time.monotonic() < deadline, f"退款未在真实到期+1800秒完成: {chain}"
            self._advance_chain(chain, claim=False)
            principal = (
                int(old["before"][0]["local_balance"], 16) + DEFAULT_MIN_DEPOSIT_CKB
            )
            # R 离线时其保证金可留在派生 cell，P 的全部本金才是本例退款 oracle。
            if principal - chain["fee"] <= chain["net"][0] <= principal:
                break
            self.node.getClient().generate_epochs("0x1", wait_time=0)
            time.sleep(5 if time.time() >= expiry else min(30, expiry - time.time()))
        assert not chain["claimed"] and chain["seen"]
        assert 0 <= chain["fee"] < fixture.CKB // 100

        # 3. 不重新提交旧付款；检查旧结果和独立健康付款。
        self._restart(self.p, peers=(healthy,))
        self._wait(
            "普通原付款 Failed 且无原像",
            lambda: self._no_preimage(self.p)["status"] == "Failed",
            fixture.RESULT_WAIT,
        )
        self._healthy_probe(self.p, healthy, probe)
        assert self._no_preimage(self.p)["status"] == "Failed"
