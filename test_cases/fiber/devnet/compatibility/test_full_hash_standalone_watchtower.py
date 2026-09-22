"""H32-12: observe real watchtower RPC registration, reload it, then spend on chain."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import socket
import subprocess
import threading
import time

import requests

from framework.config import (
    DEFAULT_MIN_DEPOSIT_CKB,
    DEFAULT_MIN_LEDGER_DEPOSIT_CKB,
)
from framework.test_fiber import FiberConfigPath
from framework.util import ckb_hash
from test_cases.fiber.devnet.compatibility.contract_upgrade_support import (
    CKB,
    ROOT,
    ContractUpgradeSupport,
)


class WatchtowerRpcRecorder(BaseHTTPRequestHandler):
    """Transparent localhost recorder: forward original bytes/headers, never alter fields."""

    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        try:
            response = requests.post(
                "http://127.0.0.1:21300",
                data=raw,
                headers={
                    k: v
                    for k, v in self.headers.items()
                    if k.lower() not in ("host", "connection")
                },
                timeout=10,
            )
            request, result = json.loads(raw), response.json()
            self.server.records.append((request, result))
            self.send_response(response.status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response.content)))
            self.end_headers()
            self.wfile.write(response.content)
        except requests.RequestException:
            self.send_error(502)

    def log_message(self, *_args):
        pass  # Channel/private settlement keys stay in memory, not HTTP access logs.


class TestFullHashStandaloneWatchtower(ContractUpgradeSupport):
    tmp_path_name = f"report/h32-standalone-watchtower-{time.time_ns()}"
    ckb_rpc_port, ckb_p2p_port = 21214, 21215
    fiber1_rpc_port, fiber1_p2p_port = 21228, 21227
    fiber2_rpc_port, fiber2_p2p_port = 21229, 21230
    extra_fiber_rpc_port, extra_fiber_p2p_port = 21300, 21400
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 2}
    shared_fiber2_extra_config = {
        "fiber_disable_built_in_watchtower": "true",
        "fiber_standalone_watchtower_rpc_url": "http://127.0.0.1:21500",
    }

    @classmethod
    def setup_class(cls):
        for port in (
            21214,
            21215,
            21228,
            21227,
            21229,
            21230,
            21300,
            21301,
            21400,
            21401,
            21500,
        ):
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", port))
        # 旧节点二进制必须固定（独立 Watchtower 的 Legacy 注册对照组依赖它）。
        assert "9a561b3" in subprocess.check_output(
            [ROOT / FiberConfigPath.V091_DEV.fiber_bin_path, "--version"], text=True
        )
        cls.recorder = ThreadingHTTPServer(("127.0.0.1", 21500), WatchtowerRpcRecorder)
        cls.recorder.records = []
        cls.recorder_thread = threading.Thread(
            target=cls.recorder.serve_forever, daemon=True
        )
        cls.recorder_thread.start()
        try:
            super().setup_class()
        except BaseException:
            cls.recorder.shutdown()
            cls.recorder.server_close()
            raise
        cls.sender, cls.new_receiver = cls.fiber1, cls.fiber2
        cls.ckb = cls.node.getClient()
        cls.processes = None

    def setUp(self):
        cls = self.__class__
        if not hasattr(cls, "tower"):
            cls.tower = self.start_new_fiber(self.generate_account(5000))
            cls.old_receiver = self.start_new_fiber(
                self.generate_account(5000),
                fiber_version=FiberConfigPath.V091_DEV,
                config=dict(
                    cls.shared_fiber2_extra_config, ckb_rpc_url=cls.node.rpcUrl
                ),
            )
            cls.processes = cls.node_processes()

    @classmethod
    def node_processes(cls):
        records = super().node_processes()
        for port in (21300, 21301):
            pid = subprocess.check_output(
                ["lsof", "-nP", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"], text=True
            ).strip()
            started = subprocess.check_output(
                ["ps", "-p", pid, "-o", "lstart="], text=True
            ).strip()
            records.append((pid, started))
        return records

    def channel_calls(self, method):
        return [
            (r, result)
            for r, result in self.recorder.records
            if r["method"] == method
            and r["params"][0].get("channel_id") == self.channel_id
        ]

    # TEST-MAP: H32V2-12
    # TEST-EVIDENCE-BEGIN: H32V2-12
    # Evidence | partial | Version propagation, persisted registration (no re-register after the tower
    # restarts alone) and the chain settlement it performs are covered, and the sender is now required to
    # record Success with the published preimage and a terminal target TLC. Still missing: no second,
    # simultaneously pending control channel during the restart, and only one hold TLC per layout (no
    # derived-cell follow-up settlement).
    # TEST-EVIDENCE-END: H32V2-12
    # H32-12 证明链：节点把通道注册给支持 V1 的独立 Watchtower 并由对端强关；另以省略版本字段的
    # 旧请求注册 Legacy 通道，验证跨 RPC 的版本传递与旧调用方兼容。
    # 1) 观察点：21500 的本地透明 RPC 记录器原样转发到 21300 的独立 Watchtower，记录每次
    #    (请求, 响应)；被观察节点的内置 Watchtower 关闭，链上结算必须由独立服务完成。
    # 2) 版本传递：V1 组合要求 create_watch_channel 的 params 带 `commitment_contract_version == "V1"`；
    #    Legacy 组合用固定旧节点 9a561b3，要求该字段“不出现” → 新节点不会给旧调用方加字段，
    #    省略字段仍按 Legacy 结算。
    # 3) 快照经 RPC 送达：真实付款产生一笔已承诺 TLC，直到记录器里出现
    #    update_pending_remote_settlement / update_revocation 且其 settlement_data.tlcs 含本笔
    #    payment_hash，才开始强关 → 独立服务拿到的是 RPC 传递的快照，不是本端内存模拟。
    # 4) 持久化而非重复注册：只重启独立服务（其余进程 PID/启动时间不变），随后强关并结算；
    #    结束时 create_watch_channel 的调用次数必须与重启前相同 → 版本与注册信息来自持久化，
    #    重启后没有重新注册。
    # 5) 链上结果：强关承诺锁 V1 为 58 字节 + 末位 0x01、Legacy 为 57 字节；结算交易含升级合约
    #    code dep，assert_tlc_settlement / assert_settled 按对应版本核对原像、金额与到账，到账方
    #    是独立服务的钱包（account2 = tower），因为它代被观察节点完成结算。
    def test_v1_registration_and_omitted_legacy_version_settle_from_reloaded_store(
        self,
    ):
        # 默认快照已部署升级后的 commitment-lock，直接用当前代码 cell。
        code_tx = self.current_contract_code_tx()
        # 同一用例跑两次：新接收方（双方都支持完整哈希 ⇒ V1）与固定旧接收方 9a561b3（Legacy）。
        for receiver, version in (
            (self.new_receiver, "v1"),
            (self.old_receiver, "legacy"),
        ):
            self.fiber1, self.fiber2 = self.sender, receiver
            self.fibers = [self.sender, receiver]
            self.commitment_version = version
            self.channel_id = self.open_channel(self.fiber1, receiver, 1000 * CKB, 0)
            channels = [self.channel(f) for f in self.fibers]
            raw = bytes.fromhex(channels[0]["channel_outpoint"][2:])
            assert raw[32:] == bytes(4)
            self.funding_tx = "0x" + raw[:32].hex()
            reserve = (
                DEFAULT_MIN_DEPOSIT_CKB
                if version == "v1"
                else DEFAULT_MIN_LEDGER_DEPOSIT_CKB
            )
            self.principals = [int(c["local_balance"], 16) + reserve for c in channels]
            # Standalone watchtower's signer receives the watched party's payout.
            # Track the sender + tower wallets; the watched node's built-in service is disabled.
            self.account1, self.account2 = (
                self.sender.get_account(),
                self.tower.get_account(),
            )
            self.wallet_before = self.wallet_balances()
            # 等独立服务真的收到注册请求，且响应无错误。
            for _ in range(30):
                calls = self.channel_calls("create_watch_channel")
                if calls:
                    break
                time.sleep(1)
            else:
                self.fail(
                    "Node did not register its real channel with the standalone service"
                )
            assert all("error" not in result for _, result in calls), calls
            params = calls[-1][0]["params"][0]
            # 跨 RPC 的版本传递：V1 必须显式带字段；旧调用方必须看不到这个字段（省略 ⇒ Legacy）。
            if version == "v1":
                assert params["commitment_contract_version"] == "V1"
            else:
                assert (
                    "commitment_contract_version" not in params
                ), "old RPC compatibility must really omit the field"

            # 真实付款产生一笔 hold TLC；快照必须经 RPC 到达独立服务后才继续。
            preimage = "0x" + secrets.token_hex(32)
            payment_hash = ckb_hash(preimage)
            invoice = receiver.get_client().new_invoice(
                {
                    "amount": hex(CKB),
                    "currency": "Fibd",
                    "payment_hash": payment_hash,
                    "hash_algorithm": "ckb_hash",
                    "final_expiry_delta": hex(9_600_000),
                }
            )
            self.sender.get_client().send_payment(
                {"invoice": invoice["invoice_address"]}
            )
            self.wait_invoice_state(receiver, payment_hash, "Received")
            # 记录器里出现带本笔 payment_hash 的 settlement_data，且两端都已 Committed 才算送达。
            for _ in range(60):
                channels = [self.channel(f) for f in self.fibers]
                updated = self.channel_calls(
                    "update_pending_remote_settlement"
                ) + self.channel_calls("update_revocation")
                delivered = any(
                    "error" not in result
                    and any(
                        t["payment_hash"] == payment_hash
                        for t in request["params"][0]["settlement_data"]["tlcs"]
                    )
                    for request, result in updated
                )
                if delivered and all(
                    len(c["pending_tlcs"]) == 1
                    and "Committed" in c["pending_tlcs"][0]["status"].values()
                    for c in channels
                ):
                    break
                time.sleep(1)
            else:
                self.fail(
                    "Committed TLC snapshot did not reach standalone watchtower via RPC"
                )
            self.signed_hashes = [
                c["latest_commitment_transaction_hash"] for c in channels
            ]
            # 记录重启前的注册次数与进程基线：注册信息应当来自持久化。
            create_count = len(self.channel_calls("create_watch_channel"))
            before = self.processes
            self.tower.stop()
            self.tower.start(fnn_log_level=self.fnn_log_level)
            after = self.node_processes()
            # 只重启独立服务：索引 3 是 tower，其 PID/启动时间必须变化，其余进程保持不变。
            assert after[:3] == before[:3] and after[4:] == before[4:]
            assert after[3] != before[3]
            self.__class__.processes = after
            # 由发送方（对端）强关，承诺锁布局按版本核对。
            commitment = self.force_close(self.sender)
            args = bytes.fromhex(commitment["outputs"][0]["lock"]["args"][2:])
            assert len(args) == (58 if version == "v1" else 57)
            if version == "v1":
                assert args[-1] == 1
            self.ckb.generate_epochs("0x1")
            # 公布原像后由独立服务完成链上结算；到账方是本用例的 account2 = tower 钱包。
            receiver.get_client().settle_invoice(
                {"payment_hash": payment_hash, "payment_preimage": preimage}
            )
            spent = self.wait_for_spend(commitment["hash"])
            self.assert_tlc_settlement(
                commitment, spent, code_tx, [(payment_hash, CKB)], preimage
            )
            self.principals[0] -= CKB
            self.principals[1] += CKB
            self.assert_settled(
                spent, code_tx, self.get_tx_message(commitment["hash"])["fee"]
            )
            # SPEC-08 还要求付款结果与目标 TLC 终态：独立服务完成链上结算后，本端必须记录 Success
            # 且持有本次公布的原像，目标 TLC 收尾，而不是只有 RPC 调用成功。
            self.wait_payment_state(self.sender, payment_hash, "Success", timeout=120)
            assert (
                self.sender.get_client().get_payment({"payment_hash": payment_hash})[
                    "payment_preimage"
                ]
                == preimage
            )
            self.wait_tlc_terminal(self.sender, payment_hash)
            # 重启后没有再次 create_watch_channel → 用的是持久化里的版本，而非重新注册。
            assert (
                len(self.channel_calls("create_watch_channel")) == create_count
            ), "must reload the stored version, not re-register it"
            print(
                "Standalone persisted registration and chain settlement:",
                version,
                self.channel_id,
            )

    @classmethod
    def teardown_class(cls):
        try:
            super().teardown_class()
        finally:
            cls.recorder.shutdown()
            cls.recorder.server_close()
            cls.recorder_thread.join(timeout=5)
