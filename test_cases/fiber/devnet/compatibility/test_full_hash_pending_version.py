"""H32V2-03: a disconnected V1 pending request is invalidated.

The valid-pending feature-change branch remains unobservable through the public RPC:
normal disconnect clears the original request before the peer can reconnect with
changed features. One p2p-tap FNN advertises V1 initially, then restarts with
its test-only feature switch off. This proves invalidation, not feature pinning.
"""

import os
import shutil
import socket
import subprocess
import time

from framework.config import DEFAULT_MIN_LEDGER_DEPOSIT_CKB
from framework.attack_fnn import LEGACY_COUNTERPARTY_ENV, requires_attack_fnn
from framework.test_fiber import FiberConfigPath
from test_cases.fiber.devnet.compatibility.contract_upgrade_support import (
    CKB,
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
# Fiber feature bit 7 的完整哈希特性名（fiber-types `feature_bits`）。
FULL_HASH_FEATURE = "ONCHAIN_FULL_PAYMENT_HASH"


@requires_attack_fnn
class TestFullHashPendingVersion(ContractUpgradeSupport):
    """对端断连后，原待接受请求必须失效；重连后的请求是全新请求。"""

    ckb_rpc_port, ckb_p2p_port = 25414, 25415
    fiber1_rpc_port, fiber1_p2p_port = 25428, 25427
    fiber2_rpc_port, fiber2_p2p_port = 25429, 25430
    # 只起一个额外对端；跨版本重启沿用同一 data dir 与同一端口，不再申请下一个端口。
    extra_fiber_rpc_port, extra_fiber_p2p_port = 25500, 25600
    # 受害端关闭自动接受：请求必须停在待接受状态由用例显式 accept。
    start_fiber_config = {"fiber_auto_accept_channel_ckb_funding_amount": 0}

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
        super().setup_class()
        cls.ckb = cls.node.getClient()
        cls.victim = cls.fiber1
        # 受害端与 CKB 不允许在用例期间重启；对端在额外端口，不在这个基线里。
        cls.processes = cls.node_processes()

    def setUp(self):
        cls = self.__class__
        if not hasattr(cls, "peer"):
            # generate_account / start_new_fiber 是实例方法，extra 节点只能在 setUp 里惰性启动。
            cls.peer = self.start_new_fiber(
                self.generate_account(5000),
                fiber_version=FiberConfigPath.ATTACK_FULL_HASH_DEV,
            )

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

        当前节点断连时通常删除持久化记录，因此预期是 entry 完全消失；这里同时接受
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
        """已停止的同一节点用测试开关重启：身份来自 <data_dir>/fiber/sk。

        `start_new_fiber` 会给下一个节点分配新的端口与新的 tmp_path（新的随机 sk），
        pubkey 就变了，所以“同一个对端重连”只能用同一 data dir / 同一端口重启。
        重启时仅对这个对端设置 p2p-tap 测试开关，不切换二进制。
        清理尚未接受的请求记录，保留 sk 与 ckb/key，避免重启恢复旧 outgoing 请求。
        """
        shutil.rmtree(os.path.join(peer.tmp_path, "fiber", "store"), ignore_errors=True)
        peer.extra_env = dict(LEGACY_COUNTERPARTY_ENV)
        peer.start(fnn_log_level=self.fnn_log_level)
        restarted_pubkey = peer.get_pubkey()
        assert (
            restarted_pubkey == pubkey
        ), f"重启后对端身份必须不变: before={pubkey} after={restarted_pubkey}"

    def open_request(self, sender, receiver):
        return sender.get_client().open_channel(
            {
                "pubkey": receiver.get_pubkey(),
                "funding_amount": hex(1000 * CKB + DEFAULT_MIN_LEDGER_DEPOSIT_CKB),
                "public": True,
            }
        )

    # ---------------------------------------------------------------- test

    # TEST-MAP: H32V2-03
    # TEST-EVIDENCE-BEGIN: H32V2-03
    # Evidence | partial | V1 request is pending before disconnect; after the peer's identity
    # reconnects without the feature, the original temporary id remains absent and accept rejects
    # it. Feature pinning of a still-live request is not observable via public RPC.
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

        # 步骤 2：停止对端。先等受害端真的处理完断连再重连：on_peer_disconnected 的
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

        # 同一个 p2p-tap 二进制用测试开关关闭完整哈希特性后重连：pubkey 不变、features 变了。
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

        # This old peer no longer advertises the feature. A *new* request would be rejected
        # under the new admission policy (H32V2-34); it is not used to prove this original id.
        print(
            "H32V2-03:",
            {
                "temporary_id_1": temporary_id_1,
                "invalidated_before_reconnect": invalidated_before["result"],
                "invalidated_after_reconnect": invalidated_after["result"],
                "accept_error": accept_error,
                "peer_features_before": peer1_features,
                "peer_features_after": peer2_features,
            },
        )
