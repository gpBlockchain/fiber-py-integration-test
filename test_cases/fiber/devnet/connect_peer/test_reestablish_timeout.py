"""旧通道升级到当前版本后的恢复超时、资金回收及误关保护。"""

import hashlib
from pathlib import Path
import secrets
import shutil
import socket
import time

import pytest

from framework.basic_fiber import COMMIT_LOCK_CODE_HASH, FiberTest
from framework.config import DEFAULT_MIN_LEDGER_DEPOSIT_CKB
from framework.helper.settlement_witness import SettlementWitness
from framework.test_fiber import FiberConfigPath
from framework.util import get_project_root
from test_cases.fiber.devnet.compatibility.contract_upgrade_support import (
    commitment_hash_without_deps,
    tlc_is_terminal,
)
from test_cases.fiber.devnet.migration._helpers import start_with_confirm

ROOT = Path(get_project_root())
CKB = 100_000_000


class TestReestablishTimeout(FiberTest):
    fiber_version = FiberConfigPath.V091_DEV
    tmp_path_name = "tmp-reestablish-timeout"
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 2}

    @classmethod
    def setup_class(cls):
        assert not (ROOT / cls.tmp_path_name).exists(), "保留并检查已有测试目录"
        for port in (
            cls.ckb_rpc_port,
            cls.ckb_p2p_port,
            cls.fiber1_rpc_port,
            cls.fiber1_p2p_port,
            cls.fiber2_rpc_port,
            cls.fiber2_p2p_port,
            cls.extra_fiber_rpc_port,
            cls.extra_fiber_p2p_port,
        ):
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", port))
        super().setup_class()

    def setup_method(self, method):
        self.fiber_version = (
            FiberConfigPath.CURRENT_DEV
            if method.__name__.endswith("_v2")
            else FiberConfigPath.V091_DEV
        )
        try:
            super().setup_method(method)
            self.ckb = self.node.getClient()
            self.pubkeys = [f.get_pubkey() for f in self.fibers]
            self.locks = [
                self.get_account_script(f.account_private) for f in self.fibers
            ]
            for fiber in self.fibers:
                fiber.get_client().try_count = 1
                fiber.get_client().timeout = 10
            self.backups = ROOT / self.tmp_path_name / "case-backups"
            self.backups.mkdir()
            self.udt = None
            self.amount = 0
            self.health = None
        except Exception:
            for fiber in reversed(getattr(self, "fibers", [])):
                fiber.stop()
                fiber.clean()
            raise

    def teardown_method(self, method):
        for fiber in reversed(self.fibers):
            fiber.stop()
            fiber.clean()
        shutil.rmtree(self.backups)

    @classmethod
    def teardown_class(cls):
        super().teardown_class()
        shutil.rmtree(ROOT / cls.tmp_path_name, ignore_errors=True)

    # TEST-MAP: PR1711-01
    def test_lost_peer_closes_ckb_and_healthy_channel_keeps_paying(self):
        """
        1. 创建并升级 Legacy CKB 通道，另建 A–C 健康通道。
        2. B 保留身份丢库后重连，等待期间持续核对 A–C 双向付款和余额。
        3. A–B 在 300＋90 秒内自动强关，核对 A 链上本金到账。
        """
        # 1. 两条真实通道分别保存账本。
        self._prepare_channel()
        self._prepare_healthy_channel()
        # 2. 故障对端保持在线，健康通道每 30 秒执行双向探针。
        self._lose_peer_data()
        self.wallet_before = self._wallet_assets()
        started = self._start_peer()
        # 3. 自动关闭期限和实际资金回收分别检查。
        commitment = self._wait_auto_close(started, health=self._probe_healthy)
        self._check_funds(commitment, time.monotonic() + 660)
        self._probe_healthy()

    # TEST-MAP: PR1711-01
    def test_lost_peer_closes_udt_and_healthy_channel_keeps_paying(self):
        """
        1. 创建并升级 Legacy UDT 通道，另建 A–C 健康通道。
        2. B 保留身份丢库后重连，等待期间持续核对 A–C 双向付款和余额。
        3. A–B 自动强关，分别核对 A 代币到账及 CKB 预留和手续费。
        """
        # 1. UDT 与 CKB 使用相同故障及判据，资产分开核算。
        self._prepare_channel(udt=True)
        self._prepare_healthy_channel()
        # 2. B 空库重连，A–C 继续正常付款。
        self._lose_peer_data()
        self.wallet_before = self._wallet_assets()
        started = self._start_peer()
        # 3. 不以节点状态变化代替代币实际到账。
        commitment = self._wait_auto_close(started, health=self._probe_healthy)
        self._check_funds(commitment, time.monotonic() + 660)
        self._probe_healthy()

    # TEST-MAP: PR1711-02
    def test_offline_time_does_not_count_toward_new_reconnect(self):
        """
        1. 升级 Legacy CKB 通道，B 丢库后重连并等待 150 秒。
        2. 停止 B 并观察 390 秒，离线期间原 funding 必须保持未消费。
        3. B 再次重连，按新的完整窗口自动强关并回收 A 本金。
        """
        # 1. 无在途付款，避免其他 TLC 到期路径介入。
        self._prepare_channel()
        self._lose_peer_data()
        self.wallet_before = self._wallet_assets()
        self._start_peer()
        self._observe(150, connected=True)
        # 2. 离线时间超过恢复期限仍不自动关闭。
        self._stop_peer()
        self._observe(390, connected=False)
        # 3. 新窗口前 290 秒不强关，390 秒内须自动广播。
        started = self._start_peer()
        commitment = self._wait_auto_close(started)
        self._check_funds(commitment, time.monotonic() + 660)

    # TEST-MAP: PR1711-03
    def test_local_restart_preserves_timeout_and_funds(self):
        """
        1. 升级 Legacy CKB 通道，B 丢库重连后等待 150 秒。
        2. 正常停止 A，从原目录重启并恢复同一通道和余额。
        3. 最终重连后 390 秒内自动强关，A 本金按实际费用到账。
        """
        # 1. 以公开连接和链查询建立无响应等待窗口。
        self._prepare_channel()
        self._lose_peer_data()
        self.wallet_before = self._wallet_assets()
        self._start_peer()
        self._observe(150, connected=True)
        before = self._channel(self.fiber1)
        # 2. 不回滚 A 的数据库，只做正常停启。
        self.fiber1.stop()
        started = time.monotonic()
        self.fiber1.start(timeout=120)
        self.fiber1.connect_peer(self.fiber2)
        self._wait("A 重启后双方重连", self._connected, 120)
        after = self._channel(self.fiber1)
        for key in (
            "channel_id",
            "channel_outpoint",
            "local_balance",
            "remote_balance",
        ):
            assert after[key] == before[key], (key, before[key], after[key])
        # 3. 此用例不要求持久化重启前的已等待秒数。
        commitment = self._wait_auto_close(started, minimum=0)
        self._check_funds(commitment, time.monotonic() + 660)

    # TEST-MAP: PR1711-04
    def test_healthy_recovery_fulfills_hold_without_force_close_legacy(self):
        """
        1. 升级 Legacy 通道并建立唯一 B→A 持单。
        2. B 正常离线 390 秒后原目录恢复，结算持单。
        3. 再观察 390 秒无误关，终态和余额正确且双向付款成功。
        """
        # 1. 使用真实旧通道升级路径。
        self._prepare_channel()
        self._hold_inbound()
        # 2. 对端保留完整数据恢复并接收原像。
        self._recover_and_settle_hold()
        # 3. 观察完整超时窗口，排除遗留计时器误关。
        self._observe(390, connected=True, state="ChannelReady")
        self._pay(0, CKB)
        self._pay(1, CKB)

    # TEST-MAP: PR1711-04
    def test_healthy_recovery_fulfills_hold_without_force_close_v2(self):
        """
        1. 用 current 双方新建 V2 通道并建立唯一 B→A 持单。
        2. B 正常离线 390 秒后原目录恢复，结算持单。
        3. 再观察 390 秒无误关，终态和余额正确且双向付款成功。
        """
        # 1. 本方法的两个节点从 setup 起即使用新版本。
        self._prepare_channel(upgrade=False)
        self._hold_inbound()
        # 2. V2 共享连接处理也必须保护正常离线恢复。
        self._recover_and_settle_hold()
        # 3. 确认正常恢复后不会被旧的超时任务关闭。
        self._observe(390, connected=True, state="ChannelReady")
        self._pay(0, CKB)
        self._pay(1, CKB)

    # TEST-MAP: PR1711-05
    def test_cooperative_close_with_lost_peer_recovers_ckb_hold(self):
        """
        1. 升级 Legacy CKB 通道并建立 B→A 持单。
        2. A 请求合作关闭，确认持单及 funding 仍在，再让 B 丢库重连。
        3. 自动强关后提交原像，核对 A 本金和应收款到账、发票 Paid。
        """
        # 1. 原像先保留在测试端。
        self._prepare_channel()
        self._hold_inbound()
        # 2. 合作关闭等待由持单稳定构造，不控制消息先后。
        started = self._cooperative_close_then_lose_peer()
        # 3. 只在自动广播后提交原像。
        commitment = self._wait_auto_close(started)
        deadline = time.monotonic() + 660
        self._settle_hold()
        self._check_funds(commitment, deadline)

    # TEST-MAP: PR1711-05
    def test_cooperative_close_with_lost_peer_recovers_udt_hold(self):
        """
        1. 升级 Legacy UDT 通道并建立 B→A 持单。
        2. A 请求合作关闭，确认持单及 funding 仍在，再让 B 丢库重连。
        3. 自动强关后提交原像，精确核对代币本金、应收款及 CKB 费用。
        """
        # 1. 使用 UDT 变体，账本金额与 CKB 预留分离。
        self._prepare_channel(udt=True)
        self._hold_inbound()
        # 2. 同一公开运维过程构造合作关闭中的无响应对端。
        started = self._cooperative_close_then_lose_peer()
        # 3. 代币到账及发票终态使用同一个 660 秒总期限。
        commitment = self._wait_auto_close(started)
        deadline = time.monotonic() + 660
        self._settle_hold()
        self._check_funds(commitment, deadline)

    # TEST-MAP: PR1711-06
    def test_old_directory_never_broadcasts_revoked_commitment(self):
        """
        1. 升级 Legacy 通道，停机复制 A 完整目录后恢复并完成三笔付款。
        2. 保留 A 最新目录，换回旧副本连接 B，观察 390 秒不广播旧承诺。
        3. 恢复最新副本，双向付款成功且账本正确。
        """
        # 1. 两份目录都来自真实可付款的节点，不改数据库字段。
        self._prepare_channel()
        self.fiber1.stop()
        old = self.backups / "a-old-directory"
        shutil.copytree(self.fiber1.tmp_path, old)
        self.fiber1.start(timeout=120)
        self.fiber1.connect_peer(self.fiber2)
        self._wait("复制后恢复连接", self._connected, 120)
        for _ in range(3):
            self._pay(0, CKB)
        latest_balance = list(self.expected)
        # 2. B 保留更高承诺计数；原 funding 在整个观察窗口内保持 live。
        self.fiber1.stop()
        latest = self.backups / "a-latest-directory"
        shutil.move(self.fiber1.tmp_path, latest)
        shutil.copytree(old, self.fiber1.tmp_path)
        self.fiber1.start(timeout=120)
        self.fiber1.connect_peer(self.fiber2)
        self._wait("旧副本与 B 连接", self._connected, 120)
        assert self.fiber1.get_pubkey() == self.pubkeys[0]
        self._observe(390, connected=True)
        # 3. 换回最新数据后必须仍能使用原通道，避免无效负例。
        self.fiber1.stop()
        shutil.move(self.fiber1.tmp_path, self.backups / "a-old-after-observation")
        shutil.copytree(latest, self.fiber1.tmp_path)
        self.fiber1.start(timeout=120)
        self.fiber1.connect_peer(self.fiber2)
        self._wait("最新副本恢复", self._connected, 120)
        self.expected = latest_balance
        self._pay(1, CKB)
        self._pay(0, CKB)

    # TEST-MAP: PR1711-07
    def test_cli_restored_stale_channel_waits_without_force_close(self):
        """
        1. 升级 Legacy 通道，B 离线后正常重启 A，取得启动自动生成的完整备份。
        2. 正常停止 A，使用正式 --restore 恢复并确认原通道为 Stale。
        3. B 保留身份丢库重连，观察 390 秒无误关且 RPC 持续可用。
        """
        # 1. 备份须由节点生成；筛选本次重启之后的新目录。
        self._prepare_channel()
        self._stop_peer()
        self.fiber1.stop()
        backup_root = Path(self.fiber1.tmp_path) / "fiber/backups"
        previous = set(backup_root.iterdir()) if backup_root.exists() else set()
        self.fiber1.start(timeout=120)
        assert self._channel(self.fiber1)["channel_id"] == self.channel_id

        def new_backup():
            candidates = (
                set(backup_root.iterdir()) - previous if backup_root.exists() else set()
            )
            return next(
                (
                    p
                    for p in sorted(candidates)
                    if all((p / f).is_file() for f in ("db/CURRENT", "key", "sk"))
                ),
                None,
            )

        backup = self._wait("启动自动备份完成", new_backup, 120)
        # 2. 恢复完整备份，确认通道等待核对且节点身份保持一致。
        self.restore_fiber_from_backup(
            self.fiber1, backup, self.backups / "a-before-restore"
        )
        assert self._channel(self.fiber1)["state"]["state_name"] == "Stale"
        assert self.fiber1.get_pubkey() == self.pubkeys[0]
        # 3. 对端空库没有可供审计的数据，A 应继续等待。
        self._move_peer_store()
        self._start_peer()
        self._observe(390, connected=True, state="Stale")

    # TEST-MAP: PR1711-08
    @pytest.mark.skip(reason="反复重连会重置恢复计时，按用户要求暂时跳过")
    def test_inbound_settlement_exits_despite_repeated_reconnects(self):
        """
        1. 升级 Legacy CKB 通道，唯一 B→A 持单稳定 60 秒。
        2. B 保留身份丢库重连，A 提交原像；按 150＋150＋90 秒窗口反复重连。
        3. 累计在线 390 秒内须自动强关，随后 660 秒内核对本金和应收款到账。
        """
        # 1. 保留原先的累计期限要求。
        self._prepare_channel()
        self._hold_inbound()
        # 2. 离线暂停，后续窗口只补足累计 390 秒。
        self._lose_peer_data()
        self.wallet_before = self._wallet_assets()
        online = 0.0
        commitment = None
        for round_index, limit in enumerate((150, 300, 390)):
            self._start_peer()
            started = time.monotonic()
            if round_index == 0:
                self._settle_hold()
            commitment = self._observe(
                max(0, limit - online), connected=True, allow_close=True
            )
            elapsed = time.monotonic() - started
            assert elapsed < 300, "场景构造失败：单次连接达到完整超时"
            online += elapsed
            print(
                "reconnect window:",
                round_index + 1,
                "online:",
                elapsed,
                "total:",
                online,
            )
            if commitment or round_index == 2:
                break
            self._stop_peer()
            commitment = self._observe(10, connected=False, allow_close=True)
            if commitment:
                break
        # 3. 超时仍悬挂保持失败，不能用尾部长连接改判。
        assert commitment is not None, f"累计在线 {online:.2f}s 后仍未自动广播强关"
        assert online <= 392, online
        self._check_funds(commitment, time.monotonic() + 660)

    def _wait(self, description, observe, timeout=120):
        deadline = time.monotonic() + timeout
        while True:
            value = observe()
            if value:
                return value
            assert (
                time.monotonic() < deadline
            ), f"{description}，{timeout:.1f}s 超时：{value}"
            time.sleep(0.5)

    def _channel(self, fiber, channel_id=None):
        channel_id = channel_id or self.channel_id
        rows = fiber.get_client().list_channels({"include_closed": True})["channels"]
        return next(c for c in rows if c["channel_id"] == channel_id)

    def _channels(self):
        return [self._channel(f) for f in (self.fiber1, self.fiber2)]

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

    def _assert_balances(self):
        def balances_match():
            return all(
                c["state"]["state_name"] == "ChannelReady"
                and int(c["local_balance"], 16) == self.expected[i]
                and int(c["remote_balance"], 16) == self.expected[1 - i]
                and int(c["offered_tlc_balance"], 16) == 0
                and int(c["received_tlc_balance"], 16) == 0
                and not c["pending_tlcs"]
                for i, c in enumerate(self._channels())
            )

        self._wait("付款账本及 TLC 清空", balances_match, 120)

    def _pay(self, sender_index, amount):
        sender, receiver = (self.fiber1, self.fiber2)[sender_index], (
            self.fiber1,
            self.fiber2,
        )[1 - sender_index]
        self._pay_between(sender, receiver, amount, self.udt)
        self.expected[sender_index] -= amount
        self.expected[1 - sender_index] += amount
        self._assert_balances()

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
        self.wait_payment_state(sender, payment["payment_hash"], "Success", timeout=120)
        self.wait_invoice_state(receiver, payment["payment_hash"], "Paid", timeout=60)

    def _prepare_channel(self, udt=False, upgrade=True):
        self.udt = (
            self.get_account_udt_script(self.fiber1.account_private) if udt else None
        )
        if udt:
            self.fiber1.get_client().open_channel(
                {
                    "pubkey": self.pubkeys[1],
                    "funding_amount": hex(500 * CKB),
                    "funding_udt_type_script": self.udt,
                    "public": True,
                }
            )
            self.channel_id = self.wait_for_new_channel_state(
                self.fiber1.get_client(),
                self.pubkeys[1],
                "ChannelReady",
                set(),
                timeout=120,
            )
        else:
            self.channel_id = self.open_channel(self.fiber1, self.fiber2, 500 * CKB, 0)
        self._wait(
            "双方通道 Ready",
            lambda: all(
                c["state"]["state_name"] == "ChannelReady" for c in self._channels()
            ),
        )
        self.expected = [int(c["local_balance"], 16) for c in self._channels()]
        self._pay(0, 100 * CKB)
        self._pay(1, CKB)
        before = self._channels()
        point = bytes.fromhex(before[0]["channel_outpoint"][2:])
        self.funding_point = {
            "tx_hash": "0x" + point[:32].hex(),
            "index": hex(int.from_bytes(point[32:], "little")),
        }
        assert self.funding_point["index"] == "0x0"
        self.funding = self.ckb.get_transaction(self.funding_point["tx_hash"])[
            "transaction"
        ]
        if upgrade:
            for index, fiber in enumerate((self.fiber1, self.fiber2)):
                fiber.stop()
                shutil.copytree(
                    Path(fiber.tmp_path) / "fiber/store",
                    self.backups / f"old-store-{index}",
                )
                fiber.fiber_config_enum = FiberConfigPath.CURRENT_DEV
            for fiber in (self.fiber1, self.fiber2):
                start_with_confirm(fiber, confirm="y", timeout=120)
            self.fiber1.connect_peer(self.fiber2)
            self._wait("升级后双方连接", self._connected)
            self._assert_balances()
            for old, new in zip(before, self._channels()):
                for key in (
                    "channel_id",
                    "channel_outpoint",
                    "local_balance",
                    "remote_balance",
                    "latest_commitment_transaction_hash",
                ):
                    assert old[key] == new[key], (key, old[key], new[key])
            self._pay(0, CKB)
            self._pay(1, CKB)
        self.latest_commitment = self._channel(self.fiber1)[
            "latest_commitment_transaction_hash"
        ]

    def _hold_inbound(self):
        self.amount = 2 * CKB + 17
        self.preimage = "0x" + secrets.token_hex(32)
        self.payment_hash = (
            "0x" + hashlib.sha256(bytes.fromhex(self.preimage[2:])).hexdigest()
        )
        invoice = self.fiber1.get_client().new_invoice(
            {
                "amount": hex(self.amount),
                "currency": "Fibd",
                "hash_algorithm": "sha256",
                "payment_hash": self.payment_hash,
                "expiry": hex(3600),
                "final_expiry_delta": hex(86_400_000),
                "udt_type_script": self.udt,
            }
        )
        payment = self.fiber2.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "max_parts": "0x1",
                "tlc_expiry_limit": hex(172_800_000),
                "timeout": hex(1800),
            }
        )
        assert payment["payment_hash"] == self.payment_hash
        self.wait_invoice_state(self.fiber1, self.payment_hash, "Received", timeout=60)
        self.wait_payment_state(self.fiber2, self.payment_hash, "Inflight", timeout=60)
        until = time.monotonic() + 60
        while time.monotonic() < until:
            a, b = self._channels()
            for c in (a, b):
                assert len(c["pending_tlcs"]) == 1, c
                tlc = c["pending_tlcs"][0]
                assert (
                    tlc["payment_hash"] == self.payment_hash
                    and int(tlc["amount"], 16) == self.amount
                )
                assert (
                    int(tlc["expiry"], 16) - time.time() * 1000 - 9_660_000
                    > 28 * 60_000
                )
            assert int(a["offered_tlc_balance"], 16) == 0
            assert int(a["received_tlc_balance"], 16) == self.amount
            assert (
                self.fiber1.get_client().get_invoice(
                    {"payment_hash": self.payment_hash}
                )["status"]
                == "Received"
            )
            time.sleep(1)
        self.latest_commitment = a["latest_commitment_transaction_hash"]

    def _settle_hold(self):
        self.fiber1.get_client().settle_invoice(
            {"payment_hash": self.payment_hash, "payment_preimage": self.preimage}
        )

    def _recover_and_settle_hold(self):
        self._stop_peer()
        self._observe(390, connected=False)
        self._start_peer(empty=False)
        self._settle_hold()
        self.wait_invoice_state(self.fiber1, self.payment_hash, "Paid", timeout=120)
        self.wait_payment_state(self.fiber2, self.payment_hash, "Success", timeout=120)
        payment = self.fiber2.get_client().get_payment(
            {"payment_hash": self.payment_hash}
        )
        assert payment["payment_preimage"] == self.preimage
        self.expected[0] += self.amount
        self.expected[1] -= self.amount
        self._assert_balances()

    def _cooperative_close_then_lose_peer(self):
        self.fiber1.get_client().shutdown_channel(
            {"channel_id": self.channel_id, "force": False}
        )
        self._wait(
            "合作关闭进入等待",
            lambda: self._channel(self.fiber1)["state"]["state_name"] == "ShuttingDown",
        )
        assert any(
            t["payment_hash"] == self.payment_hash
            for t in self._channel(self.fiber1)["pending_tlcs"]
        )
        self._assert_unspent()
        self._lose_peer_data()
        self.wallet_before = self._wallet_assets()
        return self._start_peer()

    def _stop_peer(self):
        self.fiber2.stop()
        self._wait("A 观察到 B 离线", lambda: not self._peer_present(), 30)

    def _move_peer_store(self):
        shutil.move(
            str(Path(self.fiber2.tmp_path) / "fiber/store"),
            str(self.backups / "b-before-loss"),
        )

    def _lose_peer_data(self):
        self._stop_peer()
        self._move_peer_store()

    def _start_peer(self, empty=True):
        started = time.monotonic()
        self.fiber2.start(timeout=120)
        assert self.fiber2.get_pubkey() == self.pubkeys[1]
        self.fiber1.connect_peer(self.fiber2)
        self._wait(
            "双方恢复连接", self._connected, max(1, 120 - (time.monotonic() - started))
        )
        assert time.monotonic() - started < 120
        if empty:
            assert (
                self.fiber2.get_client().list_channels({"include_closed": True})[
                    "channels"
                ]
                == []
            )
        return time.monotonic()

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

    def _observe(self, seconds, connected, state=None, allow_close=False):
        deadline = time.monotonic() + seconds
        while True:
            assert (
                self._connected() if connected else not self._peer_present()
            ), "观察窗口连接状态不符合前提"
            commitment = self._spender(self.funding)
            if commitment and allow_close:
                return commitment
            assert commitment is None, "不应在此观察窗口内广播关闭"
            assert (
                self.ckb.get_live_cell("0x0", self.funding["hash"], False)["status"]
                == "live"
            )
            channel = self._channel(self.fiber1)
            if state:
                assert channel["state"]["state_name"] == state, channel
            if time.monotonic() >= deadline:
                return None
            time.sleep(max(0, min(1, deadline - time.monotonic())))

    def _wait_auto_close(self, started, health=None, minimum=290):
        next_probe = started
        while True:
            assert self._connected(), "自动强关观察期间发生意外断连"
            commitment = self._spender(self.funding)
            elapsed = time.monotonic() - started
            if commitment:
                assert elapsed >= minimum, f"自动强关过早：{elapsed:.2f}s"
                assert elapsed <= 392, f"自动强关超过观察上限：{elapsed:.2f}s"
                print(
                    "automatic close after:",
                    round(elapsed, 2),
                    "seconds",
                    commitment["hash"],
                )
                return commitment
            assert (
                elapsed < 390
            ), f"重连 {elapsed:.2f}s 后仍未自动广播强关：{self._channel(self.fiber1)}"
            if health and time.monotonic() >= next_probe:
                health()
                next_probe = time.monotonic() + 30
            time.sleep(1)

    def _prepare_healthy_channel(self):
        c = self.start_new_fiber(
            self.generate_account(1000), fiber_version=FiberConfigPath.CURRENT_DEV
        )
        c.get_client().try_count, c.get_client().timeout = 1, 10
        channel_id = self.open_channel(self.fiber1, c, 300 * CKB, 0)
        self._pay_between(self.fiber1, c, 5 * CKB)
        self._wait(
            "健康通道 TLC 清空",
            lambda: not self._channel(self.fiber1, channel_id)["pending_tlcs"],
        )
        self.health = (
            c,
            channel_id,
            [
                int(self._channel(f, channel_id)["local_balance"], 16)
                for f in (self.fiber1, c)
            ],
        )

    def _probe_healthy(self):
        c, channel_id, expected = self.health
        self._pay_between(self.fiber1, c, CKB)
        self._pay_between(c, self.fiber1, CKB)
        self._wait(
            "健康通道余额守恒",
            lambda: all(
                self._channel(f, channel_id)["state"]["state_name"] == "ChannelReady"
                and int(self._channel(f, channel_id)["local_balance"], 16)
                == expected[i]
                and not self._channel(f, channel_id)["pending_tlcs"]
                for i, f in enumerate((self.fiber1, c))
            ),
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

    def _check_funds(self, commitment, deadline):
        self._wait(
            "承诺交易确认",
            lambda: self.ckb.get_transaction(commitment["hash"])["tx_status"]["status"]
            == "committed",
            max(1, deadline - time.monotonic()),
        )
        packed = self.ckb.get_transaction(commitment["hash"], "0x0")["transaction"]
        assert commitment_hash_without_deps(packed) == self.latest_commitment
        assert len(bytes.fromhex(commitment["outputs"][0]["lock"]["args"][2:])) == 57
        commitment_fee = self.get_tx_message(commitment["hash"])["fee"]
        assert 0 < commitment_fee < 10_000
        principal = (
            self.expected[0]
            if self.udt
            else self.expected[0] + DEFAULT_MIN_LEDGER_DEPOSIT_CKB
        )
        claimed_principal, claimed_tlc = False, self.amount == 0
        total_fees = 0
        current = commitment
        while not (claimed_principal and claimed_tlc):
            # 通过公开 devnet 出块推进真实相对锁定期，不修改节点超时或系统时间。
            self.ckb.generate_epochs("0x2")
            tx = self._wait(
                "A 的链上资金兑现",
                lambda: self._spender(current),
                max(1, deadline - time.monotonic()),
            )
            self._wait(
                "结算交易确认",
                lambda: self.ckb.get_transaction(tx["hash"])["tx_status"]["status"]
                == "committed",
                max(1, deadline - time.monotonic()),
            )
            witness = SettlementWitness.from_hex(tx["witnesses"][0], version="legacy")
            inputs = [
                self.ckb.get_transaction(i["previous_output"]["tx_hash"])[
                    "transaction"
                ]["outputs"][int(i["previous_output"]["index"], 16)]
                for i in tx["inputs"]
            ]
            assert tx["inputs"][0]["previous_output"] == {
                "tx_hash": current["hash"],
                "index": "0x0",
            }
            assert all(c["lock"] == self.locks[0] for c in inputs[1:])
            fee = sum(int(c["capacity"], 16) for c in inputs) - sum(
                int(c["capacity"], 16) for c in tx["outputs"]
            )
            assert 0 < fee < 10_000
            total_fees += fee
            released, principal_step = 0, False
            for unlock in witness.unlocks:
                if unlock.unlock_type < 0xFE:
                    assert not claimed_tlc
                    witness.assert_pending_tlcs([(self.payment_hash, self.amount)])
                    assert unlock.preimage == bytes.fromhex(self.preimage[2:])
                    assert witness.tlcs[unlock.unlock_type].amount == self.amount
                    claimed_tlc = True
                    released += self.amount
                else:
                    assert unlock.unlock_type == 0xFE and not claimed_principal
                    assert witness.remote_amount == principal
                    claimed_principal, principal_step = True, True
                    released += principal
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
                    + 57
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
                before_tokens = int.from_bytes(
                    bytes.fromhex(current["outputs_data"][0][2:]), "little"
                )
                after_tokens = int.from_bytes(
                    bytes.fromhex(tx["outputs_data"][0][2:]), "little"
                )
                assert before_tokens - after_tokens == released
                assert all(c["type"] in (None, self.udt) for c in inputs[1:])
                net_tokens = sum(
                    int.from_bytes(bytes.fromhex(d[2:]), "little")
                    for c, d in zip(tx["outputs"], tx["outputs_data"])
                    if c["lock"] == self.locks[0] and c["type"] == self.udt
                )
                for i, c in zip(tx["inputs"][1:], inputs[1:]):
                    if c["type"] == self.udt:
                        previous = self.ckb.get_transaction(
                            i["previous_output"]["tx_hash"]
                        )["transaction"]
                        net_tokens -= int.from_bytes(
                            bytes.fromhex(
                                previous["outputs_data"][
                                    int(i["previous_output"]["index"], 16)
                                ][2:]
                            ),
                            "little",
                        )
                assert net_tokens == released
            else:
                capacity_release = released
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
            print("settlement:", tx["hash"], "released:", released, "fee:", fee)
            current = tx
            assert time.monotonic() < deadline, "资金回收超出 660 秒"
        if self.udt:
            expected_capacity = (
                int(commitment["outputs"][0]["capacity"], 16) - occupied - total_fees
            )
            expected_tokens = self.expected[0] + self.amount
            assert (
                int.from_bytes(bytes.fromhex(current["outputs_data"][0][2:]), "little")
                == self.expected[1] - self.amount
            )
        else:
            expected_capacity, expected_tokens = principal + self.amount - total_fees, 0
            assert (
                int(current["outputs"][0]["capacity"], 16)
                == self.expected[1]
                - self.amount
                + DEFAULT_MIN_LEDGER_DEPOSIT_CKB
                - commitment_fee
            )
        print(
            "wallet:",
            self.wallet_before,
            "current:",
            self._wallet_assets(),
            "expected delta:",
            (expected_capacity, expected_tokens),
        )
        self._wait(
            "A 钱包实际到账",
            lambda: tuple(
                a - b for a, b in zip(self._wallet_assets(), self.wallet_before)
            )
            == (expected_capacity, expected_tokens),
            max(1, deadline - time.monotonic()),
        )
        if self.amount:
            self._wait(
                "链上兑现后发票 Paid 且本方 TLC 终态",
                lambda: self.fiber1.get_client().get_invoice(
                    {"payment_hash": self.payment_hash}
                )["status"]
                == "Paid"
                and all(
                    tlc_is_terminal(t)
                    for t in self._channel(self.fiber1)["pending_tlcs"]
                ),
                max(1, deadline - time.monotonic()),
            )
