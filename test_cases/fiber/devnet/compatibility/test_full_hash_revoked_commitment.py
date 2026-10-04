"""H32V2-11: 已撤销承诺的撤销证据在升级后的合约下被链上惩罚。

原文件只有一个 ``TestFullHashRevokedCommitment`` 类，却把共享节点固定成 PR base 二进制
（``fiber_version = V091_DEV``），于是两端都只宣告 V1 之前的特性、协商出 Legacy，而它同时
又断言 57 字节 Legacy args。缺陷在于承诺版本来自开通时的 P2P 特性宣告
（``negotiated_commitment_contract_version``），而不是链上部署的合约：head 节点之间互相宣告
``ONCHAIN_FULL_PAYMENT_HASH`` → V1；PR base 节点不宣告，与 head 节点之间才协商出 Legacy。
安装 ``OLD_CONTRACT`` 只制造真实的升级窗口，绝不选择布局。本文件按评审行 H32V2-11 要求的
两个方向拆成两个类：

1. ``TestFullHashRevokedLegacyByOldPeer`` —— 旧节点发送旧证据，新节点执行惩罚。
   - 共享 ``fiber1`` 保持默认 head（``CURRENT_DEV``），它是惩罚方/被测监控，全程不重启；
     Legacy 对端在 ``setUp`` 里按类级 guard 惰性启动一次，固定
     ``FiberConfigPath.V091_DEV``（PR base 9a561b3）。旧对端不宣告完整哈希特性，与 head
     对端之间协商出 57 字节 Legacy 布局。
   - 升级窗口：先断言 live 代码 cell 是 ``NEW_CONTRACT``，装 ``OLD_CONTRACT`` 并断言
     OLD 字节，开 Legacy 通道、完成一次付款（旧状态被 RAA 撤销），再把同一 Type ID 原地
     升级回 ``NEW_CONTRACT``，共享 head 节点 PID/启动时间不变。
   - 在旧对端上做 store 回滚 + 强关；head 节点的内置 watchtower 用升级后的代码惩罚 57 字节
     Legacy 承诺。

2. ``TestFullHashRevokedV1`` —— 新节点发送 V1 旧证据，新节点执行惩罚。
   - 两端都是默认 head，通道协商为 V1：被撤销承诺 lock args 是 58 字节且最后一字节
     feature = ``0x01``。
   - 全程不安装 ``OLD_CONTRACT``（V1 从不在旧代码下执行）：先断言 live 代码 cell 是
     ``NEW_CONTRACT``，惩罚结束后再断言仍是 NEW 且两个节点都没重启。
   - 回滚 ``self.fiber2`` 的 store 并强关，由 ``self.fiber1`` 的 watchtower 惩罚。

共同前提：
- 承诺版本只由开通时的 P2P 特性宣告决定，与链上部署哪份合约无关。
- 被撤销承诺用文件原有的 store 回滚夹具产生：付款前 ``shutil.copytree`` 拷贝
  ``<tmp>/fiber/store``，强关前停节点 → 换回 store → 启动 → 重连 →
  ``shutdown_channel(force=True)``，于是该节点广播的正是付款 RAA 刚撤销的那格承诺。
- 惩罚方是 head 对端的内置 watchtower；惩罚只从链上证据断言，不看日志。

撤销 witness 形状（来自合约与 watchtower 源码，两版一致）：
- 合约 ``commitment-lock`` 的撤销分支与 HTLC/settlement 布局无关：``resolve_htlc_layout``
  只作用于 settlement 分支；撤销分支固定要求前 16 字节 XUDT 兼容前缀、``witness[16] == 0``
  （unlock_count，0 表示撤销路径）、``witness[17..25]`` 大端 commitment number；合约只要求它
  不小于被撤销锁 ``args[28..36]``（``InvalidRevocationVersion`` 拒绝 current > new），
  watchtower 的匹配条件同样是 ``revocation_data.commitment_number >=`` 被广播承诺的版本号。
  其后是 32 字节 x-only 聚合公钥 + 64 字节聚合签名。
- watchtower ``build_revocation_tx`` 也按同一常量拼装：16 + 1 + 8 + 32 + 64 = 121 字节。
  因此 Legacy（57 字节 args）与 V1（58 字节 args）的撤销 witness 形状相同，差别只在被撤销
  承诺的 args 长度，断言里对此显式说明。

限制：
- 没有 RPC 暴露 store 里的 ``revocation_data``，"惩罚而不是普通逐笔结算" 只能由链上 witness
  形状、被撤销 outpoint 的消费关系和空的 TLC/解锁列表证明，不读 store、不看日志。
- store 回滚刻意只回退一格：再退会让撤销签名与被撤销承诺的 lock args 不匹配。
- 强关顺序是"恢复 store → 重启 → 等通道加载 → 强关 → 再重连"：先重连会让对端用新承诺
  追平回滚掉的旧状态，那格旧承诺就不会上链（实测偶发）。
- 惩罚方通道的 WAITING_ONCHAIN_SETTLEMENT 由每 300 秒一次的 CheckChannelsShutdown 清除，
  所以收尾等待覆盖两轮（660 秒），等待期间持续出块。
- 两个方法都已在本机 devnet 实际执行通过（2 passed in 1250.82s）；证据见
  ``report/h32v2-runs/revoked-commitment-11.log``。
"""

import shutil
import socket
import subprocess
import time
from pathlib import Path

from framework.test_fiber import FiberConfigPath
from framework.util import ckb_hasher
from test_cases.fiber.devnet.compatibility.contract_upgrade_support import (
    CKB,
    NEW_CONTRACT,
    OLD_CONTRACT,
    ROOT,
    FullHashChannelSupport,
)

PAYMENT_AMOUNT = 1 * CKB
SETTLEMENT_FLAG = "WAITING_ONCHAIN_SETTLEMENT"
# 撤销 witness：16 字节 XUDT 兼容前缀 | 1 字节 unlock_count=0 | 8 字节大端 commitment number
# | 32 字节 x-only aggregated pubkey | 64 字节聚合签名 = 121 字节。该形状与承诺版本无关，
# 由合约撤销分支和 watchtower build_revocation_tx 共同固定（见模块 docstring）。
REVOCATION_PREFIX = bytes([16, 0, 0, 0] * 4)
REVOCATION_WITNESS_LENGTH = 121
UNLOCK_COUNT_OFFSET = 16
WITNESS_VERSION_OFFSET = 17
WITNESS_VERSION_LENGTH = 8
WITNESS_PUBKEY_OFFSET = 25
WITNESS_PUBKEY_LENGTH = 32
# commitment number 是 lock args 偏移 28 的 8 字节大端 u64。
COMMITMENT_NUMBER_OFFSET = 28
COMMITMENT_NUMBER_LENGTH = 8
LEGACY_ARGS_LENGTH = 57
V1_ARGS_LENGTH = 58
V1_FEATURE_BYTE = 0x01
# wallet_balances() 返回 [account1, account2]，select_peers 把 account1 设为发起方（惩罚方）。
PUNISHER_BALANCE_INDEX = 0


class RevokedCommitmentSupportMixin:
    """H32V2-11 两个方向共用的链上证据与 store 回滚夹具；不含测试方法。"""

    # 默认只监控本文件使用的共享节点；Legacy 类把惰性启动的旧对端端口也纳入基线。
    extra_monitored_ports = ()
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 2}

    @classmethod
    def node_processes(cls):
        """只采集本文件节点的 PID + 启动时间，供“没有多余重启”断言使用。"""
        processes = []
        ports = (
            cls.ckb_rpc_port,
            cls.fiber1_rpc_port,
            cls.fiber2_rpc_port,
            *cls.extra_monitored_ports,
        )
        for port in ports:
            pid = subprocess.check_output(
                ["lsof", "-nP", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"], text=True
            ).strip()
            started = subprocess.check_output(
                ["ps", "-p", pid, "-o", "lstart="], text=True
            ).strip()
            processes.append((pid, started))
        return processes

    # ---- 合约 / 承诺布局 -------------------------------------------------

    def _assert_contract_bytes(self, path):
        """live commitment-lock 代码 cell 必须正好是 ``path`` 的字节。"""
        cell = self.contract_code_cell()
        assert cell["output_data"] == "0x" + path.read_bytes().hex(), cell
        return cell

    def _lock_args(self, transaction):
        return bytes.fromhex(transaction["outputs"][0]["lock"]["args"][2:])

    def _assert_commitment_layout(self, transaction, expected_args_length):
        """承诺锁 args 长度按协商版本断言；V1 还要 feature 位为 0x01。"""
        args = self._lock_args(transaction)
        assert (
            len(args) == expected_args_length
        ), f"承诺锁 args 长度应为 {expected_args_length}，实际 {len(args)}: {args.hex()}"
        if expected_args_length == V1_ARGS_LENGTH:
            assert args[V1_ARGS_LENGTH - 1] == V1_FEATURE_BYTE, args.hex()
        return args

    def _commitment_number(self, args):
        """commitment number：lock args 偏移 28 的 8 字节大端 u64。"""
        return int.from_bytes(
            args[
                COMMITMENT_NUMBER_OFFSET : COMMITMENT_NUMBER_OFFSET
                + COMMITMENT_NUMBER_LENGTH
            ],
            "big",
        )

    def _wallet_lock(self, account):
        return {
            "code_hash": self.Config.CKB_DEFAULT_CONFIG[
                "ckb_block_assembler_code_hash"
            ],
            "hash_type": "type",
            "args": account["lock_arg"],
        }

    # ---- 等待 ------------------------------------------------------------

    def assert_nodes_running(self):
        """离线比较进程基线并把差异写进消息：能直接看出是哪一端的 PID/启动时间变了。"""
        processes = getattr(self, "processes", None)
        if processes is None:
            return
        observed = self.node_processes()
        assert (
            observed == processes
        ), f"节点重启或退出: baseline={processes} observed={observed}"

    def _wait_tx_spend(self, tx_hash, timeout=240):
        """等待 ``tx_hash:0`` 被确认消费；不跳过任何 delay epoch。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.assert_nodes_running()
            spent_by, _ = self.get_ln_cell_death_hash(tx_hash)
            if spent_by:
                result = self.ckb.get_transaction(spent_by)
                assert result["tx_status"]["status"] == "committed", result
                return result["transaction"]
            time.sleep(1)
        self.fail(f"cell 在 {timeout}s 内始终未被消费: {tx_hash}:0")

    def _mine_until_committed(self, tx_hash):
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        result = self.ckb.get_transaction(tx_hash)
        assert result["tx_status"]["status"] == "committed", result
        return result["transaction"]

    # ---- store 回滚夹具（付款前拷贝 → 停 → 换回 → 启动 → 重连 → 强关）------

    def _snapshot_store(self, fiber):
        """拷贝付款前的 store：那正是随后 RAA 会撤销的那格状态。"""
        store = Path(fiber.tmp_path) / "fiber/store"
        backup = Path(fiber.tmp_path) / "fiber-one-state-ago"
        assert store.exists(), f"fiber store 不存在: {store}"
        if backup.exists():
            shutil.rmtree(backup)
        shutil.copytree(store, backup)
        return backup

    def _wait_channel_loaded(self, fiber, timeout=60):
        """重启后等通道重新加载，否则 shutdown_channel 会落到还没恢复的通道上。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            channels = fiber.get_client().list_channels({"include_closed": True})[
                "channels"
            ]
            if any(c["channel_id"] == self.channel_id for c in channels):
                return
            time.sleep(1)
        self.fail(
            f"{fiber.rpc_port} 重启后 {timeout}s 内没有恢复通道 {self.channel_id}"
        )

    def _force_close(self, fiber, timeout=60):
        """有界重试强关；shutdown_channel 成功时返回 null，抛异常才算失败。"""
        deadline = time.monotonic() + timeout
        error = None
        while time.monotonic() < deadline:
            try:
                fiber.get_client().shutdown_channel(
                    {"channel_id": self.channel_id, "force": True}
                )
                return
            except Exception as exc:  # 通道尚未恢复/actor 尚未就绪
                error = exc
                time.sleep(1)
        self.fail(f"{fiber.rpc_port} 强关请求始终失败: {error}")

    def _restore_store_and_force_close(self, victim, punisher, backup):
        """换回保存的 store、重启，然后在不重连的情况下强关恢复后的节点。"""
        store = Path(victim.tmp_path) / "fiber/store"
        victim.stop()
        shutil.rmtree(store)
        shutil.copytree(backup, store)
        victim.start(fnn_log_level="debug")
        self._wait_channel_loaded(victim)
        # 先强关再重连：重连会让对端用新承诺把回滚掉的状态追平，那格旧承诺就永远不会上链。
        self._force_close(victim)
        # 强关后重连，让惩罚方按正常路径看到通道闭合与链上结算。
        punisher.connect_peer(victim)
        victim.connect_peer(punisher)

    def _revoked_commitment(self, funding_outpoint, timeout=90):
        """等待强关节点广播的承诺交易：它必须直接花费 funding output。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            pool = self.ckb.get_raw_tx_pool()
            for tx_hash in list(pool.get("pending") or []) + list(
                pool.get("proposed") or []
            ):
                tx = self.ckb.get_transaction(tx_hash)["transaction"]
                if (
                    tx["inputs"]
                    and tx["inputs"][0]["previous_output"] == funding_outpoint
                ):
                    return tx
            self.Miner.miner_with_version(self.node, "0x0")
            time.sleep(1)
        self.fail("恢复后的节点始终没有广播它那格旧承诺")

    def _mine_round(self):
        """推进一轮链上确认；有 pending 交易就先提交它。"""
        pool = self.ckb.get_raw_tx_pool()
        pending = list(pool.get("pending") or [])
        if pending:
            self.Miner.miner_until_tx_committed(self.node, pending[0])
        else:
            self.Miner.miner_with_version(self.node, "0x0")

    def _wait_closed(self, fiber, timeout=660):
        """等惩罚方把通道收尾为 Closed 且不带 WAITING_ONCHAIN_SETTLEMENT。

        节点每 300 秒才跑一次 CheckChannelsShutdown，链上结算确认后要等下一轮才会清标志；
        因此像 ContractUpgradeSupport.assert_local_closed 一样覆盖两轮（660 秒）再判定失败。
        等待期间持续出块，保证任何待确认的链上交易都能推进（框架默认不自动挖矿）。
        """
        deadline = time.monotonic() + timeout
        channel = None
        seen = None
        while time.monotonic() < deadline:
            self.assert_nodes_running()
            channel = self.channel(fiber)
            current = (
                channel["state"]["state_name"],
                str((channel.get("state") or {}).get("state_flags") or ""),
            )
            if current != seen:
                print(f"{fiber.rpc_port} 通道状态: {current}")
                seen = current
            if current[0] == "Closed" and SETTLEMENT_FLAG not in current[1]:
                return channel
            self._mine_round()
            time.sleep(1)
        self.fail(f"惩罚方始终没有完成 Closed: {channel}")

    # ---- 场景步骤 --------------------------------------------------------

    def _revoke_previous_commitment(self, victim, punisher):
        """完成一笔正常付款，使 victim 的前一格承诺被 RAA 撤销。

        返回 (付款前的 store 拷贝, payment_hash)。付款前先改动的
        ``latest_commitment_transaction_hash`` 只是状态推进的可见证据；撤销本身由下面的
        链上惩罚 witness 证明——没有对端的撤销秘密造不出这个 witness。
        """
        latest_before = self.channel(victim)["latest_commitment_transaction_hash"]
        one_state_ago = self._snapshot_store(victim)
        payment_hash = self.send_payment(punisher, victim, PAYMENT_AMOUNT)
        self.wait_payment_state(punisher, payment_hash, "Success", timeout=60)
        channels = []
        for _ in range(60):
            channels = [self.channel(f) for f in self.fibers]
            if all(not c["pending_tlcs"] for c in channels):
                break
            time.sleep(1)
        else:
            self.fail(f"付款移除/RAA 未完成: {channels}")
        latest_after = self.channel(victim)["latest_commitment_transaction_hash"]
        assert latest_after != latest_before, (latest_before, latest_after)
        return one_state_ago, payment_hash

    def _assert_revoked_commitment_is_punished(
        self, victim, punisher, expected_args_length, wallet_before
    ):
        """从链上证据断言 head 的 watchtower 惩罚了被撤销承诺。"""
        funding_outpoint = {"tx_hash": self.funding_tx, "index": "0x0"}
        revoked = self._revoked_commitment(funding_outpoint)
        self._mine_until_committed(revoked["hash"])
        revoked_args = self._assert_commitment_layout(revoked, expected_args_length)
        revoked_number = self._commitment_number(revoked_args)

        # 惩罚交易直接消费被撤销承诺 cell：build_revocation_tx 把它作为第一个 input，
        # 其余 input 只是惩罚方的矿工费，所以被撤销 outpoint 恰好出现一次。
        revoked_outpoint = {"tx_hash": revoked["hash"], "index": "0x0"}
        retribution = self._wait_tx_spend(revoked["hash"])
        assert (
            retribution["inputs"][0]["previous_output"] == revoked_outpoint
        ), retribution["inputs"]
        assert (
            sum(
                1
                for item in retribution["inputs"]
                if item["previous_output"] == revoked_outpoint
            )
            == 1
        ), retribution["inputs"]

        # output[0] 是 RAA 时记录的撤销输出，由惩罚方自己的 shutdown script 重建，所以资金
        # 归惩罚方（作弊的 victim 失去整条通道容量）。它的 capacity 与被撤销承诺 output[0]
        # 相同（两者都是 total - commitment fee），type 也一致（CKB 通道都是 null）；但被撤销
        # 承诺 output[0] 本身是 commitment-lock cell，lock 不可能与普通钱包锁相等，因此这里
        # 断言的是“归惩罚方所有 + capacity/type 与被撤销输出一致”，而不是 lock 逐字节相等。
        revoked_output = revoked["outputs"][0]
        returned_output = retribution["outputs"][0]
        assert int(returned_output["capacity"], 16) == int(
            revoked_output["capacity"], 16
        ), (returned_output, revoked_output)
        assert returned_output.get("type") == revoked_output.get("type"), (
            returned_output,
            revoked_output,
        )
        punisher_lock = self._wallet_lock(punisher.get_account())
        assert returned_output["lock"] == punisher_lock, (
            returned_output["lock"],
            punisher_lock,
        )
        assert (
            returned_output["lock"]
            == punisher.get_client().node_info()["default_funding_lock_script"]
        ), returned_output["lock"]

        # 被撤销承诺 cell 不能再作为 live cell 被消费。CKB 0.202 对本例已消费的承诺 cell 返回
        # "unknown"（实测 plain 与 include_tx_pool 两种取值的 get_live_cell 都是 unknown，而不是
        # "dead"），所以断言"不再是 live"，并额外要求该 outpoint 的唯一消费者就是这笔已确认的
        # 惩罚交易（上面的 inputs[0] 与恰好一次断言 + 这里的索引器死亡哈希）。
        assert self.ckb.get_live_cell("0x0", revoked["hash"])["status"] != "live"
        death_tx, _ = self.get_ln_cell_death_hash(revoked["hash"])
        assert death_tx == retribution["hash"], (death_tx, retribution["hash"])
        code_tx = self.current_contract_code_tx()
        assert {
            "out_point": {"tx_hash": code_tx, "index": "0x0"},
            "dep_type": "code",
        } in retribution["cell_deps"], retribution["cell_deps"]

        # 撤销 witness：121 字节常量形状。offset/长度与承诺版本无关——V1 只是被撤销承诺的
        # lock args 长 58 字节，撤销分支本身不读 feature 位，所以这里不按版本分叉。
        raw = bytes.fromhex(retribution["witnesses"][0][2:])
        assert len(raw) == REVOCATION_WITNESS_LENGTH, raw.hex()
        assert raw[: len(REVOCATION_PREFIX)] == REVOCATION_PREFIX, raw.hex()
        assert raw[UNLOCK_COUNT_OFFSET] == 0x00, raw.hex()
        witness_version = int.from_bytes(
            raw[
                WITNESS_VERSION_OFFSET : WITNESS_VERSION_OFFSET + WITNESS_VERSION_LENGTH
            ],
            "big",
        )
        # 合约只要求被撤销承诺的版本号不大于 witness 里的新版本号（main.rs 撤销分支用
        # InvalidRevocationVersion 拒绝 current_version > new_version），watchtower 只要求
        # revocation_data.commitment_number >= 被广播承诺的版本号（actor.rs 的匹配条件）。
        # 实测 store 回滚一格后 witness 版本比被撤销承诺版本大（1 -> 2），所以断言序关系而不是相等。
        assert revoked_number <= witness_version, (
            raw.hex(),
            revoked_number,
            witness_version,
        )
        witness_pubkey = raw[
            WITNESS_PUBKEY_OFFSET : WITNESS_PUBKEY_OFFSET + WITNESS_PUBKEY_LENGTH
        ]
        hasher = ckb_hasher()
        hasher.update(witness_pubkey)
        assert hasher.digest()[:20] == revoked_args[:20], (
            witness_pubkey.hex(),
            revoked_args.hex(),
        )
        # unlock_count=0 说明撤销路径没有按 TLC 结算，也没有解锁列表；SettlementWitness
        # 描述的是 settlement 分支，会拒绝这 121 字节尾随结构，所以这里按原始形状断言。

        # 惩罚方钱包按真实矿工费收到撤销输出：净增量 = 输出容量 - 惩罚交易矿工费。
        recovered = (
            self.wallet_balances()[PUNISHER_BALANCE_INDEX]
            - wallet_before[PUNISHER_BALANCE_INDEX]
        )
        returned = int(returned_output["capacity"], 16)
        assert returned - CKB // 100 <= recovered <= returned, (
            wallet_before,
            self.wallet_balances(),
            returned_output,
        )
        assert recovered > 0, (wallet_before, self.wallet_balances())
        return revoked, retribution


class TestFullHashRevokedLegacyByOldPeer(
    RevokedCommitmentSupportMixin, FullHashChannelSupport
):
    """H32V2-11（旧证据）: 旧节点广播 57 字节 Legacy 已撤销承诺，head 节点惩罚。"""

    tmp_path_name = f"report/h32v2-11-legacy-{time.time_ns()}"
    ckb_rpc_port, ckb_p2p_port = 24614, 24615
    fiber1_rpc_port, fiber1_p2p_port = 24628, 24627
    fiber2_rpc_port, fiber2_p2p_port = 24629, 24630
    extra_fiber_rpc_port, extra_fiber_p2p_port = 24700, 24800
    # 旧对端是本类的惩罚对象，必须纳入“没有多余重启”的进程基线。
    extra_monitored_ports = (extra_fiber_rpc_port,)

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
        # Legacy 样本必须来自固定的 PR base 二进制 9a561b3；共享节点保持框架默认 head。
        version = subprocess.check_output(
            [ROOT / FiberConfigPath.V091_DEV.fiber_bin_path, "--version"], text=True
        )
        assert "9a561b3" in version, version
        super().setup_class()
        cls.ckb = cls.node.getClient()

    def setUp(self):
        cls = self.__class__
        if not hasattr(cls, "legacy_peer"):
            # generate_account / start_new_fiber 都是实例方法：旧对端只能在 setUp 里按类级
            # guard 惰性启动一次，端口是 extra_fiber_rpc_port/extra_fiber_p2p_port。
            cls.legacy_peer = self.start_new_fiber(
                self.generate_account(5000), fiber_version=FiberConfigPath.V091_DEV
            )
        # 基线在旧对端起来之后采集：之后任何节点多余重启都会被 assert_nodes_running 发现。
        self.processes = self.node_processes()

    # TEST-MAP: H32V2-11
    # TEST-EVIDENCE-BEGIN: H32V2-11
    # Evidence | covered | 旧节点（V091_DEV，9a561b3）与 head 节点开通 57 字节 Legacy 通道：
    # 版本来自开通时 P2P 特性宣告（旧节点不宣告 ONCHAIN_FULL_PAYMENT_HASH），不是链上合约。
    # 先装 OLD_CONTRACT 制造真实升级窗口，付款撤销旧状态后把同一 Type ID 原地升级到
    # NEW_CONTRACT（head 与旧对端 PID/启动时间不变），再回滚旧对端 store 并强关。旧对端广播
    # 的正是付款 RAA 已撤销的那格承诺；head 的 watchtower 用升级后的代码惩罚：retribution
    # 只消费该承诺 outpoint（恰好 1 个 input 指向它且是首个 input），重建 RAA 时记录的撤销
    # 输出（惩罚方 shutdown script 所有，capacity/type 与被撤销承诺 output[0] 一致），被撤销
    # cell 变 dead，cell deps 指向升级后的合约代码，witness 是 121 字节撤销形状
    # （unlock_count=0、大端 commitment number 不小于被撤销 lock args[28..36]、其后 x-only 聚合
    # 公钥的 blake2b_256 前 20 字节等于 lock args 前 20 字节）。惩罚方钱包按真实矿工费收到该
    # 输出，通道最终 Closed 且不带 WAITING_ONCHAIN_SETTLEMENT。
    # Evidence | limitation | 没有 RPC 暴露 revocation_data，"惩罚而不是普通逐笔结算" 由链上
    # witness 形状、outpoint 消费关系与空的 TLC/解锁列表证明，不读 store、不看日志；store
    # 回滚刻意只回退一格（见模块 docstring）。
    # Evidence | limitation | 撤销 witness 形状与承诺版本无关（见模块 docstring），Legacy 与 V1
    # 共用同一 121 字节常量；本方法单独断言 Legacy 的 57 字节 args。
    # Evidence | run | 已在本机 devnet 执行通过（report/h32v2-runs/revoked-commitment-11.log；
    # 该文件两条方法合计 2 passed in 1250.82s）。实测：付款 0x752fb560… →
    # 旧对端强关广播 0x82c3712a…（57 字节 args）→ 惩罚交易 0x07591214… 消费该 outpoint。
    # 惩罚方通道状态按 ChannelReady -> Closed|UNCOOPERATIVE_REMOTE|WAITING_ONCHAIN_SETTLEMENT
    # -> Closed|UNCOOPERATIVE_REMOTE 收尾：节点每 300 秒才跑 CheckChannelsShutdown，
    # 本轮标志清除发生在这条 300 秒周期检查之后（等待上限 660 秒覆盖两轮）。
    # TEST-EVIDENCE-END: H32V2-11
    def test_legacy_revoked_commitment_is_punished_by_head_watchtower(self):
        # --- 1. 升级窗口：先装 PR base 合约，只让后面的原地升级成为真实字节变化 ---
        self._assert_contract_bytes(NEW_CONTRACT)
        self.upgrade_contract(OLD_CONTRACT)
        self._assert_contract_bytes(OLD_CONTRACT)

        # --- 2. 旧对端 + head 协商出 Legacy（57 字节）通道 ---
        self.select_peers(self.fiber1, self.legacy_peer, "legacy")
        self.open_ready()
        ready = [self.channel(f) for f in self.fibers]
        assert all(c["state"]["state_name"] == "ChannelReady" for c in ready), ready
        victim, punisher = self.legacy_peer, self.fiber1

        # --- 3. 付款撤销 victim 的前一格承诺 ---
        one_state_ago, payment_hash = self._revoke_previous_commitment(victim, punisher)

        # --- 4. 同一 Type ID 原地升级到 head 合约；两边进程身份不变 ---
        # upgrade_contract 内部会 assert_nodes_running，基线含旧对端端口。
        self.upgrade_contract(NEW_CONTRACT)
        self._assert_contract_bytes(NEW_CONTRACT)
        self.assert_nodes_running()

        # --- 5. 回滚旧对端 store 并强关，让 head 的 watchtower 惩罚 Legacy 承诺 ---
        wallet_before = self.wallet_balances()
        self.processes = None  # 允许 victim 唯一一次 store 回滚重启
        self._restore_store_and_force_close(victim, punisher, one_state_ago)
        self.processes = self.node_processes()  # 立刻重建基线，之后不允许再重启

        revoked, retribution = self._assert_revoked_commitment_is_punished(
            victim, punisher, LEGACY_ARGS_LENGTH, wallet_before
        )
        # Legacy 布局没有 feature 字节：57 字节 args 已在 helper 里断言（V1 才会多出 0x01）。
        assert len(self._lock_args(revoked)) == LEGACY_ARGS_LENGTH

        # --- 6. 惩罚方通道收尾 Closed，不带 WAITING_ONCHAIN_SETTLEMENT ---
        closed = self._wait_closed(punisher)
        assert closed["state"]["state_name"] == "Closed", closed
        assert SETTLEMENT_FLAG not in str(
            (closed.get("state") or {}).get("state_flags") or ""
        ), closed
        self.assert_nodes_running()
        print(
            "H32V2-11 Legacy: 付款",
            payment_hash,
            "被撤销承诺",
            revoked["hash"],
            "惩罚",
            retribution["hash"],
        )


class TestFullHashRevokedV1(RevokedCommitmentSupportMixin, FullHashChannelSupport):
    """H32V2-11（V1 旧证据）: head 节点广播 58 字节 V1 已撤销承诺，head 节点惩罚。"""

    tmp_path_name = f"report/h32v2-11-v1-{time.time_ns()}"
    ckb_rpc_port, ckb_p2p_port = 26614, 26615
    fiber1_rpc_port, fiber1_p2p_port = 26628, 26627
    fiber2_rpc_port, fiber2_p2p_port = 26629, 26630
    extra_fiber_rpc_port, extra_fiber_p2p_port = 26700, 26800

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
        # 本类只用 head 节点（框架默认 CURRENT_DEV）；不启动旧对端，也不装 OLD_CONTRACT。
        super().setup_class()
        cls.ckb = cls.node.getClient()

    def setUp(self):
        # 两个共享节点都是 head；除 victim 的 store 回滚外不允许任何重启。
        self.processes = self.node_processes()

    # TEST-MAP: H32V2-11
    # TEST-EVIDENCE-BEGIN: H32V2-11
    # Evidence | covered | 两个 head 节点（CURRENT_DEV）开通 V1 通道：双方都宣告
    # ONCHAIN_FULL_PAYMENT_HASH，被撤销承诺 lock args 为 58 字节且最后一字节 feature = 0x01。
    # 全程不安装 OLD_CONTRACT：本方法先断言 live 代码 cell 就是 NEW_CONTRACT，惩罚结束后再断言
    # 仍是 NEW_CONTRACT 且两个节点没有重启，因此 V1 只在新合约下执行。付款撤销旧状态后回滚
    # self.fiber2 的 store 并强关，self.fiber1 的 watchtower 惩罚：断言集合与 Legacy 方向一致
    # （只消费被撤销 outpoint、重建惩罚方所有的撤销输出、容量/type 与被撤销 output[0] 一致、
    # 被撤销 cell 变 dead、cell deps 指向新合约、惩罚方钱包按真实矿工费到账、通道 Closed 且无
    # WAITING_ONCHAIN_SETTLEMENT）。
    # Evidence | witness-shape | V1 的撤销 witness 仍是 121 字节同一形状，不因 58 字节布局改变：
    # 合约 revocation 分支只读 witness[16]（unlock_count=0）、witness[17..25]（大端 commitment
    # number，不小于被撤销 lock args[28..36]）与其后的 32 字节 x-only 聚合公钥 + 64 字节聚合签名，
    # resolve_htlc_layout 只作用于 settlement 分支；watchtower build_revocation_tx 的拼装也与
    # 版本无关。因此这里断言同一常量形状，并额外用 blake2b_256(公钥)[:20] == lock args[:20] 把
    # witness 绑定到 58 字节 V1 承诺。
    # Evidence | limitation | 没有 RPC 暴露 revocation_data，"惩罚而不是普通逐笔结算" 由链上
    # witness 形状与 outpoint 消费关系证明，不读 store、不看日志；store 回滚只回退一格。
    # Evidence | run | 已在本机 devnet 执行通过（report/h32v2-runs/revoked-commitment-11.log）。
    # 实测：付款 0xf5e8a46a… → 被撤销的 V1 承诺 0x67257fb0…（58 字节 args + feature 0x01）→
    # 惩罚交易 0x73c1f57b… 消费该 outpoint；通道状态与 Legacy 方向相同，WAITING 标志同样在
    # 300 秒周期检查后清除。
    # TEST-EVIDENCE-END: H32V2-11
    def test_v1_revoked_commitment_is_punished_by_head_watchtower(self):
        # --- 1. V1 只在 head 合约下执行：先断言 live 代码 cell 是 NEW_CONTRACT ---
        self._assert_contract_bytes(NEW_CONTRACT)

        # --- 2. 两个 head 节点协商出 V1（58 字节 + feature 0x01）通道 ---
        self.select_peers(self.fiber1, self.fiber2, "v1")
        self.open_ready()
        ready = [self.channel(f) for f in self.fibers]
        assert all(c["state"]["state_name"] == "ChannelReady" for c in ready), ready
        victim, punisher = self.fiber2, self.fiber1

        # --- 3. 付款撤销 victim 的前一格承诺 ---
        one_state_ago, payment_hash = self._revoke_previous_commitment(victim, punisher)

        # --- 4. 付款/撤销期间合约保持 NEW，两个节点都没有重启 ---
        self._assert_contract_bytes(NEW_CONTRACT)
        self.assert_nodes_running()

        # --- 5. 回滚 victim store 并强关，让 head 的 watchtower 惩罚 V1 承诺 ---
        wallet_before = self.wallet_balances()
        self.processes = None  # 允许 victim 唯一一次 store 回滚重启
        self._restore_store_and_force_close(victim, punisher, one_state_ago)
        self.processes = self.node_processes()  # 立刻重建基线，之后不允许再重启

        revoked, retribution = self._assert_revoked_commitment_is_punished(
            victim, punisher, V1_ARGS_LENGTH, wallet_before
        )
        # V1 布局：58 字节，最后一字节是 feature 0x01。
        revoked_args = self._lock_args(revoked)
        assert len(revoked_args) == V1_ARGS_LENGTH
        assert revoked_args[-1] == V1_FEATURE_BYTE

        # --- 6. 惩罚后合约仍是 NEW，惩罚方通道收尾 Closed 且不带 WAITING_ONCHAIN_SETTLEMENT ---
        self._assert_contract_bytes(NEW_CONTRACT)
        closed = self._wait_closed(punisher)
        assert closed["state"]["state_name"] == "Closed", closed
        assert SETTLEMENT_FLAG not in str(
            (closed.get("state") or {}).get("state_flags") or ""
        ), closed
        self.assert_nodes_running()
        print(
            "H32V2-11 V1: 付款",
            payment_hash,
            "被撤销承诺",
            revoked["hash"],
            "惩罚",
            retribution["hash"],
        )
