"""Issue 1705：成功证明先于最终资金交付，重启/升级和纯链上恢复。"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from test_cases.fiber.devnet.send_payment.mpp import mpp_fixture as fixture


class TestMppSuccessRecovery(fixture.MppFixture):
    # TEST-MAP: MPP1705-01
    def test_early_success_sha256(self):
        """
        1. 两条容量不足的通道构成 sha256 MPP，逐跳两端提交后强关一条。
        2. 暂停出块，结算发票；链上片仍未消费时发送方已成功且原像正确。
        3. 恢复出块，核对接收本金、每端净额与独立链费，不重复交付。
        """
        # 1. 两片 Committed 之后才强关。
        self._topology("D", "sha256")
        chain = self._close(self.split[0], self.m)

        # 2. 保留稳定的链上未完成窗口，不以最终成功代替提前成功。
        self.node.stop_miner()
        try:
            self._settle()
            self._success(self.p)
            assert self._spend(chain["outpoint"]) is None
        finally:
            self.node.start_miner()

        # 3. 原付款精确消费链和开放通道都须只交付应有金额。
        self._final_funds(chain)

    # TEST-MAP: MPP1705-01
    def test_early_success_ckb_hash(self):
        """
        1. 以独立 CKB hash 构建两片 MPP 并强关其中一条。
        2. 暂停出块，交出原像，断言 P 提前成功且链上片尚未完成。
        3. 恢复出块核对原付款全路径资金及手续费。
        """
        # 1. 输入只改变 hash 算法，资金与分片前提保持一致。
        self._topology("D", "ckb_hash")
        chain = self._close(self.split[0], self.m)

        # 2. 成功查询与链上未消费证据必须同时成立。
        self.node.stop_miner()
        try:
            self._settle()
            self._success(self.p)
            assert self._spend(chain["outpoint"]) is None
        finally:
            self.node.start_miner()

        # 3. 不以 RPC Success 替代实际交付。
        self._final_funds(chain)

    # TEST-MAP: MPP1705-02
    def test_trampoline_settles_upstream_before_chain_finishes(self):
        """
        1. P–T 两片转付给 R，冻结含正费用预算的上游金额并强关下游一条。
        2. 暂停链，R 结算后 T/P 均成功且开放上游只扣一次、锁定释放。
        3. 恢复链，核对 R 本金和 T 扣除实际链费后的全路径资金。
        """
        # 1. 明确走 trampoline，禁止旁路与单片付款冒充。
        self._topology("T")
        chain = self._close(self.split[0], self.r)

        # 2. 下游仍待链上交付时，上游已经结清。
        self.node.stop_miner()
        try:
            self._settle()
            self._success(self.m)
            self._success(self.p)
            self._wait(
                "上游只结算一次", lambda: self._cleared(self.up), fixture.RESULT_WAIT
            )
            assert self._spend(chain["outpoint"]) is None
        finally:
            self.node.start_miner()

        # 3. 各通道守恒和分所有者链费共同证明中继无本金损失。
        self._final_funds(chain)

    # TEST-MAP: MPP1705-03
    def test_sender_restart_preserves_success_and_remaining_work(self):
        """
        1. 建立 P 已成功且一个分片尚在链上的状态。
        2. 保留数据目录重启 P，原 hash/原像仍成功且链上片仍未完成。
        3. 恢复链并核对只交付一次，再通过原健康通道付款。
        """
        # 1. 强关并暂停出块，交出原像后观察早期成功。
        self._topology("D")
        closed = max(self.split, key=lambda edge: edge["sent"])
        healthy = next(edge for edge in self.split if edge is not closed)
        chain = self._close(closed, self.m)
        self.node.stop_miner()
        try:
            self._settle()
            self._success(self.p)
            assert self._spend(chain["outpoint"]) is None

            # 2. 重启不重新 send_payment，也不替换数据。
            self._restart(self.p, peers=(self.m,))
            self._success(self.p)
            assert self._spend(chain["outpoint"]) is None
        finally:
            self.node.start_miner()

        # 3. 原款完整交付后健康通道再付一笔，原款仍保持成功。
        self._final_funds(chain)
        self._healthy_probe(self.p, self.m, healthy)
        self._success(self.p)

    # TEST-MAP: MPP1705-04
    def test_offline_upstream_and_trampoline_restart_settle_once(self):
        """
        1. 两片提交后停 P，R 强关一条并结算，T 在链未完成时取得成功证明。
        2. 重启 T，再恢复 P；开放上游用原付款只结算一次。
        3. 完成下游交付，再次重启 T/P 并核对余额和锁定不变化。
        """
        # 1. P 在原像交给 R 前停机。
        self._topology("T")
        self.p.stop()
        chain = self._close(self.split[0], self.r)
        self.node.stop_miner()
        try:
            self._settle()
            self._success(self.m)
            assert self._spend(chain["outpoint"]) is None

            # 2. T 的原数据重启后才恢复上游。
            self._restart(self.m, peers=(self.r,))
            self._success(self.m)
            self._start(self.p, peers=(self.m,))
            self._success(self.p)
            self._wait(
                "离线上游恢复后一次结算",
                lambda: self._cleared(self.up),
                fixture.RESULT_WAIT,
            )
        finally:
            self.node.start_miner()

        # 3. 第二次重启后仍按原始余额验证，而不是把错误新余额当基准。
        self._final_funds(chain)
        self._restart(self.m, peers=(self.r, self.p))
        self._restart(self.p, peers=(self.m,))
        self._success(self.p)
        for edge in (self.up, self.split[1]):
            self._wait(
                "再次重启无重放扣款",
                lambda e=edge: self._cleared(e),
                fixture.RESULT_WAIT,
            )

    # TEST-MAP: MPP1705-05
    def test_sender_recovers_preimage_only_from_chain(self):
        """
        1. P 无原像时停机，M 强关一条 P–M 后由 R 结算。
        2. 确认原像领取的链上消费，再停 M；P 无 Fiber 对端启动并恢复成功。
        3. 恢复 M，原付款继续成功且全路径金额正确。
        """
        # 1. 原像只在 P 停机后交给 R。
        self._topology("D")
        self._no_preimage(self.p)
        self.p.stop()
        chain = self._close(self.split[0], self.m)
        self._settle()

        # 2. 先证明原像已实际用于原 TLC 领取，再隔离 P 的唯一对端。
        self._chain_until(chain, claimed_only=True)
        assert chain["claimed"]
        self.m.stop()
        # R 也暂时离线，避免已持久化的 gossip 地址自动接入 P。
        self.r.stop()
        self._start(self.p)
        self._success(self.p, isolated=True)
        assert self.p.get_client().list_peers()["peers"] == []

        # 3. 重连仅负责剩余结算，不作为原像来源证明。
        self._start(self.r)
        self._start(self.m, peers=(self.p, self.r))
        self._final_funds(chain)

    # TEST-MAP: MPP1705-13
    def test_trampoline_chain_preimage_then_offline_upstream(self):
        """
        1. P/T 都无原像时停机；R 强关一片并结算，证明实际链上原像领取。
        2. 停 R，T 无 Fiber 对端启动，从首次 node_info 后 420 秒内恢复正确成功。
        3. R 仍停机时只恢复 P，上游开放通道只结算一次，再恢复 R 核对全部资金。
        """
        # 1. 冻结 U=A+F，原像未交给任意节点前停止 P/T。
        self._topology("T")
        self._no_preimage(self.p)
        self._no_preimage(self.m)
        self.p.stop()
        self.m.stop()
        chain = self._close(self.split[0], self.r)
        # 对端全部离线时 Paid 可能要等实际领取；先登记原像并推进链。
        self._settle(wait_paid=False)
        self._chain_until(chain, claimed_only=True)
        assert chain["claimed"]

        # 2. 隔离来源阶段禁止 Fiber 重连，也不向 T 调用提供原像的 RPC。
        self.r.stop()
        started = self._start(self.m)
        self._success(
            self.m,
            isolated=True,
            timeout=fixture.RESULT_WAIT - (time.monotonic() - started),
        )
        rows = self._open(self.up, offline=(self.p,))
        assert int(rows[0]["received_tlc_balance"], 16) == self.up["sent"]
        assert {
            (t["id"], t["amount"])
            for t in rows[0]["pending_tlcs"]
            if t["payment_hash"] == self.hash
        } == {(t["id"], t["amount"]) for t in self.up["tlcs"]}
        self._expiry_margin(2 * fixture.RESULT_WAIT)

        # 3. R 离线时上游已领取，随后才恢复剩余下游。
        self._start(self.p, peers=(self.m,))
        self._success(self.p)
        self._wait(
            "R 离线时上游结清", lambda: self._cleared(self.up), fixture.RESULT_WAIT
        )
        assert {p["pubkey"] for p in self.m.get_client().list_peers()["peers"]} == {
            self.p.get_pubkey()
        }
        self._start(self.r, peers=(self.m,))
        self._final_funds(chain)


class TestMppUpgrade(fixture.MppFixture):
    def setup_method(self, method):
        self.head_config = self.fiber_version
        base = os.environ.get(
            "MPP1705_BASE_FNN",
            str(Path(fixture.get_project_root()) / "download/fiber/current/fnn"),
        )
        version = subprocess.check_output([base, "--version"], text=True)
        assert "7aa1d5d" in version, f"升级前必须是已评审 base 7aa1d5d: {version}"
        self.fiber_version = fixture.binary_config(base)
        super().setup_method(method)

    # TEST-MAP: MPP1705-10
    def test_upgrade_partial_success_and_paginated_queries(self):
        """
        1. base 经正常操作生成成功/失败对照和已有原像、链上片未完成的旧付款。
        2. 停 P 并备份原数据，升级同一副本到 head，查询须显示原像及 Success。
        3. 按 limit=1 遍历无过滤/各状态列表，核对集合无漏重，再核对原款交付。
        """
        # 1. 独立控制通道不通往目标 R，既不改变两片路由也不改写数据库。
        control = self.start_new_fiber(
            self.generate_account(10000), fiber_version=self.fiber_version
        )
        self._edge(self.fiber1, control, 200 * fixture.CKB)
        ok = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": control.get_pubkey(),
                "amount": hex(fixture.CKB),
                "keysend": True,
            }
        )
        self.wait_payment_state(self.fiber1, ok["payment_hash"], "Success", timeout=120)
        bad_hash = fixture.payment_hash(self.generate_random_preimage(), "ckb_hash")
        invoice = control.get_client().new_invoice(
            {
                "amount": hex(fixture.CKB),
                "currency": "Fibd",
                "payment_hash": bad_hash,
                "hash_algorithm": "ckb_hash",
            }
        )
        self.fiber1.get_client().send_payment({"invoice": invoice["invoice_address"]})
        self.wait_invoice_state(control, bad_hash, "Received", timeout=120)
        control.get_client().cancel_invoice({"payment_hash": bad_hash})
        self.wait_payment_state(
            self.fiber1, bad_hash, "Failed", timeout=fixture.RESULT_WAIT
        )
        self._topology("D")
        chain = self._close(self.split[0], self.m)
        self.node.stop_miner()
        try:
            self._settle()
            old = self._wait("base 已获原像但尚未成功", lambda: self._old_partial())
            assert self._spend(chain["outpoint"]) is None
            print("base query: " + json.dumps(old), flush=True)

            # 2. 停机复制完整目录，备份只读保留；在原运行目录替换可执行版本。
            self.p.stop()
            backup = Path(self.p.tmp_path).with_name("payer-pre-upgrade-backup")
            assert not backup.exists(), backup
            shutil.copytree(self.p.tmp_path, backup)
            self._start(self.p, peers=(self.m,), config=self.head_config)
            self._success(self.p)

            # 3. 游标必须前进，逐页全量集合与独立创建的 hash 对照。
            expected = {self.hash, ok["payment_hash"], bad_hash}
            all_rows = self._pages()
            assert set(all_rows) == expected, all_rows
            succeeded = self._pages("Success")
            assert set(succeeded) == {self.hash, ok["payment_hash"]}
            assert set(self._pages("Failed")) == {bad_hash}
            for status in ("Created", "Inflight"):
                assert self.hash not in self._pages(status)
            assert all_rows[self.hash]["payment_preimage"] == self.preimage
            assert succeeded[self.hash]["payment_preimage"] == self.preimage
            assert self._spend(chain["outpoint"]) is None
        finally:
            self.node.start_miner()
        self._final_funds(chain)

    def _old_partial(self):
        row = self.p.get_client().get_payment({"payment_hash": self.hash})
        if row.get("payment_preimage") != self.preimage:
            return None
        assert (
            row["status"] != "Success"
        ), "base 未产生设计要求的旧在途状态，需核对构建身份"
        return row

    def _pages(self, status=None):
        cursor, seen_cursors, rows = None, set(), {}
        for _ in range(20):
            request = {"limit": "0x1"}
            if status is not None:
                request["status"] = status
            if cursor is not None:
                request["after"] = cursor
            page = self.p.get_client().list_payments(request)
            entries = page["payments"]
            if not entries:
                return rows
            assert len(entries) == 1
            row = entries[0]
            assert row["payment_hash"] not in rows, page
            if status:
                assert row["status"] == status
            rows[row["payment_hash"]] = row
            cursor = page["last_cursor"]
            assert cursor not in seen_cursors
            seen_cursors.add(cursor)
        raise AssertionError("分页未在固定上限内结束")
