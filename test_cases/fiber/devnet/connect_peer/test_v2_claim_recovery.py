"""V2 累计恢复超时与链上领取；领取测试会调整系统时间，须串行执行。"""

import hashlib
import json
from pathlib import Path
import secrets
import shutil
import time

import pytest
import requests
from parameterized import parameterized

from framework.basic_fiber import COMMIT_LOCK_CODE_HASH, FiberTest
from framework.config import DEFAULT_MIN_DEPOSIT_CKB
from framework.helper.settlement_witness import (
    SettlementWitness,
    assert_commitment_args,
)
from framework.rpc import RPCClient
from framework.util import change_time, ckb_hash, restore_time
from test_cases.fiber.devnet.compatibility.contract_upgrade_support import (
    commitment_hash_without_deps,
    tlc_is_terminal,
)

CKB = 100_000_000
SAFETY_MS = 9_600_000
PRIVACY_DELTA_MS = 14_400_000


class TestV2ClaimRecovery(FiberTest):
    tmp_path_name = "tmp-v2-claim-recovery"
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 2}
    fnn_log_level = "info"

    @pytest.mark.skip("too slow")
    # TEST-MAP: PR1733-01
    def test_reconnects_preserve_online_recovery_budget(self):
        """
        1. 建立并使用 V2 CKB 通道及另一健康通道，B 保留身份丢库。
        2. 按 140、140、110 秒短连接累计观察，首轮后离线 390 秒。
        3. 核对累计期限内自动关闭、A 实际本金和健康通道余额。
        """
        # 1. 没有在途 TLC，排除领取期限或 ACK 超时替代恢复计时。
        self.channel_id = self.open_channel(
            self.fiber1,
            self.fiber2,
            fiber1_balance=500 * CKB,
            fiber2_balance=0,
            udt=None,
        )
        self._check_initial_payments()
        healthy = self.start_new_fiber(
            self.generate_account(1000), fiber_version=self.fiber_version
        )
        healthy.get_client().try_count, healthy.get_client().timeout = 1, 10
        healthy_id = self.open_channel(
            self.fiber1,
            healthy,
            fiber1_balance=300 * CKB,
            fiber2_balance=0,
            udt=None,
        )
        self._pay_between(self.fiber1, healthy, 5 * CKB)
        self._wait(
            "健康通道准备完成",
            lambda: not self._channel(self.fiber1, healthy_id)["pending_tlcs"],
        )
        healthy_balance = int(
            self._channel(self.fiber1, healthy_id)["local_balance"], 16
        )
        self.fiber2.stop()
        self._wait("B 离线", lambda: not self._peer_present(), 30)
        shutil.move(
            str(Path(self.fiber2.tmp_path) / "fiber/store"),
            Path(self.fiber2.tmp_path) / "store-backup",
        )
        self.wallet_before = self._wallet_assets()

        # 2. 正常停启与 RPC 断连各发生一次，所有在线段均短于 300 秒。
        online, commitment = 0.0, None
        for index, cumulative_limit in enumerate((140, 280, 390)):
            if index < 2:
                self.fiber2.start(fnn_log_level=self.fnn_log_level, timeout=120)
            self.fiber1.connect_peer(self.fiber2)
            self._wait("双方 peer 已连接", self._connected, 120)
            assert self.fiber2.get_pubkey() == self.pubkeys[1]
            assert not self.fiber2.get_client().list_channels({"include_closed": True})[
                "channels"
            ]
            started = time.monotonic()
            next_probe = started
            while online + time.monotonic() - started < cumulative_limit:
                assert self._connected(), "在线计时出现意外断连"
                elapsed = online + time.monotonic() - started
                commitment = self._spender(self.funding)
                if commitment:
                    assert elapsed >= 290, f"累计在线 {elapsed:.2f}s 提前关闭"
                    break
                if time.monotonic() >= next_probe:
                    self._probe_channel(healthy, healthy_id, healthy_balance)
                    next_probe = time.monotonic() + 30
                time.sleep(0.5)
            leg = time.monotonic() - started
            online += leg
            print("online leg:", index + 1, round(leg, 2), "total:", round(online, 2))
            assert leg < 300
            if commitment:
                break
            if index == 0:
                self.fiber2.stop()
                self._wait("长离线开始", lambda: not self._peer_present(), 30)
                end, next_probe = time.monotonic() + 390, 0
                while time.monotonic() < end:
                    assert not self._peer_present()
                    self._assert_unspent()
                    if time.monotonic() >= next_probe:
                        self._probe_channel(healthy, healthy_id, healthy_balance)
                        next_probe = time.monotonic() + 30
                    time.sleep(1)
            elif index == 1:
                self.fiber1.get_client().disconnect_peer({"pubkey": self.pubkeys[1]})
                self._wait("RPC 断连完成", lambda: not self._peer_present(), 30)

        # 3. 不追加长连接或人工强关；用链上输入输出独立证明本金到账。
        commitment = commitment or self._spender(self.funding)
        assert commitment is not None, f"累计在线 {online:.2f}s 后没有自动强关"
        assert online <= 395, online
        print("automatic recovery close:", commitment["hash"], "online:", online)
        self._assert_claimed_funds(commitment)
        self._probe_channel(healthy, healthy_id, healthy_balance)

    # TEST-MAP: PR1733-04
    @parameterized.expand([("ckb",), ("udt",)])
    def test_offline_claims_survive_restart(self, asset):
        """
        1. 建立 V2 CKB/UDT 通道，收取两笔哈希算法及到期时间不同的持单。
        2. B 离线后 A 各提交一次原像，正常重启 A 并保持 B 离线。
        3. 调时到最早保护窗之前，核对自动强关、两笔款与本金到账及发票 Paid。
        """
        # 1. CKB、UDT 各自执行完整流程，两笔应收款分别用 sha256、ckb_hash。
        self.udt = (
            self.get_account_udt_script(self.fiber1.account_private)
            if asset == "udt"
            else None
        )
        self.channel_id = self.open_channel(
            self.fiber1,
            self.fiber2,
            fiber1_balance=500 * CKB,
            fiber2_balance=0,
            udt=self.udt,
        )
        self._check_initial_payments()
        for index, algorithm in enumerate(("sha256", "ckb_hash")):
            claim = self._hold_payment(
                self.fiber2,
                self.fiber1,
                2 * CKB + 17 + index,
                SAFETY_MS + (600 + 300 * index) * 1000,
                algorithm,
            )
            self.claims[claim["payment_hash"]] = claim
        self._wait_committed_claims()

        # 2. 原像只交一次；重启后仍须自行恢复领取能力。
        self.fiber2.stop()
        self._wait("付款方离线", lambda: not self._peer_present(), 30)
        for payment_hash, claim in self.claims.items():
            self.fiber1.get_client().settle_invoice(
                {"payment_hash": payment_hash, "payment_preimage": claim["preimage"]}
            )
        self.fiber1.stop()
        self.fiber1.start(fnn_log_level=self.fnn_log_level, timeout=120)
        self.wallet_before = self._wallet_assets()

        # 3. 观察的是第一笔期限，第二笔也必须一并实际兑现。
        commitment = self._wait_claim_close()
        self._assert_claimed_funds(commitment)
        for payment_hash in self.claims:
            self.wait_invoice_state(self.fiber1, payment_hash, "Paid", timeout=120)

    # TEST-MAP: RSETTLE-01
    def test_submitted_settlement_completes_after_sender_restart(self):
        """
        1. 建立 V2 CKB 通道并准备付款余额，确认单笔持单在双方均已承诺。
        2. 停止付款方，仅提交一次原像，离线观察五秒且 funding 保持未消费。
        3. 原目录启动付款方，从 RPC 首次可用起十秒内自动结清，至多重连一次。
        4. 再观察三十秒无重复入账或误关，等额双向付款后余额仍正确。
        """
        # 1. fiber1 收款、fiber2 付款；直接展示建通道参数，不调系统时间。
        self._case_deadline = time.monotonic() + 480
        self.ckb = RPCClient(self.ckb.url, timeout=2, try_count=1)
        rpcs = [
            RPCClient(f.get_client().url, timeout=2, try_count=1) for f in self.fibers
        ]
        for rpc in [self.ckb, *rpcs]:
            rpc.deadline = self._case_deadline
        self.channel_id = self.open_channel(
            self.fiber1,
            self.fiber2,
            fiber1_balance=500 * CKB,
            fiber2_balance=0,
            udt=None,
            other_config={"commitment_delay_epoch": "0x10000000001"},
        )
        self._check_initial_payments()
        before = self.expected[:]
        wallets_before = [
            self.ckb.get_cells_capacity(
                {"script": lock, "script_type": "lock", "script_search_mode": "exact"}
            )["capacity"]
            for lock in self.locks
        ]
        amount = 200_000_017
        claim = self._hold_payment(
            self.fiber2,
            self.fiber1,
            amount,
            10_200_000,
            "sha256",
            session_timeout=86_400,
        )
        payment_hash = claim["payment_hash"]
        expected = [before[0] + amount, before[1] - amount]
        directions = ("Inbound", "Outbound")
        last, times = {}, {}
        stage = "双端承诺前置"

        def snapshot():
            # 每轮重新查询，保留部分完成的最后状态；调用不含自动重试。
            for i, rpc in enumerate(rpcs):
                rows = rpc.call("list_channels", [{"include_closed": True}])["channels"]
                last[f"channel_{i}"] = next(
                    (c for c in rows if c["channel_id"] == self.channel_id), None
                )
                last[f"peers_{i}"] = rpc.call("list_peers", [])
            last["invoice"] = rpcs[0].call(
                "get_invoice", [{"payment_hash": payment_hash}]
            )
            last["payment"] = rpcs[1].call(
                "get_payment", [{"payment_hash": payment_hash}]
            )
            self._assert_unspent()
            last["sample_finished"] = time.monotonic()

        def completely_settled():
            return (
                last["invoice"]["status"] == "Paid"
                and last["payment"]["status"] == "Success"
                and last["payment"]["payment_preimage"] == claim["preimage"]
                and all(
                    c is not None
                    and c["state"]["state_name"] == "ChannelReady"
                    and int(c["local_balance"], 16) == expected[i]
                    and int(c["remote_balance"], 16) == expected[1 - i]
                    and int(c["offered_tlc_balance"], 16) == 0
                    and int(c["received_tlc_balance"], 16) == 0
                    and all(
                        t["payment_hash"] == payment_hash
                        and t["status"] == {directions[i]: "RemoveAckConfirmed"}
                        for t in c["pending_tlcs"]
                    )
                    for i, c in enumerate((last["channel_0"], last["channel_1"]))
                )
            )

        try:
            committed_rounds = 0
            deadline = min(self._case_deadline, time.monotonic() + 60)
            for rpc in [self.ckb, *rpcs]:
                rpc.deadline = deadline
            while committed_rounds < 2:
                snapshot()
                committed = (
                    last["invoice"]["status"] == "Received"
                    and last["payment"]["status"] == "Inflight"
                    and all(
                        c is not None
                        and c["state"]["state_name"] == "ChannelReady"
                        and int(c["offered_tlc_balance"], 16)
                        == (amount if i == 1 else 0)
                        and int(c["received_tlc_balance"], 16)
                        == (amount if i == 0 else 0)
                        and len(c["pending_tlcs"]) == 1
                        and c["pending_tlcs"][0]["payment_hash"] == payment_hash
                        and int(c["pending_tlcs"][0]["amount"], 16) == amount
                        and int(c["pending_tlcs"][0]["expiry"], 16) == claim["expiry"]
                        and c["pending_tlcs"][0]["status"]
                        == {directions[i]: "Committed"}
                        for i, c in enumerate((last["channel_0"], last["channel_1"]))
                    )
                )
                assert time.monotonic() <= deadline, "双端承诺前置超时"
                committed_rounds = committed_rounds + 1 if committed else 0
                if committed_rounds < 2:
                    time.sleep(min(0.2, max(0, deadline - time.monotonic())))
            protection_at = claim["expiry"] / 1000 - SAFETY_MS / 1000 - 60
            assert time.time() + 600 < protection_at, "故障前保护窗口余量不足"
            for rpc in [self.ckb, *rpcs]:
                rpc.deadline = self._case_deadline

            # 2. 自停止 P 开始，任何 funding 消费均是行为失败而非前置失败。
            stage = "付款方离线时单次提交原像"
            self.fiber2.stop()
            self._wait("付款方离线", lambda: not self._peer_present(), 30)
            self._assert_unspent()
            try:
                last["settle_response"] = rpcs[0].call(
                    "settle_invoice",
                    [
                        {
                            "payment_hash": payment_hash,
                            "payment_preimage": claim["preimage"],
                        }
                    ],
                )
            except requests.RequestException as exc:
                raise AssertionError(
                    "单次提交结果不确定；本次样本结束，不重新提交"
                ) from exc
            print("offline settle response:", last["settle_response"], flush=True)
            offline_until = time.monotonic() + 5
            while True:
                peers = rpcs[0].call("list_peers", [])["peers"]
                assert all(p["pubkey"] != self.pubkeys[1] for p in peers)
                last["offline_invoice"] = rpcs[0].call(
                    "get_invoice", [{"payment_hash": payment_hash}]
                )
                last["offline_channels"] = rpcs[0].call(
                    "list_channels", [{"include_closed": True}]
                )
                self._assert_unspent()
                if time.monotonic() >= offline_until:
                    break
                time.sleep(min(0.2, max(0, offline_until - time.monotonic())))

            # 3. TCP 端口开放不算就绪；第一次成功 node_info 返回后立即计时。
            stage = "付款方启动就绪"
            times["t_start"] = time.monotonic()
            startup_deadline = min(self._case_deadline, times["t_start"] + 120)
            self.fiber2.start(
                fnn_log_level=self.fnn_log_level,
                timeout=startup_deadline - time.monotonic(),
            )
            rpcs[1].deadline = startup_deadline
            while True:
                try:
                    info = rpcs[1].call("node_info", [{}])
                    times["t_rpc"] = time.monotonic()
                    assert info["pubkey"] == self.pubkeys[1], info
                    break
                except (requests.ConnectionError, requests.Timeout):
                    assert time.monotonic() < startup_deadline, "启动就绪超时"
                    time.sleep(min(0.2, max(0, startup_deadline - time.monotonic())))
            deadline = min(self._case_deadline, times["t_rpc"] + 10)
            for rpc in [self.ckb, *rpcs]:
                rpc.deadline = deadline
            stage = "重连与账本结清"
            connect_sent = False
            while True:
                snapshot()
                connected = all(
                    any(
                        p["pubkey"] == self.pubkeys[1 - i]
                        for p in last[f"peers_{i}"]["peers"]
                    )
                    for i in range(2)
                )
                if connected:
                    times.setdefault("t_peer", last["sample_finished"])
                elif not connect_sent:
                    address = rpcs[0].call("node_info", [{}])["addresses"][0]
                    connect_sent = True
                    last["connect_sent"] = True
                    rpcs[1].call(
                        "connect_peer",
                        [{"address": address.replace("0.0.0.0", "127.0.0.1")}],
                    )
                assert last["payment"]["status"] != "Failed", last["payment"]
                assert time.monotonic() <= deadline, "RPC 可用后十秒内未完整结清"
                if connected and completely_settled():
                    times["t_settled"] = last["sample_finished"]
                    break
                time.sleep(min(0.2, max(0, deadline - time.monotonic())))
            print(
                "restart settlement timing:",
                json.dumps(
                    {
                        "startup_seconds": times["t_rpc"] - times["t_start"],
                        "reconnect_seconds": times["t_peer"] - times["t_rpc"],
                        "settlement_after_rpc_seconds": times["t_settled"]
                        - times["t_rpc"],
                        "restart_to_settled_seconds": times["t_settled"]
                        - times["t_start"],
                        "connect_sent": connect_sent,
                    }
                ),
                flush=True,
            )

            # 4. 十秒验收不借用后置观察时间；探针净额为零，双方钱包也不变。
            stage = "结清后稳定性"
            for rpc in [self.ckb, *rpcs]:
                rpc.deadline = self._case_deadline
            stable_until = times["t_settled"] + 30
            while True:
                snapshot()
                assert completely_settled(), "结清后状态或余额再次变化"
                assert time.time() < claim["expiry"] / 1000, "已到 TLC 期限"
                if time.monotonic() >= stable_until:
                    break
                time.sleep(min(0.2, max(0, stable_until - time.monotonic())))
            stage = "结清后双向付款"
            self._pay_between(self.fiber1, self.fiber2, CKB)
            self._pay_between(self.fiber2, self.fiber1, CKB)
            deadline = min(self._case_deadline, time.monotonic() + 60)
            for rpc in [self.ckb, *rpcs]:
                rpc.deadline = deadline
            while True:
                snapshot()
                assert time.monotonic() <= deadline, "探针付款后账本未对齐"
                if completely_settled():
                    break
                time.sleep(min(0.2, max(0, deadline - time.monotonic())))
            wallets_after = [
                self.ckb.get_cells_capacity(
                    {
                        "script": lock,
                        "script_type": "lock",
                        "script_search_mode": "exact",
                    }
                )["capacity"]
                for lock in self.locks
            ]
            assert wallets_after == wallets_before, (wallets_before, wallets_after)
            assert time.monotonic() <= self._case_deadline, "用例八分钟预算已用尽"
        except Exception as exc:
            raise AssertionError(
                f"{stage}: {exc}\n最后状态: {json.dumps(last, ensure_ascii=False)}\n"
                f"时间戳: {json.dumps(times)}"
            ) from exc

    # TEST-MAP: RCLAIM-03
    def test_manual_force_close_claims_survive_restart(self):
        """
        1. 建立 V2 CKB 通道，记录本金和钱包，接收两笔先后到期的不同哈希持单。
        2. 付款方离线后各提交一次原像，正常重启收款方，不再次提交原像。
        3. 在最早保护阈值前至少五分钟主动强关一次，不调系统时间。
        4. 核对两笔款、本金及预留容量真实到账、费用正确和两张发票 Paid。
        """
        # 1. fiber1 是收款方；固定一 epoch，两个算法共用已有真实链核款方法。
        self._case_deadline = time.monotonic() + 25 * 60
        self.ckb = RPCClient(self.ckb.url, timeout=10, try_count=1)
        self.ckb.deadline = self._case_deadline
        for fiber in self.fibers:
            fiber.get_client().deadline = self._case_deadline
        self.channel_id = self.open_channel(
            self.fiber1,
            self.fiber2,
            fiber1_balance=500 * CKB,
            fiber2_balance=0,
            udt=None,
            other_config={"commitment_delay_epoch": "0x10000000001"},
        )
        self._check_initial_payments()
        self.wallet_before = self._wallet_assets()
        for index, algorithm in enumerate(("sha256", "ckb_hash")):
            claim = self._hold_payment(
                self.fiber2,
                self.fiber1,
                2 * CKB + 17 + index,
                SAFETY_MS + (600 + 300 * index) * 1000,
                algorithm,
            )
            self.claims[claim["payment_hash"]] = claim
        self._wait_committed_claims()
        expiries = [claim["expiry"] for claim in self.claims.values()]
        assert len(expiries) == 2 and expiries[0] < expiries[1], expiries

        # 2. 停付款方后才结算；重启只使用原目录，不修改原像或通道数据库。
        self.fiber2.stop()
        self._wait("付款方离线", lambda: not self._peer_present(), 30)
        for payment_hash, claim in self.claims.items():
            self.fiber1.get_client().settle_invoice(
                {"payment_hash": payment_hash, "payment_preimage": claim["preimage"]}
            )
        self.fiber1.stop()
        self.fiber1.start(fnn_log_level=self.fnn_log_level, timeout=120)

        # 3. 先排除自动关闭，再走单次公开 RPC；广播和领取共用有界预算。
        assert not self._peer_present(), "付款方意外恢复连接"
        assert self._channel(self.fiber1)["state"]["state_name"] == "ChannelReady"
        self._assert_unspent()
        protection_at = min(expiries) / 1000 - SAFETY_MS / 1000 - 60
        assert time.time() + 300 < protection_at, "已不足五分钟的主动关闭观察余量"
        close_started = time.monotonic()
        self._settlement_deadline = min(self._case_deadline, close_started + 660)
        rpc = self.fiber1.get_client()
        rpc.deadline = self._settlement_deadline
        rpc.shutdown_channel({"channel_id": self.channel_id, "force": True})
        assert time.monotonic() - close_started <= 10, "主动关闭 RPC 超过十秒"
        self.ckb.deadline = min(self._settlement_deadline, close_started + 60)
        commitment = self._wait(
            "主动关闭广播",
            lambda: self._spender(self.funding),
            max(0, self.ckb.deadline - time.monotonic()),
        )
        print("manual force close:", commitment["hash"], flush=True)

        # 4. 沿真实消费链逐笔核款，不以 Closed 或 RPC 成功代替资产到账。
        self.ckb.deadline = self._settlement_deadline
        funds = self._assert_claimed_funds(
            commitment, deadline=self._settlement_deadline
        )
        assert set(funds["claimed_hashes"]) == set(self.claims)
        expected_credit = (
            self.expected[0]
            + DEFAULT_MIN_DEPOSIT_CKB
            + sum(claim["amount"] for claim in self.claims.values())
            - funds["settlement_fees"]
        )
        assert funds["wallet_delta"] == (expected_credit, 0), funds
        for payment_hash in self.claims:
            self.wait_invoice_state(self.fiber1, payment_hash, "Paid", timeout=120)
        assert not self._peer_present(), "领取期间付款方意外恢复连接"
        assert time.monotonic() <= self._settlement_deadline, "链上回款预算已用尽"
        print(
            "manual close claims: 2 paid; wallet credit:", expected_credit, flush=True
        )

    # TEST-MAP: PR1733-07
    def test_relay_claims_offline_upstream_after_restart(self):
        """
        1. 建立付款方→中继→收款方的 V2 路由，公开设置逐跳期限为 160 分钟。
        2. 收款持单后停止上游，结算下游，再正常重启中继。
        3. 按真实上游 expiry 调时后等待自动强关，核对中继垫款和路由费到账。
        """
        # 1. fiber2 是付款方，fiber1 是受保护中继，receiver 是末跳。
        self.channel_id = self.open_channel(
            self.fiber1,
            self.fiber2,
            fiber1_balance=500 * CKB,
            fiber2_balance=0,
            udt=None,
        )
        self._check_initial_payments()
        receiver = self.start_new_fiber(
            self.generate_account(1000), fiber_version=self.fiber_version
        )
        receiver.get_client().try_count, receiver.get_client().timeout = 1, 10
        downstream_id = self.open_channel(
            self.fiber1,
            receiver,
            fiber1_balance=300 * CKB,
            fiber2_balance=0,
            udt=None,
        )
        self._pay_between(self.fiber1, receiver, 5 * CKB)
        self._pay_between(receiver, self.fiber1, CKB)
        self._wait(
            "下游 TLC 清空",
            lambda: not self._channel(self.fiber1, downstream_id)["pending_tlcs"],
        )
        downstream_before = int(
            self._channel(self.fiber1, downstream_id)["local_balance"], 16
        )
        for fiber, channel_id in (
            (self.fiber1, self.channel_id),
            (self.fiber2, self.channel_id),
            (self.fiber1, downstream_id),
            (receiver, downstream_id),
        ):
            fiber.get_client().update_channel(
                {
                    "channel_id": channel_id,
                    "tlc_expiry_delta": hex(SAFETY_MS),
                    "tlc_fee_proportional_millionths": hex(1000),
                }
            )
            assert (
                int(self._channel(fiber, channel_id)["tlc_expiry_delta"], 16)
                == SAFETY_MS
            )

        def routing_updated():
            channels = self.fiber2.get_client().graph_channels({})["channels"]
            wanted = {
                self._channel(self.fiber1, cid)["channel_outpoint"]
                for cid in (self.channel_id, downstream_id)
            }
            rows = [c for c in channels if c["channel_outpoint"] in wanted]
            return len(rows) == 2 and all(
                c[key] and int(c[key]["tlc_expiry_delta"], 16) == SAFETY_MS
                for c in rows
                for key in ("update_info_of_node1", "update_info_of_node2")
            )

        self._wait("付款方路由已收到 160 分钟配置", routing_updated, 120)
        amount = 2 * CKB + 17
        claim = self._hold_payment(
            self.fiber2, receiver, amount, SAFETY_MS + 600_000, "sha256"
        )
        payment_hash = claim["payment_hash"]
        upstream_tlc = self._wait(
            "上游 TLC",
            lambda: self._pending(self.fiber1, self.channel_id, payment_hash),
        )
        downstream_tlc = self._pending(receiver, downstream_id, payment_hash)
        fee = (amount * 1000 + 999_999) // 1_000_000
        assert int(upstream_tlc["amount"], 16) == amount + fee
        expiry_difference = int(upstream_tlc["expiry"], 16) - int(
            downstream_tlc["expiry"], 16
        )
        assert expiry_difference == SAFETY_MS, expiry_difference
        claim["amount"], claim["expiry"] = amount + fee, int(upstream_tlc["expiry"], 16)
        self.claims[payment_hash] = claim
        self._wait_committed_claims()

        # 2. 下游确已付款后才重启；中继上游一直离线且不再次提交原像。
        self.fiber2.stop()
        self._wait("上游离线", lambda: not self._peer_present(), 30)
        receiver.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": claim["preimage"]}
        )
        self.wait_invoice_state(receiver, payment_hash, "Paid", timeout=120)
        self._wait(
            "中继已付下游",
            lambda: int(self._channel(self.fiber1, downstream_id)["local_balance"], 16)
            == downstream_before - amount
            and not self._channel(self.fiber1, downstream_id)["pending_tlcs"],
        )
        assert int(self._channel(self.fiber1)["local_balance"], 16) == self.expected[0]
        self.fiber1.stop()
        self.fiber1.start(fnn_log_level=self.fnn_log_level, timeout=120)
        receiver.connect_peer(self.fiber1)
        self._probe_channel(receiver, downstream_id, downstream_before - amount)
        self.wallet_before = self._wallet_assets()

        # 3. 调系统时间接近真实 expiry；保护窗口内下游仍须持续可用。
        commitment = self._wait_claim_close(
            probe=lambda: self._probe_channel(
                receiver, downstream_id, downstream_before - amount
            )
        )
        self._assert_claimed_funds(commitment)
        self._probe_channel(receiver, downstream_id, downstream_before - amount)
        assert claim["amount"] - amount == fee
        print(
            "relay recovered:",
            claim["amount"],
            "downstream paid:",
            amount,
            "routing fee:",
            fee,
        )

    # TEST-MAP: RCLAIM-01
    # TEST-MAP: RCLAIM-02
    def test_relay_claims_after_expiry(self):
        """
        1. 建立 A→B→C 路由，记录目标付款前的本金、下游余额和钱包。
        2. C 持单后停止 A，提交一次原像，确认 B 已付 C、上游仍未结。
        3. B 不重启，调到上游 expiry 前，跨过到期点观察至 expiry+180 秒。
        4. 自动未关则人工强关一次，独立核对链上到账及中继净额，再汇总结果。
        """
        # 1. fiber2=A，fiber1=B，receiver=C；建通道参数直接放在正文。
        self._case_deadline = time.monotonic() + 1800
        self.ckb = RPCClient(self.ckb.url, timeout=10, try_count=1)
        self.ckb.deadline = self._case_deadline
        self.channel_id = self.open_channel(
            self.fiber1,
            self.fiber2,
            fiber1_balance=500 * CKB,
            fiber2_balance=0,
            udt=None,
        )
        self._check_initial_payments()
        receiver = self.start_new_fiber(
            self.generate_account(1000), fiber_version=self.fiber_version
        )
        receiver.get_client().try_count, receiver.get_client().timeout = 1, 10
        downstream_id = self.open_channel(
            self.fiber1,
            receiver,
            fiber1_balance=300 * CKB,
            fiber2_balance=0,
            udt=None,
        )
        self._pay_between(self.fiber1, receiver, 5 * CKB)
        self._pay_between(receiver, self.fiber1, CKB)
        self._wait(
            "下游准备完成",
            lambda: not self._channel(self.fiber1, downstream_id)["pending_tlcs"],
        )
        for fiber, channel_id in (
            (self.fiber1, self.channel_id),
            (self.fiber2, self.channel_id),
            (self.fiber1, downstream_id),
            (receiver, downstream_id),
        ):
            fiber.get_client().update_channel(
                {
                    "channel_id": channel_id,
                    "tlc_expiry_delta": hex(SAFETY_MS),
                    "tlc_fee_proportional_millionths": hex(1000),
                }
            )
        outpoints = {
            self._channel(self.fiber1, cid)["channel_outpoint"]
            for cid in (self.channel_id, downstream_id)
        }

        def routing_updated():
            rows = [
                c
                for c in self.fiber2.get_client().graph_channels({})["channels"]
                if c["channel_outpoint"] in outpoints
            ]
            return len(rows) == 2 and all(
                c[key]
                and int(c[key]["tlc_expiry_delta"], 16) == SAFETY_MS
                and int(c[key]["fee_rate"], 16) == 1000
                for c in rows
                for key in ("update_info_of_node1", "update_info_of_node2")
            )

        self._wait("逐跳期限和费率已同步", routing_updated)
        downstream_before = int(
            self._channel(self.fiber1, downstream_id)["local_balance"], 16
        )
        self.wallet_before = self._wallet_assets()
        principal_before = self.expected[0] + DEFAULT_MIN_DEPOSIT_CKB

        # 2. 付款前已固定基线；m 和 fee 来自输入，不由节点应收总额反推。
        amount = 200_000_017
        fee = (amount * 1000 + 999_999) // 1_000_000
        sent_at = time.time()
        claim = self._hold_payment(
            self.fiber2,
            receiver,
            amount,
            SAFETY_MS + 600_000,
            "sha256",
            session_timeout=86_400,
        )
        payment_hash = claim["payment_hash"]
        upstream = self._wait(
            "上游 TLC",
            lambda: self._pending(self.fiber1, self.channel_id, payment_hash),
        )
        downstream = self._pending(receiver, downstream_id, payment_hash)
        assert int(upstream["amount"], 16) == amount + fee
        assert int(upstream["expiry"], 16) - int(downstream["expiry"], 16) == SAFETY_MS
        claim["amount"], claim["expiry"] = amount + fee, int(upstream["expiry"], 16)
        assert claim["expiry"] / 1000 < sent_at + 86_400
        self.claims[payment_hash] = claim
        self._wait_committed_claims()
        self.fiber2.stop()
        self._wait("A 离线", lambda: not self._peer_present(), 30)
        receiver.get_client().settle_invoice(
            {
                "payment_hash": payment_hash,
                "payment_preimage": claim["preimage"],
            }
        )
        self.wait_invoice_state(receiver, payment_hash, "Paid", timeout=120)
        self._wait(
            "B 已付下游且无在途 TLC",
            lambda: int(self._channel(self.fiber1, downstream_id)["local_balance"], 16)
            == downstream_before - amount
            and not self._channel(self.fiber1, downstream_id)["pending_tlcs"],
        )
        assert int(self._channel(self.fiber1)["local_balance"], 16) == self.expected[0]
        assert self._pending(self.fiber1, self.channel_id, payment_hash)

        # 3. 不重启 B；观察阶段不调用 shutdown，自动结果在人工干预前固定。
        commitment, automatic = self._wait_expiry_close(claim["expiry"])
        result = {"restart_relay": False, "RCLAIM-01": automatic, "RCLAIM-02": None}
        print("RCLAIM-01:", json.dumps(automatic, ensure_ascii=False), flush=True)

        # 4. 自动失败也继续核款；660 秒包括人工 RPC、广播、确认和全部领取。
        stage_started = automatic.get("observed_monotonic", time.monotonic())
        self._settlement_deadline = min(self._case_deadline, stage_started + 660)
        self.ckb.deadline = self._settlement_deadline
        intervention = {"rpc_sent": False, "source": "automatic_before_cutoff"}
        result["intervention"] = intervention
        try:
            if commitment is None:
                commitment = self._spender(self.funding)
                intervention["channel_before_rpc"] = self._channel(self.fiber1)["state"]
                if commitment:
                    intervention["source"] = "automatic_after_cutoff"
                else:
                    # 单次公开 RPC；和最后一次查询间的关闭竞态不倒填自动成功。
                    rpc = RPCClient(
                        self.fiber1.get_client().url, timeout=10, try_count=1
                    )
                    rpc.deadline = self._settlement_deadline
                    intervention.update(
                        rpc_sent=True,
                        started_at=time.time(),
                        source="after_intervention_source_unconfirmed",
                    )
                    rpc_error = None
                    try:
                        rpc.call(
                            "shutdown_channel",
                            [{"channel_id": self.channel_id, "force": True}],
                        )
                    except Exception as exc:
                        rpc_error = exc
                        intervention["rpc_error"] = str(exc)
                    finally:
                        intervention["finished_at"] = time.time()
                    if rpc_error and not any(
                        word in str(rpc_error).lower()
                        for word in (
                            "closed",
                            "shuttingdown",
                            "shutting down",
                            "channelnotfound",
                            "channel not found",
                        )
                    ):
                        raise rpc_error
                    commitment = self._wait(
                        "人工干预后的真实 funding 消费",
                        lambda: self._spender(self.funding),
                        60,
                    )
            funds = self._assert_claimed_funds(
                commitment, deadline=self._settlement_deadline
            )
            self._probe_channel(receiver, downstream_id, downstream_before - amount)
            assert (
                receiver.get_client().get_invoice({"payment_hash": payment_hash})[
                    "status"
                ]
                == "Paid"
            )
            assert not self._peer_present(), "核款期间 A 意外恢复"
            wallet_delta = self._wallet_assets()[0] - self.wallet_before[0]
            downstream_after = int(
                self._channel(self.fiber1, downstream_id)["local_balance"], 16
            )
            net = wallet_delta - principal_before + downstream_after - downstream_before
            assert net == fee - funds["settlement_fees"], (net, fee, funds)
            result["RCLAIM-02"] = {"recovered": True, "relay_net": net, **funds}
        except Exception as exc:
            result["RCLAIM-02"] = {"recovered": False, "error": str(exc)}
            raise AssertionError(json.dumps(result, ensure_ascii=False)) from exc
        finally:
            print("RCLAIM results:", json.dumps(result, ensure_ascii=False), flush=True)
        assert automatic["closed_before_cutoff"], json.dumps(result, ensure_ascii=False)

    @classmethod
    def setup_class(cls):
        # CKB 在类内共享；调时后已有未来区块，用例之间保持时间单调前进。
        cls._elapsed_clock = getattr(time, "CLOCK_BOOTTIME", time.CLOCK_MONOTONIC_RAW)
        cls._clock_anchor = time.time(), time.clock_gettime(cls._elapsed_clock)
        cls._clock_changed = False
        super().setup_class()

    def setup_method(self, method):
        super().setup_method(method)
        self.ckb = self.node.getClient()
        self.pubkeys = [f.get_pubkey() for f in self.fibers]
        self.locks = [self.get_account_script(f.account_private) for f in self.fibers]
        for fiber in self.fibers:
            fiber.get_client().try_count, fiber.get_client().timeout = 1, 10
        self.udt, self.claims = None, {}

    @classmethod
    def teardown_class(cls):
        try:
            # 先停止矿工和 CKB、清理链数据，再回拨系统时间。
            super().teardown_class()
        finally:
            if cls._clock_changed:
                before_wall, before_elapsed = cls._clock_anchor
                expected = (
                    before_wall
                    + time.clock_gettime(cls._elapsed_clock)
                    - before_elapsed
                )
                assert restore_time(
                    reference_timestamp=expected
                ), "恢复系统时间命令失败"
                drift = time.time() - (
                    before_wall
                    + time.clock_gettime(cls._elapsed_clock)
                    - before_elapsed
                )
                assert abs(drift) < 5, f"系统时间恢复后偏差 {drift:.3f}s"

    def _check_initial_payments(self):
        """已建通道双向付款并核账，记录故障前的本金、funding 和最后承诺。"""
        self._wait(
            "双方通道 Ready",
            lambda: all(
                self._channel(f)["state"]["state_name"] == "ChannelReady"
                for f in (self.fiber1, self.fiber2)
            ),
        )
        self.expected = [
            int(self._channel(f)["local_balance"], 16)
            for f in (self.fiber1, self.fiber2)
        ]
        for sender_index, amount in ((0, 100 * CKB), (1, CKB)):
            sender, receiver = (self.fiber1, self.fiber2)[sender_index], (
                self.fiber1,
                self.fiber2,
            )[1 - sender_index]
            self._pay_between(sender, receiver, amount, self.udt)
            self.expected[sender_index] -= amount
            self.expected[1 - sender_index] += amount
            last_channels = []

            def balances_match():
                channels = [self._channel(f) for f in (self.fiber1, self.fiber2)]
                last_channels[:] = channels
                return all(
                    c["state"]["state_name"] == "ChannelReady"
                    and int(c["local_balance"], 16) == self.expected[i]
                    and int(c["remote_balance"], 16) == self.expected[1 - i]
                    and int(c["offered_tlc_balance"], 16) == 0
                    and int(c["received_tlc_balance"], 16) == 0
                    and not c["pending_tlcs"]
                    for i, c in enumerate(channels)
                )

            try:
                self._wait("付款账本及 TLC 清空", balances_match)
            except AssertionError as exc:
                # 使用最后一次轮询的原始状态，不在超时后重新请求或追加等待。
                snapshot = {
                    "sender_index": sender_index,
                    "amount": amount,
                    "expected_local_balances": self.expected,
                    "channels": last_channels,
                }
                raise AssertionError(
                    f"{exc}\n付款后账本快照: {json.dumps(snapshot, ensure_ascii=False)}"
                ) from exc
        channel = self._channel(self.fiber1)
        point = bytes.fromhex(channel["channel_outpoint"][2:])
        assert int.from_bytes(point[32:], "little") == 0
        self.funding = self.ckb.get_transaction("0x" + point[:32].hex())["transaction"]
        self.latest_commitment = channel["latest_commitment_transaction_hash"]
        if not self.udt:
            assert (
                int(self.funding["outputs"][0]["capacity"], 16) - sum(self.expected)
                == 2 * DEFAULT_MIN_DEPOSIT_CKB
            )

    def _wait(self, description, observe, timeout=120):
        started = time.monotonic()
        deadlines = {
            "当前等待预算": started + timeout,
            "用例总预算": getattr(self, "_case_deadline", float("inf")),
            "链上回款预算": getattr(self, "_settlement_deadline", float("inf")),
        }
        budget, deadline = min(deadlines.items(), key=lambda item: item[1])
        value = None
        while True:
            now = time.monotonic()
            if now >= deadline:
                raise AssertionError(
                    f"{description}，{budget}已用尽；已等待 {now - started:.3f}s，"
                    f"本次等待上限 {timeout:.1f}s，最后观察值 {value!r}"
                )
            value = observe()
            now = time.monotonic()
            if now > deadline:
                raise AssertionError(
                    f"{description}，查询返回时已超过等待预算（{budget}）；"
                    f"已等待 {now - started:.3f}s，最后观察值 {value!r}"
                )
            if value:
                return value
            time.sleep(min(0.5, max(0, deadline - now)))

    def _channel(self, fiber, channel_id=None):
        channel_id = channel_id or self.channel_id
        rows = fiber.get_client().list_channels({"include_closed": True})["channels"]
        return next(c for c in rows if c["channel_id"] == channel_id)

    def _peer_present(self):
        return any(
            p["pubkey"] == self.pubkeys[1]
            for p in self.fiber1.get_client().list_peers()["peers"]
        )

    def _connected(self):
        return self._peer_present() and any(
            p["pubkey"] == self.pubkeys[0]
            for p in self.fiber2.get_client().list_peers()["peers"]
        )

    def _pay_between(self, sender, receiver, amount, udt=None):
        invoice = receiver.get_client().new_invoice(
            {
                "amount": hex(amount),
                "currency": "Fibd",
                "payment_preimage": "0x" + secrets.token_hex(32),
                "hash_algorithm": "sha256",
                "final_expiry_delta": hex(86_400_000),
                "udt_type_script": udt,
            }
        )
        payment = sender.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "max_parts": "0x1",
                "tlc_expiry_limit": hex(172_800_000),
            }
        )

        def payment_succeeded():
            result = sender.get_client().get_payment(
                {"payment_hash": payment["payment_hash"]}
            )
            assert result["status"] != "Failed", result
            return result["status"] == "Success"

        self._wait("探针付款成功", payment_succeeded)
        self._wait(
            "探针发票 Paid",
            lambda: receiver.get_client().get_invoice(
                {"payment_hash": payment["payment_hash"]}
            )["status"]
            == "Paid",
            60,
        )

    def _spender(self, previous):
        point = {"tx_hash": previous["hash"], "index": "0x0"}
        pool = self.ckb.get_raw_tx_pool()
        candidates = set(pool["pending"] + pool["proposed"])
        rows = self.ckb.get_transactions(
            {
                "script": previous["outputs"][0]["lock"],
                "script_type": "lock",
                "script_search_mode": "exact",
            },
            "asc",
            "0xff",
            None,
        )
        candidates.update(
            r["tx_hash"] for r in rows["objects"] if r["io_type"] == "input"
        )
        for tx_hash in candidates:
            result = self.ckb.get_transaction(tx_hash)
            if result and result["tx_status"]["status"] in (
                "pending",
                "proposed",
                "committed",
            ):
                tx = result["transaction"]
                if any(i["previous_output"] == point for i in tx["inputs"]):
                    return tx
        return None

    def _assert_unspent(self):
        assert self._spender(self.funding) is None, "原 funding 已有消费交易"
        assert (
            self.ckb.get_live_cell("0x0", self.funding["hash"], False)["status"]
            == "live"
        )

    def _wallet_assets(self):
        capacity = int(
            self.ckb.get_cells_capacity(
                {
                    "script": self.locks[0],
                    "script_type": "lock",
                    "script_search_mode": "exact",
                }
            )["capacity"],
            16,
        )
        tokens = (
            self.udtContract.balance(
                self.ckb, self.locks[0]["args"], self.locks[0]["args"]
            )
            if self.udt
            else 0
        )
        return capacity, tokens

    def _hold_payment(
        self, payer, payee, amount, expiry_delta, algorithm, session_timeout=28_800
    ):
        preimage = "0x" + secrets.token_hex(32)
        payment_hash = (
            "0x" + hashlib.sha256(bytes.fromhex(preimage[2:])).hexdigest()
            if algorithm == "sha256"
            else ckb_hash(preimage)
        )
        invoice = payee.get_client().new_invoice(
            {
                "amount": hex(amount),
                "currency": "Fibd",
                "payment_hash": payment_hash,
                "hash_algorithm": algorithm,
                "expiry": hex(session_timeout),
                "final_expiry_delta": hex(expiry_delta),
                "udt_type_script": self.udt,
            }
        )
        # 正式版会给所有跳共同追加至少 4 小时；收紧预算固定为一档。
        upstream_delta = 0 if payee is self.fiber1 else SAFETY_MS
        expiry_limit = expiry_delta + upstream_delta + PRIVACY_DELTA_MS + 1000
        sent_at_ms = time.time() * 1000
        payment = payer.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "max_parts": "0x1",
                "timeout": hex(session_timeout),
                "final_tlc_expiry_delta": hex(expiry_delta),
                "tlc_expiry_limit": hex(expiry_limit),
            }
        )
        assert payment["payment_hash"] == payment_hash
        self.wait_invoice_state(payee, payment_hash, "Received", timeout=120)
        self.wait_payment_state(payer, payment_hash, "Inflight", timeout=60)
        rows = payee.get_client().list_channels({})["channels"]
        tlc = next(
            t
            for c in rows
            for t in c["pending_tlcs"]
            if t["payment_hash"] == payment_hash
        )
        actual_expiry = int(tlc["expiry"], 16)
        expected_expiry = sent_at_ms + expiry_delta + PRIVACY_DELTA_MS
        assert expected_expiry - 1000 <= actual_expiry <= expected_expiry + 30_000, (
            actual_expiry,
            expected_expiry,
        )
        print(
            "actual final expiry:",
            actual_expiry,
            "privacy delta ms:",
            PRIVACY_DELTA_MS,
            flush=True,
        )
        return {
            "payment_hash": payment_hash,
            "preimage": preimage,
            "amount": amount,
            "expiry": actual_expiry,
        }

    def _pending(self, fiber, channel_id, payment_hash):
        return next(
            (
                t
                for t in self._channel(fiber, channel_id)["pending_tlcs"]
                if t["payment_hash"] == payment_hash
            ),
            None,
        )

    def _wait_committed_claims(self):
        end = time.monotonic() + 60
        while time.monotonic() < end:
            channel = self._channel(self.fiber1)
            assert int(channel["offered_tlc_balance"], 16) == 0
            assert int(channel["received_tlc_balance"], 16) == sum(
                c["amount"] for c in self.claims.values()
            )
            for payment_hash, claim in self.claims.items():
                tlc = self._pending(self.fiber1, self.channel_id, payment_hash)
                assert tlc and int(tlc["amount"], 16) == claim["amount"]
                claim["expiry"] = int(tlc["expiry"], 16)
                assert claim["expiry"] - time.time() * 1000 > SAFETY_MS + 120_000
            time.sleep(1)
        self.latest_commitment = channel["latest_commitment_transaction_hash"]

    def _probe_channel(self, peer, channel_id, expected):
        self._pay_between(self.fiber1, peer, CKB)
        self._pay_between(peer, self.fiber1, CKB)
        self._wait(
            "健康通道账本",
            lambda: int(self._channel(self.fiber1, channel_id)["local_balance"], 16)
            == expected
            and not self._channel(self.fiber1, channel_id)["pending_tlcs"],
        )

    def _wait_claim_close(self, probe=None):
        earliest = (
            min(c["expiry"] for c in self.claims.values()) / 1000 - SAFETY_MS / 1000
        )
        lower, upper = earliest - 60, earliest + 30
        # 只跳过窗口前的长空等，保留至少两分钟的提前关闭反例观察。
        advance_minutes = max(0, int((lower - time.time() - 120) // 60))
        if advance_minutes:
            self._assert_unspent()
            before = time.time()
            self.__class__._clock_changed = True
            assert change_time(0, advance_minutes), "修改系统时间命令失败"
            actual_advance = time.time() - before
            assert abs(actual_advance - advance_minutes * 60) < 65, actual_advance
            assert time.time() < lower - 100
            self._assert_unspent()
        deadline = time.monotonic() + 420
        observed_wall, observed_mono = time.time(), time.monotonic()
        next_print, next_probe = 0, 0
        print(
            "claim close UTC epoch window:",
            lower,
            upper,
            "remaining:",
            upper - time.time(),
        )
        while time.time() <= upper and time.monotonic() < deadline:
            clock_drift = (time.time() - observed_wall) - (
                time.monotonic() - observed_mono
            )
            assert (
                abs(clock_drift) < 5
            ), f"观察期间系统时间跳变或休眠 {clock_drift:.2f}s"
            assert not self._peer_present(), "故障上游意外恢复连接"
            commitment = self._spender(self.funding)
            if commitment:
                assert time.time() >= lower - 2, "claim 在保护窗口前强关"
                print("automatic claim close:", commitment["hash"], "at:", time.time())
                return commitment
            now = time.monotonic()
            if probe and now >= next_probe:
                probe()
                next_probe = time.monotonic() + 60
            if now >= next_print:
                print(
                    "waiting claim window, seconds:",
                    round(upper - time.time()),
                    flush=True,
                )
                next_print = now + 60
            time.sleep(1)
        clock_drift = (time.time() - observed_wall) - (time.monotonic() - observed_mono)
        assert abs(clock_drift) < 5, f"观察期间系统时间跳变或休眠 {clock_drift:.2f}s"
        raise AssertionError(
            f"越过 claim 保护窗口仍未广播：{self._channel(self.fiber1)}"
        )

    def _wait_expiry_close(self, expiry_ms):
        """固定到期点 E、截止 E+180s；提前关闭和跳时中的关闭单独分类。"""
        target = expiry_ms / 1000
        cutoff = target + 180
        assert time.time() < target, "准备阶段已错过 expiry，前置条件失败"
        observation = {
            "expiry": expiry_ms / 1000,
            "target": target,
            "cutoff": cutoff,
            "closed_before_cutoff": False,
        }
        last_absent = time.time()
        commitment = self._spender(self.funding)
        seen_wall, seen_mono = time.time(), time.monotonic()
        phase = "before_expiry"
        if not commitment:
            # 留至少两分钟到期前余量；随后真实跨过 E，截止不随调时落点移动。
            minutes = max(0, int((target - time.time() - 120) // 60))
            if minutes:
                before_wall, before_mono = time.time(), time.monotonic()
                self.__class__._clock_changed = True
                assert change_time(0, minutes), "修改系统时间命令失败"
                after_wall, after_mono = time.time(), time.monotonic()
                shift_error = (
                    after_wall - before_wall - (after_mono - before_mono) - minutes * 60
                )
                assert abs(shift_error) < 5, f"调时偏差 {shift_error}s"
                observation["clock_shift"] = {
                    "before": before_wall,
                    "after": after_wall,
                    "minutes": minutes,
                }
                assert after_wall < target, "调时落点越过 expiry，前置条件失败"
                commitment = self._spender(self.funding)
                seen_wall, seen_mono = time.time(), time.monotonic()
                if commitment:
                    phase = "during_clock_shift_time_unknown"
        anchor_wall, anchor_mono = time.time(), time.monotonic()
        if not commitment:
            assert anchor_wall < target, "开始完整观察前已错过 expiry，前置条件失败"
        while True:
            now, mono = time.time(), time.monotonic()
            assert (
                abs((now - anchor_wall) - (mono - anchor_mono)) < 5
            ), "观察期间系统时间跳变或休眠"
            assert mono < self._case_deadline, "本例 30 分钟预算已用尽"
            assert not self._peer_present(), "上游 A 意外恢复"
            if commitment:
                lock = commitment["outputs"][0]["lock"]
                assert (
                    lock["code_hash"] == COMMIT_LOCK_CODE_HASH
                ), "funding 消费者不是承诺交易"
                assert_commitment_args(
                    bytes.fromhex(lock["args"][2:]), "v1", derived=False
                )
                observation.update(
                    closed_before_cutoff=seen_wall <= cutoff,
                    observed_at=seen_wall,
                    observed_monotonic=seen_mono,
                    phase=phase if seen_wall <= cutoff else "first_seen_after_cutoff",
                    transaction=commitment["hash"],
                    last_absent_at=last_absent,
                    expiry_trigger_proven=False,
                )
                return (commitment if seen_wall <= cutoff else None), observation
            if now >= cutoff:
                observation.update(
                    observed_at=now,
                    phase="not_observed_by_cutoff",
                    last_absent_at=last_absent,
                )
                observation["channel_at_cutoff"] = self._channel(self.fiber1)["state"]
                return None, observation
            last_absent = now
            time.sleep(min(1, cutoff - now))
            commitment = self._spender(self.funding)
            seen_wall, seen_mono = time.time(), time.monotonic()
            phase = "before_expiry" if time.time() < target else "observed_in_window"

    def _assert_claimed_funds(self, commitment, deadline=None):
        """沿真实消费链逐笔核对应收款、本金、目的锁及费用，最后对账钱包增量。"""
        deadline = deadline if deadline is not None else time.monotonic() + 660
        self._wait(
            "承诺确认",
            lambda: self.ckb.get_transaction(commitment["hash"])["tx_status"]["status"]
            == "committed",
            max(0, deadline - time.monotonic()),
        )
        packed = self.ckb.get_transaction(commitment["hash"], "0x0")["transaction"]
        assert commitment_hash_without_deps(packed) == self.latest_commitment
        assert_commitment_args(
            bytes.fromhex(commitment["outputs"][0]["lock"]["args"][2:]), "v1"
        )
        # 本夹具承诺交易仅花 funding，无 B 钱包补充输入；费用留在未领取的对手份额。
        assert len(commitment["inputs"]) == len(commitment["outputs"]) == 1
        assert commitment["inputs"][0]["previous_output"] == {
            "tx_hash": self.funding["hash"],
            "index": "0x0",
        }
        commitment_fee = int(self.funding["outputs"][0]["capacity"], 16) - int(
            commitment["outputs"][0]["capacity"], 16
        )
        assert 0 < commitment_fee < 100_000, commitment_fee
        principal = self.expected[0] + (0 if self.udt else DEFAULT_MIN_DEPOSIT_CKB)
        claimed, got_principal, total_fees = set(), False, 0
        total_capacity, total_tokens = 0, 0
        current = commitment
        while not (got_principal and claimed == set(self.claims)):
            assert time.monotonic() < deadline, "链上回款预算已用尽"
            self.ckb.generate_epochs("0x2")
            tx = self._wait(
                "本金及应收款兑现",
                lambda: self._spender(current),
                max(1, deadline - time.monotonic()),
            )
            self._wait(
                "领取交易确认",
                lambda: self.ckb.get_transaction(tx["hash"])["tx_status"]["status"]
                == "committed",
                max(1, deadline - time.monotonic()),
            )
            witness = SettlementWitness.from_hex(tx["witnesses"][0], version="v1")
            released, principal_step = 0, False
            for unlock in witness.unlocks:
                if unlock.unlock_type < 0xFE:
                    tlc = witness.tlcs[unlock.unlock_type]
                    payment_hash = "0x" + tlc.payment_hash.hex()
                    assert payment_hash in self.claims and payment_hash not in claimed
                    claim = self.claims[payment_hash]
                    assert tlc.amount == claim["amount"]
                    assert unlock.preimage == bytes.fromhex(claim["preimage"][2:])
                    released += tlc.amount
                    claimed.add(payment_hash)
                else:
                    assert unlock.unlock_type == 0xFE and not got_principal
                    assert witness.remote_amount == principal, (
                        witness.remote_amount,
                        principal,
                    )
                    released += principal
                    got_principal, principal_step = True, True
            inputs, input_data = [], []
            for item in tx["inputs"]:
                point = item["previous_output"]
                previous = self.ckb.get_transaction(point["tx_hash"])["transaction"]
                index = int(point["index"], 16)
                inputs.append(previous["outputs"][index])
                input_data.append(previous["outputs_data"][index])
            assert tx["inputs"][0]["previous_output"] == {
                "tx_hash": current["hash"],
                "index": "0x0",
            }
            assert all(c["lock"] == self.locks[0] for c in inputs[1:])
            fee = sum(int(c["capacity"], 16) for c in inputs) - sum(
                int(c["capacity"], 16) for c in tx["outputs"]
            )
            assert 0 < fee < 100_000, fee
            remaining = [
                c
                for c in tx["outputs"]
                if c["lock"]["code_hash"] == COMMIT_LOCK_CODE_HASH
            ]
            assert len(remaining) == 1 and tx["outputs"][0] == remaining[0]
            assert all(
                c["lock"] == self.locks[0] or c in remaining for c in tx["outputs"]
            )
            net_capacity = sum(
                int(c["capacity"], 16)
                for c in tx["outputs"]
                if c["lock"] == self.locks[0]
            ) - sum(int(c["capacity"], 16) for c in inputs[1:])
            if self.udt:
                occupied = (
                    8
                    + 32
                    + 1
                    + 58
                    + 32
                    + 1
                    + len(bytes.fromhex(self.udt["args"][2:]))
                    + 16
                ) * CKB
                capacity_release = (
                    int(current["outputs"][0]["capacity"], 16) - occupied
                    if principal_step
                    else 0
                )
                net_tokens = sum(
                    int.from_bytes(bytes.fromhex(d[2:]), "little")
                    for c, d in zip(tx["outputs"], tx["outputs_data"])
                    if c["lock"] == self.locks[0] and c["type"] == self.udt
                )
                net_tokens -= sum(
                    int.from_bytes(bytes.fromhex(d[2:]), "little")
                    for c, d in zip(inputs[1:], input_data[1:])
                    if c["type"] == self.udt
                )
                assert net_tokens == released, (net_tokens, released)
                assert (
                    int.from_bytes(
                        bytes.fromhex(current["outputs_data"][0][2:]), "little"
                    )
                    - int.from_bytes(bytes.fromhex(tx["outputs_data"][0][2:]), "little")
                    == released
                )
            else:
                capacity_release, net_tokens = released, 0
            assert net_capacity == capacity_release - fee, (
                net_capacity,
                capacity_release,
                fee,
            )
            assert (
                int(current["outputs"][0]["capacity"], 16)
                - int(remaining[0]["capacity"], 16)
                == capacity_release
            )
            total_capacity += capacity_release
            total_tokens += net_tokens
            total_fees += fee
            print(
                "settlement:",
                tx["hash"],
                "released:",
                released,
                "fee:",
                fee,
                flush=True,
            )
            current = tx
            assert time.monotonic() < deadline
        expected_delta = total_capacity - total_fees, total_tokens
        self._wait(
            "钱包实际到账",
            lambda: tuple(
                a - b for a, b in zip(self._wallet_assets(), self.wallet_before)
            )
            == expected_delta,
            max(1, deadline - time.monotonic()),
        )
        if self.claims:
            self._wait(
                "本方 TLC 已终结",
                lambda: all(
                    tlc_is_terminal(t)
                    for t in self._channel(self.fiber1)["pending_tlcs"]
                ),
                max(1, deadline - time.monotonic()),
            )
        print(
            "wallet delta:",
            expected_delta,
            "claimed hashes:",
            sorted(claimed),
            flush=True,
        )
        return {
            "settlement_fees": total_fees,
            "commitment_fee": commitment_fee,
            "wallet_delta": expected_delta,
            "claimed_hashes": sorted(claimed),
        }
