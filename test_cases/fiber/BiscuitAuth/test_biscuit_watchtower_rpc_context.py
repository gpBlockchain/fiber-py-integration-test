"""PR #1775: authenticated watchtower RPC keeps its normal response."""

import time
from pathlib import Path

import requests

from framework.basic_fiber import FiberTest


BISCUIT_PUBLIC_KEY = (
    "ed25519/17b172749be74276f0ed35a5d0685752684a3c5722114bba447a2f301136db79"
)
BISCUIT_TOKEN = "EpoDCq8CCi5RbWJ2UmpKSEFRRG1qM2NnblVCR1E1elZuR3hVS3diMnFKeWd3TnMyd2s0MWg4CgNjY2gKCGNoYW5uZWxzCghtZXNzYWdlcwoFY2hhaW4KBWdyYXBoCghpbnZvaWNlcwoIcGF5bWVudHMKBXBlZXJzCgp3YXRjaHRvd2VyGAMiCQoHCBgSAxiACCIJCgcIARIDGIEIIgkKBwgAEgMYgQgiCQoHCAESAxiCCCIJCgcIABIDGIIIIgkKBwgBEgMYgwgiCQoHCAESAxiECCIJCgcIABIDGIUIIggKBggAEgIYGCIJCgcIARIDGIYIIgkKBwgAEgMYhggiCQoHCAESAxiHCCIJCgcIABIDGIcIIgkKBwgBEgMYiAgiCQoHCAASAxiICCIJCgcIARIDGIkIEiQIABIgdmX3T0R0Fu6vXZUeVD21AZU7K85xSAmsFAOQSqBd1aYaQNdmA5kqLQ6qNVmOxB0uiEFjUq_OQ0DkZgtYt0mSHktbpubKHzJo-imaFx6Pz7f4NzkOvcLLLFUwBlyHxGyEkQAiIgogAEX-eNhS6VprsYL7moBGaJeVUAnn5VzKLr77Rz9y3AI="


class TestPR1775BiscuitRpcContext(FiberTest):
    # TEST-MAP: F1775-01
    def test_authenticated_watchtower_request_reaches_handler(self):
        """
        1. 使用固定公钥重新启动开启 Biscuit 鉴权的 FNN。
        2. 使用包含 watchtower 写权限和 node id 的有效 token 调用 remove_watch_channel。
        3. 检查原始 JSON-RPC 响应成功，证明请求通过鉴权并到达处理器。
        """
        # 1. 开启 Biscuit 鉴权。
        self.fiber1.stop()
        self.fiber1.start(rpc_biscuit_public_key=BISCUIT_PUBLIC_KEY)

        # 2. 通过真实 JSON-RPC 调用需要 node id 上下文的方法。
        response = requests.post(
            self.fiber1.get_client().url,
            json={
                "jsonrpc": "2.0",
                "id": 1775,
                "method": "remove_watch_channel",
                "params": [{"channel_id": "0x" + "00" * 32}],
            },
            headers={"Authorization": f"Bearer {BISCUIT_TOKEN}"},
            timeout=10,
        )

        # 3. 不将无 error 但缺少 result 的响应误判为成功。
        assert response.status_code == 200
        assert response.json() == {"jsonrpc": "2.0", "id": 1775, "result": None}

    # TEST-MAP: F1775-02
    def test_authenticated_watchtower_request_has_no_old_warn(self):
        """
        1. 开启 Biscuit 鉴权并记录 FNN 日志的当前偏移量。
        2. 用有效 token 调用需要 node id 上下文的 watchtower RPC 并确认成功。
        3. 检查本次请求后的日志没有旧的 fetch WARN。
        """
        # 1. 从新日志片段开始观察，不混入节点启动日志。
        self.fiber1.stop()
        self.fiber1.start(rpc_biscuit_public_key=BISCUIT_PUBLIC_KEY)
        log_path = Path(self.fiber1.tmp_path) / "node.log"
        with log_path.open("rb") as log_file:
            log_file.seek(0, 2)
            log_offset = log_file.tell()

        # 2. 成功响应证明请求经过鉴权并执行 node id 提取。
        response = requests.post(
            self.fiber1.get_client().url,
            json={
                "jsonrpc": "2.0",
                "id": 1775,
                "method": "remove_watch_channel",
                "params": [{"channel_id": "0x" + "00" * 32}],
            },
            headers={"Authorization": f"Bearer {BISCUIT_TOKEN}"},
            timeout=10,
        )
        assert response.status_code == 200
        assert response.json() == {"jsonrpc": "2.0", "id": 1775, "result": None}

        # 3. 只检查该请求之后新增的 WARN，避免旧日志污染断言。
        time.sleep(1)
        with log_path.open("rb") as log_file:
            log_file.seek(log_offset)
            request_logs = log_file.read().decode("utf-8", errors="replace")
        old_warns = [
            line
            for line in request_logs.splitlines()
            if "WARN fnn::rpc::biscuit: fetch " in line
        ]
        assert not old_warns, f"old node-id WARN still emitted: {old_warns}"
