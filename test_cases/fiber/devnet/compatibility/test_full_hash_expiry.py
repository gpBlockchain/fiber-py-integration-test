"""H32V2-10 / H32V2-25: Legacy/V1 强关后无原像到期回收。

合并自 test_full_hash_timeout.py 与 test_full_hash_final_sweep.py：

- H32V2-10：一笔只有测试进程知道原像、节点从未登记的 hold TLC 留在强关承诺里。
  承诺延迟满足但 TLC 未到期时，承诺 cell 必须保持 live，付款保持 Inflight；
  快进到 TLC 到期之后，Watchtower 以无原像 witness 回收，付款只能 Failed。
- H32V2-25：两笔金额不同、原像都不公布的已承诺 hold TLC 留在强关承诺里。快进到
  两笔 TLC 都到期后，Watchtower 沿派生链消费最后一格 commitment cell；此时所有
  剩余 TLC 都必须形成精确的无原像消费证据，付款只能 Failed，通道正常关闭。

Run serially with other devnet tests (the shared CKB CLI uses /tmp files).
"""

import secrets
import socket
import subprocess
import time

from framework.basic_fiber import COMMIT_LOCK_CODE_HASH
from framework.helper.settlement_witness import SettlementWitness
from framework.test_fiber import FiberConfigPath
from framework.util import ckb_hash
from test_cases.fiber.devnet.compatibility.contract_upgrade_support import (
    CKB,
    ROOT,
    FullHashChannelSupport,
    tlc_is_terminal,
)


class TestFullHashExpiry(FullHashChannelSupport):
    tmp_path_name = f"report/h32v2-expiry-{time.time_ns()}"
    ckb_rpc_port, ckb_p2p_port = 23214, 23215
    fiber1_rpc_port, fiber1_p2p_port = 23228, 23227
    fiber2_rpc_port, fiber2_p2p_port = 23229, 23230
    extra_fiber_rpc_port, extra_fiber_p2p_port = 23300, 23400
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 2}

    @classmethod
    def teardown_class(cls):
        # 用例把系统时间向前跳过，收尾时必须恢复（SETTLE-04 的做法）。
        try:
            if getattr(cls, "_clock_advanced", False):
                cls.restore_time()
        finally:
            super().teardown_class()

    @classmethod
    def setup_class(cls):
        for port in (
            cls.ckb_rpc_port,
            cls.ckb_p2p_port,
            cls.fiber1_rpc_port,
            cls.fiber1_p2p_port,
            cls.fiber2_rpc_port,
            cls.fiber2_p2p_port,
            cls.extra_fiber_rpc_port,
            cls.extra_fiber_p2p_port,
            cls.extra_fiber_rpc_port + 1,
            cls.extra_fiber_p2p_port + 1,
        ):
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", port))
        # Legacy 对照必须是 PR base：无原像超时与剩余 TLC 收尾都要在 57/85 布局上同样成立。
        assert "9a561b3" in subprocess.check_output(
            [ROOT / FiberConfigPath.V091_DEV.fiber_bin_path, "--version"], text=True
        )
        super().setup_class()
        cls.ckb = cls.node.getClient()
        cls.new1, cls.new2 = cls.fiber1, cls.fiber2
        # generate_account 是实例方法，Legacy 对端在 setUp 里惰性启动一次。
        cls.processes = cls.node_processes()

    def setUp(self):
        cls = self.__class__
        if not hasattr(cls, "legacy_peer"):
            cls.legacy_peer = self.start_new_fiber(
                self.generate_account(5000), fiber_version=FiberConfigPath.V091_DEV
            )

    # ---------------------------------------------------------------- helpers

    def tlc_of(self, fiber, payment_hash):
        for tlc in self.channel(fiber)["pending_tlcs"]:
            if tlc["payment_hash"] == payment_hash:
                return tlc
        self.fail(
            f"TLC {payment_hash} 不在 {fiber.rpc_port} 的通道里: {self.channel(fiber)}"
        )

    def hours_past_expiry(self, tlc):
        """SETTLE-04 的时间跳跃：多跳几小时，确保区块中位时间晚于 TLC 到期。"""
        expiry_ms = int(tlc["expiry"], 16)
        remain_seconds = expiry_ms / 1000.0 - time.time()
        return max(1, int(remain_seconds // 3600) + 2)

    def mine_watchtower_rounds(self, rounds=4):
        interval = self.start_fiber_config["fiber_watchtower_check_interval_seconds"]
        deadline = time.monotonic() + interval * rounds + 2
        while time.monotonic() < deadline:
            pending = list(self.node.getClient().get_raw_tx_pool().get("pending") or [])
            if pending:
                self.Miner.miner_until_tx_committed(self.node, pending[0])
            else:
                self.Miner.miner_with_version(self.node, "0x0")
            time.sleep(1)

    def consume_commitment_chain_timeout(self, commitment, code_tx, version):
        """跟随派生链消费每一格 commitment cell，返回 (spends, fees)（H32V2-10 原实现）。

        每一笔消费都必须执行部署的 commitment-lock 并保持原版本锁布局；
        witness 里不得出现原像这一条由调用方统一断言。
        """
        tx = commitment
        fees = self.get_tx_message(tx["hash"])["fee"]
        spends = []
        for _ in range(6):
            locked = [
                o
                for o in tx["outputs"]
                if o["lock"]["code_hash"] == COMMIT_LOCK_CODE_HASH
            ]
            if not locked:
                return spends, fees
            assert locked == [tx["outputs"][0]], tx
            self.assert_commitment_layout(tx)
            self.ckb.generate_epochs("0x2")
            following = self.wait_for_spend(tx["hash"])
            fees += self.get_tx_message(following["hash"])["fee"]
            assert {
                "out_point": {"tx_hash": code_tx, "index": "0x0"},
                "dep_type": "code",
            } in following["cell_deps"], following["cell_deps"]
            witness = SettlementWitness.from_hex(
                following["witnesses"][0], version=version
            )
            assert (
                witness.to_hex() == following["witnesses"][0]
            ), "settlement witness 必须往返一致"
            spends.append((following, witness))
            tx = following
        self.fail(f"仍有待结算 commitment cell: {tx['hash']}")

    def hold_payment(self, amount, committed):
        """再留一笔只在本进程知道原像的 hold TLC，等到两端都有 committed 笔已承诺 TLC。

        框架的 `hold_one_payment` 只接受“恰好一笔待处理 TLC”，本用例需要两笔，
        所以这里用金额和笔数显式等待，而不是复用它的单笔循环条件。
        """
        preimage = "0x" + secrets.token_hex(32)
        payment_hash = ckb_hash(preimage)
        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(amount),
                "currency": "Fibd",
                "payment_hash": payment_hash,
                "hash_algorithm": "ckb_hash",
                # 旧节点要求至少 160 分钟，两种版本共用这个下界。
                "final_expiry_delta": hex(9_600_000),
                "expiry": "0xe10",
            }
        )
        self.fiber1.get_client().send_payment({"invoice": invoice["invoice_address"]})
        self.wait_payment_state(self.fiber1, payment_hash, "Inflight")
        self.wait_invoice_state(self.fiber2, payment_hash, "Received")
        for _ in range(60):
            channels = [self.channel(f) for f in self.fibers]
            if all(
                len(c["pending_tlcs"]) == committed
                and all("Committed" in t["status"].values() for t in c["pending_tlcs"])
                for c in channels
            ):
                self.signed_hashes = [
                    c["latest_commitment_transaction_hash"] for c in channels
                ]
                return payment_hash, preimage
            time.sleep(1)
        self.fail(f"{committed} 笔 TLC 未在选定通道上完成承诺握手: {channels}")

    def consume_commitment_chain_final_sweep(self, commitment, code_tx, version, held):
        """逐格消费承诺/派生 cell，返回 (spends, fees, remaining)（H32V2-25 原实现）。

        `held` 是强关承诺里剩余的 [(payment_hash, amount)]。每一笔消费都必须：
        执行部署的 commitment-lock、保持原版本锁布局、witness 里没有任何原像，
        并且 witness 列出的 TLC 清单正好等于仍未逐笔解锁的那几笔（金额互不相同，
        用来跟踪逐笔解锁造成的收缩）。
        """
        tx = commitment
        fees = self.get_tx_message(tx["hash"])["fee"]
        spends = []
        remaining = list(held)
        for _ in range(6):
            locked = [
                o
                for o in tx["outputs"]
                if o["lock"]["code_hash"] == COMMIT_LOCK_CODE_HASH
            ]
            if not locked:
                return spends, fees, remaining
            assert locked == [tx["outputs"][0]], tx
            self.assert_commitment_layout(tx)
            self.ckb.generate_epochs("0x2")
            following = self.wait_for_spend(tx["hash"])
            fees += self.get_tx_message(following["hash"])["fee"]
            assert {
                "out_point": {"tx_hash": code_tx, "index": "0x0"},
                "dep_type": "code",
            } in following["cell_deps"], following["cell_deps"]
            witness = SettlementWitness.from_hex(
                following["witnesses"][0], version=version
            )
            assert (
                witness.to_hex() == following["witnesses"][0]
            ), "settlement witness 必须往返一致"
            # 无原像收尾：整条链上不允许出现任何原像。
            assert all(unlock.preimage is None for unlock in witness.unlocks), (
                following["hash"],
                witness.unlocks,
            )
            # 精确身份：本次消费列出的 TLC 必须正好是仍未逐笔解锁的那几笔。
            witness.assert_pending_tlcs(remaining)
            spends.append((following, witness))
            # 逐笔索引解锁会把对应条目从下一格派生 cell 的清单里移除；
            # 0xfe/0xff 余额 unlock 只是整体收尾，清单保持不变。
            settled_amounts = {
                witness.tlcs[unlock.unlock_type].amount
                for unlock in witness.unlocks
                if unlock.unlock_type < len(witness.tlcs)
            }
            remaining = [
                entry for entry in remaining if entry[1] not in settled_amounts
            ]
            tx = following
        self.fail(f"仍有待结算 commitment cell: {tx['hash']}")

    # TEST-MAP: H32V2-10
    def test_legacy_and_v1_timeout_refund_without_preimage(self):
        # 默认快照已部署升级后的 commitment-lock，直接用当前代码 cell。
        code_tx = self.current_contract_code_tx()
        for version, receiver in (("v1", self.new2), ("legacy", self.legacy_peer)):
            for close_at_sender in (True, False):
                with self.subTest(version=version, close_at_sender=close_at_sender):
                    # V1 = 双方新节点；Legacy = 新节点对固定旧节点 9a561b3。
                    self.select_peers(self.new1, receiver, version)
                    self.open_ready()
                    # 原像只留在测试进程里：节点没有登记，链上只能走无原像超时回收。
                    payment_hash, preimage = self.hold_one_payment()
                    tlc = self.tlc_of(self.fiber1, payment_hash)
                    assert tlc["payment_hash"] == payment_hash, tlc
                    closer = self.fiber1 if close_at_sender else self.fiber2
                    commitment = self.force_close(closer)
                    # 强关承诺仍必须是本版本布局：Legacy 57 / V1 58 + 末位 0x01。
                    self.assert_commitment_layout(commitment)

                    # (a) 只满足承诺延迟、TLC 尚未到期：合约必须拒绝回收，
                    #     承诺 outpoint 保持 live 且付款仍是 Inflight。
                    self.ckb.generate_epochs("0x1")
                    for _ in range(3):
                        self.mine_watchtower_rounds(1)
                        spent_by, _ = self.get_ln_cell_death_hash(commitment["hash"])
                        assert not spent_by, f"TLC 未到期却已消费承诺 cell: {spent_by}"
                        assert (
                            self.ckb.get_live_cell("0x0", commitment["hash"])["status"]
                            == "live"
                        ), commitment
                        inflight = self.fiber1.get_client().get_payment(
                            {"payment_hash": payment_hash}
                        )
                        assert inflight["status"] == "Inflight", inflight

                    # (b) 快进到 TLC 到期之后（SETTLE-04 的做法：改系统时间再出块）。
                    self.__class__._clock_advanced = True
                    self.add_time_and_generate_epoch(self.hours_past_expiry(tlc), 1)
                    self.mine_watchtower_rounds(4)
                    spends, fees = self.consume_commitment_chain_timeout(
                        commitment, code_tx, version
                    )
                    assert spends, "Watchtower 未提交任何无原像回收交易"

                    # 1) 整条回收链不得携带任何原像：逐笔索引解锁和最终余额
                    #    sweep 都必须是无原像回收，而不是把测试端的原像当成兑付。
                    for tx, witness in spends:
                        assert all(
                            unlock.preimage is None for unlock in witness.unlocks
                        ), (tx["hash"], witness.unlocks)
                    # 2) 第一笔消费必须精确列出被超时的这笔 hold TLC
                    #    （V1 完整哈希 / Legacy 前 20 字节 + 金额）。
                    first_witness = spends[0][1]
                    first_witness.assert_pending_tlcs([(payment_hash, CKB)])
                    # 3) TLC 清单只减不增：逐笔到期 unlock 会让派生 cell 的清单收缩；
                    #    最终余额 sweep 则保留清单并用 >= 0xfe 的 unlock 一次消费最后一格。
                    for _, witness in spends[1:]:
                        assert len(witness.tlcs) <= len(
                            first_witness.tlcs
                        ), witness.tlcs
                    last_tx, last_witness = spends[-1]
                    if last_witness.tlcs:
                        assert any(
                            unlock.unlock_type >= 0xFE
                            for unlock in last_witness.unlocks
                        ), last_witness.unlocks
                        last_witness.assert_pending_tlcs([(payment_hash, CKB)])
                    else:
                        # 逐笔超时 unlock 已把唯一一笔 TLC 从派生清单里移除。
                        last_witness.assert_pending_tlcs([])
                    assert not any(
                        o["lock"]["code_hash"] == COMMIT_LOCK_CODE_HASH
                        for o in last_tx["outputs"]
                    ), last_tx
                    # 4) 余额守恒：本金只扣实际手续费，收尾后不再有 commitment cell。
                    self.assert_settled(
                        last_tx,
                        code_tx,
                        fees - self.get_tx_message(last_tx["hash"])["fee"],
                    )
                    # 5) 付款不得因无原像回收变成 Success：必须 Failed 且没有原像。
                    self.wait_payment_state(
                        self.fiber1, payment_hash, "Failed", timeout=660
                    )
                    result = self.fiber1.get_client().get_payment(
                        {"payment_hash": payment_hash}
                    )
                    assert result["status"] == "Failed", result
                    recorded = result.get("payment_preimage")
                    assert not recorded, result
                    # 未公布的原像绝不能出现在付款记录里。
                    assert recorded != preimage, result
                    self.assert_nodes_running()

    # TEST-MAP: H32V2-25
    # TEST-EVIDENCE-BEGIN: H32V2-25
    # Evidence | covered | 契约已确认（review 行不再以“待确认：”开头）：最后一格 cell 被消费时，
    # 仍未逐笔解锁的 TLC 必须形成精确无原像消费证据、不得悬挂；由逐笔索引 unlock 还是
    # 0xfe/0xff 余额 sweep 收尾最后一格属实现选择，按实际发生的路径判定，本测试对两种路径
    # 都接受。本测试实际证明：
    # 1) 两笔金额不同、原像都没公布的已承诺 hold TLC 确实在同一份强关承诺里；
    # 2) 承诺延迟满足后再快进到两笔 TLC 都到期，Watchtower 的每一次派生消费都执行
    #    部署的 commitment-lock、保持原版本锁布局，且 witness 里没有任何原像；
    # 3) 每次消费的 witness 精确列出仍未逐笔解锁的 TLC（逐笔索引解锁会收缩清单，
    #    余额 sweep 则保留清单并一次消费最后一格 cell），最后一格消费后不再有
    #    commitment-lock 输出，双方余额只扣实际手续费；
    # 4) 收尾后两笔付款都是 Failed（绝不 Success）且没有原像，两端 TLC 进入终态，
    #    本端通道 Closed 且不再 WAITING_ONCHAIN_SETTLEMENT。
    # 未证明/不作要求：最后一格具体由哪条 unlock 路径消费属实现选择，不作为产品预期。
    # TEST-EVIDENCE-END: H32V2-25
    def test_legacy_and_v1_final_sweep_settles_remaining_tlcs_without_preimage(self):
        # 默认快照已部署升级后的 commitment-lock，直接用当前代码 cell。
        code_tx = self.current_contract_code_tx()
        for version, receiver in (("v1", self.new2), ("legacy", self.legacy_peer)):
            with self.subTest(version=version):
                # V1 = 双方新节点；Legacy = 新节点对固定旧节点 9a561b3。
                self.select_peers(self.new1, receiver, version)
                self.open_ready()
                # 两笔金额不同、原像都不公布的已承诺 hold TLC。
                first_hash, first_preimage = self.hold_payment(CKB, committed=1)
                second_hash, second_preimage = self.hold_payment(2 * CKB, committed=2)
                held = [(first_hash, CKB), (second_hash, 2 * CKB)]
                withheld = {first_hash: first_preimage, second_hash: second_preimage}
                assert first_hash != second_hash, held
                assert first_preimage != second_preimage, withheld
                tlcs = self.channel(self.fiber1)["pending_tlcs"]
                assert sorted(
                    (t["payment_hash"], int(t["amount"], 16)) for t in tlcs
                ) == sorted(held), tlcs

                commitment = self.force_close(self.fiber2)
                self.assert_commitment_layout(commitment)
                # 先满足承诺延迟，两笔 TLC 这时都还没到期。
                self.ckb.generate_epochs("0x1")
                hours = max(self.hours_past_expiry(t) for t in tlcs)
                self.__class__._clock_advanced = True
                self.add_time_and_generate_epoch(hours, 1)
                self.mine_watchtower_rounds(4)

                spends, fees, remaining = self.consume_commitment_chain_final_sweep(
                    commitment, code_tx, version, held
                )
                assert spends, "Watchtower 未提交任何无原像收尾交易"
                last_tx, last_witness = spends[-1]
                assert not any(
                    o["lock"]["code_hash"] == COMMIT_LOCK_CODE_HASH
                    for o in last_tx["outputs"]
                ), last_tx
                # 最后一格 cell 的消费必须为仍未逐笔解锁的 TLC 形成精确无原像证据：
                # 清单还没有清空时只能用 >= 0xfe 的余额 sweep 收尾，且不带原像。
                assert all(
                    unlock.preimage is None for unlock in last_witness.unlocks
                ), last_witness.unlocks
                if last_witness.tlcs:
                    assert any(
                        unlock.unlock_type >= 0xFE for unlock in last_witness.unlocks
                    ), last_witness.unlocks
                    last_witness.assert_pending_tlcs(remaining)
                else:
                    last_witness.assert_pending_tlcs([])
                # 余额守恒：本金只扣实际手续费。
                self.assert_settled(
                    last_tx, code_tx, fees - self.get_tx_message(last_tx["hash"])["fee"]
                )

                # 两笔付款都必须 Failed（绝不 Success），并且没有原像。
                for payment_hash, _amount in held:
                    self.wait_payment_state(
                        self.fiber1, payment_hash, "Failed", timeout=660
                    )
                    payment = self.fiber1.get_client().get_payment(
                        {"payment_hash": payment_hash}
                    )
                    assert payment["status"] == "Failed", payment
                    recorded = payment.get("payment_preimage")
                    assert not recorded, payment
                    # 测试进程独占的原像绝不能出现在付款记录里。
                    assert recorded != withheld[payment_hash], payment

                # 两笔 TLC 在两端都进入终态（终态记录必须真实存在，不能靠空列表通过）。
                for fiber in self.fibers:
                    for payment_hash, _amount in held:
                        self.wait_tlc_terminal(fiber, payment_hash, timeout=660)
                        matches = [
                            t
                            for c in fiber.get_client().list_channels(
                                {"include_closed": True}
                            )["channels"]
                            for t in c["pending_tlcs"]
                            if t["payment_hash"] == payment_hash
                        ]
                        assert matches, (fiber.rpc_port, payment_hash)
                        assert all(tlc_is_terminal(t) for t in matches), matches

                # 本端最终 Closed，且不再等待链上结算。
                self.assert_local_closed()
                self.assert_nodes_running()
