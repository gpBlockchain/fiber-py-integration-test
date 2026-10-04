"""H32-04: fixed PR-base stores -> PR-head startup, original channel and settlement.

Reference: test_data.py. Unlike that historical 0.7 -> 0.8 migration test, this
test does not invoke fnn-migrate or open a replacement channel after upgrading.
Always run serially with other devnet files.
"""

import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import time

from framework.config import get_tmp_path
from framework.test_fiber import FiberConfigPath
from framework.util import ckb_hash
from test_cases.fiber.devnet.migration._helpers import start_with_confirm
from test_cases.fiber.devnet.compatibility.contract_upgrade_support import (
    CKB,
    ROOT,
    ContractUpgradeSupport,
    tlc_is_terminal,
)


def tree_hashes(directory):
    return {
        str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


def validate_store_copy(binary, checkpoint, destination):
    """Production CLI's offline validation branch exits before starting services."""
    shutil.copytree(checkpoint, destination / "fiber/store")
    (destination / "config.yml").write_text("fiber: {}\nservices: []\n")
    command = [str(ROOT / binary), "--check-validate", "-d", str(destination)]
    result = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=60,
    )
    return dict(
        command=command,
        cwd=str(ROOT),
        exit_code=result.returncode,
        output=result.stdout,
    )


class TestFullHashOldData(ContractUpgradeSupport):
    fiber_version = FiberConfigPath.V091_DEV
    ckb_rpc_port, ckb_p2p_port = 22414, 22415
    fiber1_rpc_port, fiber1_p2p_port = 22428, 22427
    fiber2_rpc_port, fiber2_p2p_port = 22429, 22430
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 2}
    shared_fiber1_extra_config = {"fiber_auto_accept_channel_ckb_funding_amount": 0}
    commitment_version = "legacy"

    @classmethod
    def setup_class(cls):
        for port in (22414, 22415, 22428, 22427, 22429, 22430):
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", port))
        # 旧库必须由固定旧二进制产生；新节点用框架默认 FiberConfigPath.CURRENT_DEV。
        assert "9a561b3" in subprocess.check_output(
            [ROOT / cls.fiber_version.fiber_bin_path, "--version"], text=True
        )
        # 本用例靠 backup(admin) 生成一致 checkpoint；显式打开所需 RPC 模块，
        # 避免默认模块集缺失时把“取不到旧库快照”误判成升级失败。
        previous = os.environ.get("RPC_ENABLED_MODULES")
        os.environ["RPC_ENABLED_MODULES"] = (
            "channel,payment,graph,info,invoice,peer,pubsub,dev,watchtower,admin"
        )
        try:
            super().setup_class()
        finally:
            if previous is None:
                os.environ.pop("RPC_ENABLED_MODULES")
            else:
                os.environ["RPC_ENABLED_MODULES"] = previous
        cls.ckb = cls.node.getClient()
        cls.processes = cls.node_processes()

    # TEST-MAP: H32V2-05
    # TEST-EVIDENCE-BEGIN: H32V2-05
    # Evidence | partial | Real PR-base store read by the head binary: the used Legacy channel, the
    # pending-open request and the watch data all originate from the old node. The pending-open record is
    # now independently re-checked after reload (same temporary id, channel_outpoint still null) and every
    # pre/post-upgrade payment asserts Success instead of being fire-and-forget. Still missing: the
    # watch-channel record has no read RPC, so its Legacy semantics are proven only behaviourally by the
    # built-in watchtower settling the restored channel; migration-vs-direct-read stays a product decision.
    # TEST-EVIDENCE-END: H32V2-05
    # H32-04 证明链：用升级前真实数据库启动新节点，库内同时含“缺少版本字段”的已用通道、
    # 待开通记录和 Watchtower 记录；两种升级策略（原库直接读取 / 必须迁移）都不得改写旧语义。
    # 1) 输入真实性：通道与待开通请求都由固定旧二进制真实产生（有双向付款），不是空库、也不是
    #    用新结构序列化出的假数据；backup 后立刻记录旧库目录 SHA-256，结尾再校验一次，
    #    证明被测过程没有回头改写这份旧库。
    # 2) 旧库自证：同一 checkpoint 交给旧二进制 `--check-validate` 必须 exit 0 → 它是旧版本自身
    #    认可的真实存储，而不只是新代码能读的构造物。
    # 3) 旧记录可读且不静默切 V1：三类记录（通道状态、待开通记录、Watchtower 数据）能否被新节点
    #    解码，由“真实旧库启动 → 恢复原通道 → 强关结算”这条链路本身证明；恢复后承诺锁仍是
    #    57 字节、结算仍走 Legacy 85 字节条目。
    # 4) 新节点真实启动：新二进制既跑 `--check-validate`，也以 `start_with_confirm(confirm="y")`
    #    走真实启动/迁移路径；不调用历史 0.8.1 迁移器、不手改 DB 版本键。本行“待确认”的策略
    #    （直接读还是迁移）不做假定，只要求两条路径都保留 Legacy 语义且启动成功。
    # 5) 不重开替代通道：恢复后 channel_id / outpoint / 双方余额 / 最新承诺哈希与关停前逐字段
    #    相等 → 是同一条原通道被恢复，而不是升级后另开一条新通道。
    # 6) 恢复后可用：继续双向付款，并对该原通道强关；承诺锁仍为 57 字节、结算仍走 Legacy
    #    85 字节条目，最终付款 Success 且节点正常收尾 → 旧数据可继续付款与结算。
    def test_real_old_stores_start_and_settle_original_legacy_channel(self):
        # 证据写到本类自己的 report/<tmp_path_name> 下，与节点数据同一目录树统一清理，
        # 不再使用 .codex-artifacts，避免跑完留下没人回收的输出。
        evidence = Path(get_tmp_path()) / "evidence"
        evidence.mkdir(parents=True, exist_ok=True)
        records = []
        original_hashes = {}
        try:
            # 默认快照已部署升级后的 commitment-lock；旧库里的通道语义仍是 Legacy，
            # 本用例验证的正是“旧数据在合约已升级”的行为，无需再提交升级交易。
            code_tx = self.current_contract_code_tx()
            # 真实使用过的旧通道：固定旧二进制开通并双向付款，库内带真实余额。
            self.open_legacy_channel()
            original_id = self.channel_id
            # A genuine used channel, rather than an empty DB or serialized new struct.
            self.send_invoice_payment(self.fiber1, self.fiber2, CKB, True)
            self.send_invoice_payment(self.fiber2, self.fiber1, CKB // 2, True)
            old_channels = [self.channel(f) for f in self.fibers]
            assert all(
                all(tlc_is_terminal(t) for t in c["pending_tlcs"]) for c in old_channels
            )
            # 待开通记录输入：旧接收方自动接受为 0，保留一条 channel_outpoint 仍为空的真实
            # pending 请求，正好对应“缺少版本字段的待开通记录”。
            pending_id = self.fiber2.get_client().open_channel(
                {
                    "pubkey": self.fiber1.get_pubkey(),
                    "funding_amount": hex(100 * CKB),
                    "public": True,
                }
            )["temporary_channel_id"]
            for _ in range(60):
                pending = self.fiber1.get_client().list_channels(
                    {"only_pending": True}
                )["channels"]
                if any(
                    c["channel_id"] == pending_id and c["channel_outpoint"] is None
                    for c in pending
                ):
                    break
                time.sleep(1)
            else:
                self.fail(
                    f"Old receiver did not retain the manual pending request: {pending}"
                )
            # Backup before disconnect: the network actor may delete pending opens
            # on disconnect, so copying only after stop would silently lose this input.
            # 因此在断连/停进程之前取库：停后再拷会静默丢掉本行要求的待开通记录。
            checkpoints = []
            for index, fiber in enumerate(self.fibers):
                backups = Path(fiber.tmp_path) / "fiber/backups"
                before = set(backups.glob("*/db"))
                # backup RPC 产生一致的 RocksDB checkpoint，而不是拷贝热库。
                fiber.get_client().call("backup", [])
                created = set(backups.glob("*/db")) - before
                assert (
                    created
                ), "Old backup RPC must produce a consistent RocksDB checkpoint"
                checkpoint = evidence / f"old-node{index}/store"
                shutil.copytree(
                    max(created, key=lambda p: int(p.parent.name)), checkpoint
                )
                checkpoints.append(checkpoint)
                original_hashes[str(checkpoint)] = tree_hashes(checkpoint)
                # 旧二进制必须认得这份库：证明它确实是 PR base 的真实存储。
                baseline = validate_store_copy(
                    FiberConfigPath.V091_DEV.fiber_bin_path,
                    checkpoint,
                    evidence / f"validate-old-{index}",
                )
                records.append(baseline)
                assert baseline["exit_code"] == 0, baseline["output"]
            old_processes = self.processes
            # 停掉旧进程；下面会把不可变的旧 checkpoint 装回 live store 并以新二进制重启。
            for fiber in self.fibers:
                fiber.stop()
            self.processes = None
            failures = []
            # 旧库能否被新节点读取，由下面的离线校验 + 真实启动 + 原通道恢复共同证明；
            # 不把“CLI 通过”或“RPC 端口能监听”单独当成通道已经恢复。
            for index, (fiber, checkpoint) in enumerate(zip(self.fibers, checkpoints)):
                # 新二进制离线校验：无论实现是直接读还是迁移，这一步都必须通过。
                validation = validate_store_copy(
                    FiberConfigPath.CURRENT_DEV.fiber_bin_path,
                    checkpoint,
                    evidence / f"validate-new-{index}",
                )
                records.append(validation)
                if validation["exit_code"] != 0:
                    failures.append(
                        f"node{index} offline validation: {validation['output']}"
                    )
                # 用不可变的旧 checkpoint 覆盖 live store，并把版本切到新二进制：
                # 这是真实的“旧库 → 新节点”升级，而不是新库在新旧版本间往返。
                live_store = Path(fiber.tmp_path) / "fiber/store"
                shutil.rmtree(live_store)
                shutil.copytree(checkpoint, live_store)
                fiber.fiber_config_enum = FiberConfigPath.CURRENT_DEV
                started = time.monotonic()
                try:
                    # Same confirm behavior as test_data.py, but no 0.8.1 tool
                    # or hand-edited version key is inserted to rescue this upgrade.
                    start_with_confirm(fiber, confirm="y", timeout=90)
                    records.append(
                        dict(
                            stage=f"node{index} startup",
                            status="rpc_listening",
                            elapsed=time.monotonic() - started,
                        )
                    )
                except (RuntimeError, TimeoutError) as error:
                    records.append(
                        dict(
                            stage=f"node{index} startup",
                            status="failed",
                            elapsed=time.monotonic() - started,
                            output=str(error),
                        )
                    )
                    failures.append(str(error))
            assert not failures, "Old-data upgrade failed:\n" + "\n".join(failures)
            self.__class__.processes = self.node_processes()
            self.processes = self.__class__.processes
            # 真升级而不是同进程续跑：CKB 进程不变，两个 fiber 进程的 PID/启动时间必须全部变化。
            assert self.processes[0] == old_processes[0]
            assert all(
                new != old for new, old in zip(self.processes[1:], old_processes[1:])
            )
            # 重连并等待原通道恢复；同一 channel_id 必须回到 ChannelReady。
            self.fiber1.connect_peer(self.fiber2)
            for _ in range(60):
                restored = [self.channel(f) for f in self.fibers]
                if all(c["state"]["state_name"] == "ChannelReady" for c in restored):
                    break
                time.sleep(1)
            else:
                self.fail(f"Original channel not restored: {restored}")
            # 原通道而非替代通道：身份、outpoint、双方余额、最新承诺哈希逐字段与关停前一致。
            for before, after in zip(old_channels, restored):
                for field in (
                    "channel_id",
                    "channel_outpoint",
                    "local_balance",
                    "remote_balance",
                    "latest_commitment_transaction_hash",
                ):
                    assert after[field] == before[field], (
                        field,
                        before[field],
                        after[field],
                    )
            assert self.channel_id == original_id
            # 三类旧记录里的“待开通记录”在 reload 后仍必须存在且语义不变：同一 temporary id 仍在
            # pending 列表、channel_outpoint 仍为空（不是被静默丢弃或升级成新通道）。
            pending_after = self.fiber1.get_client().list_channels(
                {"only_pending": True}
            )["channels"]
            restored_pending = [
                c for c in pending_after if c["channel_id"] == pending_id
            ]
            assert (
                restored_pending
            ), f"pending-open record lost after old-store reload: {pending_after}"
            assert restored_pending[0]["channel_outpoint"] is None, restored_pending[0]
            # 恢复后仍可继续双向付款，证明旧数据不只是“能启动”。
            self.send_invoice_payment(self.fiber1, self.fiber2, CKB, True)
            self.send_invoice_payment(self.fiber2, self.fiber1, CKB, True)
            # Settle a fresh hold TLC on this original old channel after recovery.
            preimage = "0x" + secrets.token_hex(32)
            payment_hash = ckb_hash(preimage)
            invoice = self.fiber2.get_client().new_invoice(
                {
                    "amount": hex(CKB),
                    "currency": "Fibd",
                    "payment_hash": payment_hash,
                    "hash_algorithm": "ckb_hash",
                    "final_expiry_delta": hex(9_600_000),
                }
            )
            self.fiber1.get_client().send_payment(
                {"invoice": invoice["invoice_address"]}
            )
            self.wait_invoice_state(self.fiber2, payment_hash, "Received")
            for _ in range(60):
                channels = [self.channel(f) for f in self.fibers]
                active = [
                    [t for t in c["pending_tlcs"] if not tlc_is_terminal(t)]
                    for c in channels
                ]
                if all(
                    len(ts) == 1
                    and ts[0]["payment_hash"] == payment_hash
                    and "Committed" in ts[0]["status"].values()
                    for ts in active
                ):
                    break
                time.sleep(1)
            else:
                self.fail(f"Recovered channel hold TLC not committed: {active}")
            self.signed_hashes = [
                c["latest_commitment_transaction_hash"] for c in channels
            ]
            # Use the pre-hold baseline. The two post-restart payments above
            # transfer the same amount in opposite directions on this channel.
            self.principals = [int(c["local_balance"], 16) + 99 * CKB for c in restored]
            self.wallet_before = self.wallet_balances()
            commitment = self.force_close(self.fiber2)
            # 强关承诺锁仍为 57 字节 → 旧库通道语义仍是 Legacy，没有被静默切到 V1。
            assert (
                len(bytes.fromhex(commitment["outputs"][0]["lock"]["args"][2:])) == 57
            )
            self.ckb.generate_epochs("0x1")
            self.fiber2.get_client().settle_invoice(
                {"payment_hash": payment_hash, "payment_preimage": preimage}
            )
            settled = self.wait_for_spend(commitment["hash"])
            # assert_tlc_settlement 按 commitment_version=legacy 解析：85 字节条目 + 20 字节前缀哈希，
            # 并核对原像、金额与收款人到账；随后 assert_settled 核对双方钱包净增与总矿工费。
            self.assert_tlc_settlement(
                commitment, settled, code_tx, [(payment_hash, CKB)], preimage
            )
            self.principals[0] -= CKB
            self.principals[1] += CKB
            self.assert_settled(
                settled, code_tx, self.get_tx_message(commitment["hash"])["fee"]
            )
            # 付款侧记录 Success 且持有正确原像，节点最终正常关闭。
            self.wait_payment_state(self.fiber1, payment_hash, "Success", timeout=660)
            assert (
                self.fiber1.get_client().get_payment({"payment_hash": payment_hash})[
                    "payment_preimage"
                ]
                == preimage
            )
            self.assert_local_closed()
        finally:
            for index, fiber in enumerate(self.fibers):
                log = Path(fiber.tmp_path) / "node.log"
                if log.exists():
                    shutil.copyfile(log, evidence / f"node{index}.log")
            (evidence / "commands.json").write_text(
                json.dumps(records, indent=2) + "\n"
            )
            # 结尾复核旧 checkpoint 的 SHA-256 未变：若升级过程回头改写了这份旧库，这里直接暴露。
            for path, expected in original_hashes.items():
                assert (
                    tree_hashes(Path(path)) == expected
                ), "Immutable old checkpoint changed"
            (evidence / "original-sha256.json").write_text(
                json.dumps(original_hashes, indent=2) + "\n"
            )
