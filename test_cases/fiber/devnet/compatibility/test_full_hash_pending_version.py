"""H32V2-03: 待接受开通请求的版本固定与断连失效识别。

对应 `reviews/full-payment-hash-settlement-v2.md` 的 H32V2-03：

    接收开通时固定V1或Legacy，关闭自动接受；在原请求仍有效时对端重连改变特性，再接受原请求
    接受原请求沿用接收时固定版本，承诺格式一致；原请求若已被断连清理，应识别失效，
    不用重新发起的请求替代验证

本文件只自动化可观测的第二分支（“原请求若已被断连清理，应识别失效”），原因：

- 受害端 8b95af3（CURRENT_DEV）与对端 9a561b3（V091）的 `on_peer_disconnected` 都会把该对端的
  待接受记录清掉（8b95af3 直接删除持久化的 `ChannelOpenRecord` 并从内存
  `to_be_accepted_channels` 移除），所以断连后原 `temporary_channel_id` 不再可被 `accept_channel`。
- 参考实现对**主预期**（accept 复用接收时刻固定的版本）实际是有实现的：
  8b95af3 的 `PendingOpenChannel` 带 `commitment_contract_version`，`create_inbound_channel` 用它
  计算预留并用它协商承诺格式。但 `list_channels` 不暴露协商到的版本，且原请求在断连时已被清理，
  所以“接收时钉住的版本被复用”无法在本仓库直接观测。本文件不声称证明了主预期，也没有否定它。

流程：关闭自动接受 → 第一版对端（CURRENT_DEV，宣告 ONCHAIN_FULL_PAYMENT_HASH）发出开通请求并停在
待接受 → 杀掉该对端、确认受害端把原请求判为失效（8b95af3 下记录直接消失）→ 用**同一身份**但只宣告
Legacy 的 0.9.1 节点重连（node_info.features 直接证明宣告变了）→ 确认原请求仍不可接受、没有通道被
建出 → 对端重新发起请求，得到不同的 temporary id，显式 accept 后两端 ChannelReady，强关承诺锁是
57 字节 Legacy，即新请求按对端当前宣告重新协商，而不是复用被清理的原请求。
"""

import os
import shutil
import socket
import subprocess
import time

from framework.test_fiber import FiberConfigPath
from test_cases.fiber.devnet.compatibility.contract_upgrade_support import (
    CKB,
    ROOT,
    ContractUpgradeSupport,
)

# JSON 状态名（fiber-json-types `ChannelState`）里仍属于“待接受/正在开通”的状态；
# `Closed(FUNDING_ABORTED)` 不算，它代表已失败的旧记录而不是可接受的请求。
IN_PROGRESS_STATES = (
    "NegotiatingFunding",
    "CollaboratingFundingTx",
    "SigningCommitment",
    "AwaitingTxSignatures",
    "AwaitingChannelReady",
)
# 8b95af3 在 feature bit 7 上宣告的完整哈希特性名（fiber-types `feature_bits`）。
FULL_HASH_FEATURE = "ONCHAIN_FULL_PAYMENT_HASH"


class TestFullHashPendingVersion(ContractUpgradeSupport):
    """对端断连后，原待接受请求必须失效；重连后的请求是全新请求。"""

    tmp_path_name = f"report/h32v2-pending-version-{time.time_ns()}"
    ckb_rpc_port, ckb_p2p_port = 25414, 25415
    fiber1_rpc_port, fiber1_p2p_port = 25428, 25427
    fiber2_rpc_port, fiber2_p2p_port = 25429, 25430
    # 只起一个额外对端；跨版本重启沿用同一 data dir 与同一端口，不再申请下一个端口。
    extra_fiber_rpc_port, extra_fiber_p2p_port = 25500, 25600
    # 受害端关闭自动接受：请求必须停在待接受状态由用例显式 accept。
    start_fiber_config = {"fiber_auto_accept_channel_ckb_funding_amount": 0}
    # 只用于继承的链上断言（本文件实际按 args 长度直接断言 Legacy）。
    commitment_version = "legacy"

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
        ):
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", port))
        # 重连后的对端固定为 PR base 9a561b3：它不宣告完整哈希特性，只会协商出 Legacy。
        version = subprocess.check_output(
            [ROOT / FiberConfigPath.V091_DEV.fiber_bin_path, "--version"], text=True
        )
        assert "9a561b3" in version, version
        super().setup_class()
        cls.ckb = cls.node.getClient()
        cls.victim = cls.fiber1
        # 受害端与 CKB 不允许在用例期间重启；对端在额外端口，不在这个基线里。
        cls.processes = cls.node_processes()

    def setUp(self):
        cls = self.__class__
        if not hasattr(cls, "peer"):
            # generate_account / start_new_fiber 是实例方法，extra 节点只能在 setUp 里惰性启动。
            cls.peer = self.start_new_fiber(self.generate_account(5000))

    # ---------------------------------------------------------------- helpers

    def pending_channels(self, fiber, pubkey=None):
        params = {"only_pending": True}
        if pubkey is not None:
            params["pubkey"] = pubkey
        return fiber.get_client().list_channels(params)["channels"]

    def active_channels(self, fiber, pubkey=None, include_closed=False):
        params = {}
        if pubkey is not None:
            params["pubkey"] = pubkey
        if include_closed:
            params["include_closed"] = True
        return fiber.get_client().list_channels(params)["channels"]

    def features_of(self, fiber):
        return fiber.get_client().node_info()["features"]

    def announces_full_hash(self, fiber):
        return any(FULL_HASH_FEATURE in name for name in self.features_of(fiber))

    def wait_peer_visible(self, fiber, pubkey, timeout=60):
        """等 fiber 的 peer session 真的看到 pubkey，避免 open_channel 反复撞
        “waiting for peer to send Init message” 的可重试错误。"""
        deadline = time.monotonic() + timeout
        observed = []
        while time.monotonic() < deadline:
            observed = fiber.get_client().list_peers().get("peers") or []
            if any(peer.get("pubkey") == pubkey for peer in observed):
                return observed
            time.sleep(1)
        self.fail(
            f"{fiber.rpc_port} 未在 {timeout}s 内看到对端 {pubkey} 已连接; 观测={observed}"
        )

    def wait_live_pending(self, fiber, pubkey, timeout=120):
        """等受害端列出该对端的 incoming 待接受请求（仍是正在开通的状态）。"""
        deadline = time.monotonic() + timeout
        observed = []
        while time.monotonic() < deadline:
            observed = self.pending_channels(fiber, pubkey)
            live = [
                c for c in observed if c["state"]["state_name"] in IN_PROGRESS_STATES
            ]
            if live:
                return live[0]
            time.sleep(1)
        self.fail(
            f"受害端未在 {timeout}s 内列出 {pubkey} 的待接受开通请求; 最后观测={observed}"
        )

    def wait_request_invalidated(self, fiber, pubkey, temporary_id, timeout=120):
        """等原请求不再是一个可接受的待接受请求。

        8b95af3 断连时直接删除持久化记录，因此预期是 entry 完全消失；这里同时接受
        “记录还在但状态已不是正在开通”（更晚的源码保留 Failed 记录）作为失效证据。
        """
        deadline = time.monotonic() + timeout
        observed = []
        entry = None
        while time.monotonic() < deadline:
            observed = self.pending_channels(fiber, pubkey)
            entry = next((c for c in observed if c["channel_id"] == temporary_id), None)
            if entry is None:
                return {"result": "removed", "observed": observed}
            if entry["state"]["state_name"] not in IN_PROGRESS_STATES:
                return {"result": "failed", "entry": entry, "observed": observed}
            time.sleep(1)
        self.fail(
            f"断连 {timeout}s 后原请求 {temporary_id} 仍是可接受的待接受请求: "
            f"state={entry['state'] if entry else None}, entry={entry}; 最后观测={observed}"
        )

    def assert_request_no_longer_acceptable(self, fiber, temporary_id):
        """原请求已失效 → accept_channel 必须被拒绝，而不是把新请求/旧请求接进来。"""
        try:
            fiber.get_client().accept_channel(
                {
                    "temporary_channel_id": temporary_id,
                    "funding_amount": hex(100 * CKB),
                }
            )
        except Exception as error:  # noqa: BLE001 - 只要求拒绝，不把措辞当契约
            return str(error)
        self.fail(
            f"原请求 {temporary_id} 已在断连时失效，但 accept_channel 仍然接受了它"
        )

    def restart_peer_without_full_hash(self, peer, pubkey):
        """同一节点身份换二进制重启：身份来自 <data_dir>/fiber/sk，而不是 account key。

        `start_new_fiber` 会给下一个节点分配新的端口与新的 tmp_path（新的随机 sk），
        pubkey 就变了，所以“同一个对端重连”只能用同一 data dir / 同一端口重启。
        8b95af3 的 `ChannelOpenRecord` 比 9a561b3 多一个
        `commitment_contract_version` 字段，为避开跨版本 bincode 反序列化，只清
        fiber/store（对端没有已接受的通道，丢掉它不影响用例），保留 sk 与 ckb/key。
        """
        peer.stop()
        shutil.rmtree(os.path.join(peer.tmp_path, "fiber", "store"), ignore_errors=True)
        peer.fiber_config_enum = FiberConfigPath.V091_DEV
        peer.start(fnn_log_level=self.fnn_log_level)
        restarted_pubkey = peer.get_pubkey()
        assert (
            restarted_pubkey == pubkey
        ), f"重启后对端身份必须不变: before={pubkey} after={restarted_pubkey}"

    def open_request(self, sender, receiver):
        return sender.get_client().open_channel(
            {
                "pubkey": receiver.get_pubkey(),
                "funding_amount": hex(1099 * CKB),
                "public": True,
            }
        )

    def wait_both_ready(self, victim, peer, timeout=60):
        deadline = time.monotonic() + timeout
        states = []
        while time.monotonic() < deadline:
            states = [
                {"rpc_port": f.rpc_port, "state": self.channel(f)["state"]}
                for f in (victim, peer)
            ]
            if all(s["state"]["state_name"] == "ChannelReady" for s in states):
                return states
            time.sleep(1)
        self.fail(f"新请求接受后两端未在 {timeout}s 内 ChannelReady: {states}")

    def record_ready_channel(self, victim, peer):
        """记录强关断言需要的 outpoint 与两端已签名承诺哈希。"""
        self.fibers = [victim, peer]
        channels = [self.channel(f) for f in self.fibers]
        outpoint = bytes.fromhex(channels[0]["channel_outpoint"][2:])
        assert outpoint[32:] == bytes(4), channels
        self.funding_tx = "0x" + outpoint[:32].hex()
        self.signed_hashes = [c["latest_commitment_transaction_hash"] for c in channels]
        return channels

    # ---------------------------------------------------------------- test

    # TEST-MAP: H32V2-03
    # TEST-EVIDENCE-BEGIN: H32V2-03
    # Evidence | partial | 断连后受害端把原 temporary_channel_id 判为失效（8b95af3 下从
    # only_pending 完全消失，且 accept_channel 被拒绝、没有建出通道），重连的同身份对端只能重新发起
    # 一个不同的 temporary_id 并重新 accept，新通道在对端只宣告 Legacy 时协商出 57 字节承诺锁。
    # 这只证明“原请求已失效 + 新请求是全新请求”，**不**证明“版本在接收时被固定并在 accept 时复用”：
    # list_channels 不暴露协商版本，断连清理由参考实现完成，主预期在本仓库不可观测。
    # TEST-EVIDENCE-END: H32V2-03
    def test_pending_request_invalidated_when_peer_reconnects_with_changed_features(
        self,
    ):
        victim, peer = self.victim, self.peer

        # 前提：受害端确实关闭了自动接受，收到请求只会停在待接受。
        auto_accept = victim.get_client().node_info()[
            "auto_accept_channel_ckb_funding_amount"
        ]
        assert int(auto_accept, 16) == 0, auto_accept
        # 受害端支持完整哈希；第一版对端也宣告该特性（否则本用例的“改变特性”不成立）。
        assert self.announces_full_hash(victim), self.features_of(victim)
        peer1_features = self.features_of(peer)
        assert self.announces_full_hash(peer), peer1_features

        peer_pubkey = peer.get_pubkey()
        peer.connect_peer(victim)
        self.wait_peer_visible(victim, peer_pubkey)

        # 步骤 1：第一版对端发出开通请求；受害端只应把它列为待接受，没有任何通道就绪。
        request_1 = self.open_request(peer, victim)
        temporary_id_1 = request_1["temporary_channel_id"]
        pending_1 = self.wait_live_pending(victim, peer_pubkey)
        assert pending_1["channel_id"] == temporary_id_1, (pending_1, temporary_id_1)
        assert pending_1["channel_outpoint"] is None, pending_1
        assert pending_1["state"]["state_name"] in IN_PROGRESS_STATES, pending_1
        ready = [
            c
            for c in self.active_channels(victim, include_closed=True)
            if c["state"]["state_name"] == "ChannelReady"
        ]
        assert ready == [], ready

        # 步骤 2：杀掉第一版对端。先等受害端真的处理完断连再重连：8b95af3 的
        # on_peer_disconnected 带 session_id，如果新 session 先建立，旧的断连事件会被当成
        # stale 忽略，原待接受记录就会残留，所以顺序不能反。
        peer.stop()
        invalidated_before = self.wait_request_invalidated(
            victim, peer_pubkey, temporary_id_1
        )
        assert invalidated_before["result"] in ("removed", "failed"), invalidated_before
        assert not self.active_channels(victim, pubkey=peer_pubkey), invalidated_before
        assert not [
            c
            for c in self.active_channels(victim, include_closed=True)
            if c["state"]["state_name"] == "ChannelReady"
        ], invalidated_before

        # 同一个对端身份，换成只宣告 Legacy 的 0.9.1 二进制重连：pubkey 不变、features 变了。
        self.restart_peer_without_full_hash(peer, peer_pubkey)
        peer2_features = self.features_of(peer)
        assert not self.announces_full_hash(peer), peer2_features

        peer.connect_peer(victim)
        self.wait_peer_visible(victim, peer_pubkey)

        # 重连不能把原请求救回来：仍然不是待接受请求，accept 也被拒绝，且没有通道建出。
        invalidated_after = self.wait_request_invalidated(
            victim, peer_pubkey, temporary_id_1
        )
        assert invalidated_after["result"] in ("removed", "failed"), invalidated_after
        accept_error = self.assert_request_no_longer_acceptable(victim, temporary_id_1)
        assert not self.active_channels(victim, pubkey=peer_pubkey), invalidated_after
        assert not [
            c
            for c in self.active_channels(victim, include_closed=True)
            if c["state"]["state_name"] == "ChannelReady"
        ], invalidated_after

        # 步骤 3：只有重新发起的请求才可接受，而且它是一个全新的 temporary id。
        request_2 = self.open_request(peer, victim)
        temporary_id_2 = request_2["temporary_channel_id"]
        assert temporary_id_2 != temporary_id_1, (temporary_id_1, temporary_id_2)
        pending_2 = self.wait_live_pending(victim, peer_pubkey)
        assert pending_2["channel_id"] == temporary_id_2, (pending_2, temporary_id_2)
        assert pending_2["channel_outpoint"] is None, pending_2
        assert pending_2["state"]["state_name"] in IN_PROGRESS_STATES, pending_2

        existing = {
            c["channel_id"] for c in self.active_channels(victim, pubkey=peer_pubkey)
        }
        victim.get_client().accept_channel(
            {
                "temporary_channel_id": temporary_id_2,
                "funding_amount": hex(100 * CKB),
            }
        )
        self.channel_id = self.wait_for_new_channel_state(
            victim.get_client(), peer_pubkey, "ChannelReady", existing
        )
        self.wait_both_ready(victim, peer)

        # 新请求按对端当前（Legacy-only）宣告重新协商：强关承诺锁必须是 57 字节。
        self.record_ready_channel(victim, peer)
        commitment = self.force_close(victim)
        args = bytes.fromhex(commitment["outputs"][0]["lock"]["args"][2:])
        assert len(args) == 57, (
            f"对端只宣告 Legacy 时新通道的承诺锁应为 57 字节，实测 {len(args)}: "
            f"args={commitment['outputs'][0]['lock']['args']}"
        )
        print(
            "H32V2-03:",
            {
                "temporary_id_1": temporary_id_1,
                "invalidated_before_reconnect": invalidated_before["result"],
                "invalidated_after_reconnect": invalidated_after["result"],
                "accept_error": accept_error,
                "temporary_id_2": temporary_id_2,
                "peer_features_before": peer1_features,
                "peer_features_after": peer2_features,
                "commitment_args_len": len(args),
            },
        )
