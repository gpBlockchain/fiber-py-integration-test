"""H32-05: persisted V1 survives honest-node/watchtower restart and peer reconnect."""

import hashlib
import secrets
import socket
import subprocess
import time

from test_cases.fiber.devnet.compatibility.contract_upgrade_support import (
    CKB,
    NEW_CONTRACT,
    ContractUpgradeSupport,
)


class TestFullHashPersistence(ContractUpgradeSupport):
    tmp_path_name = f"report/h32-persistence-{time.time_ns()}"
    ckb_rpc_port, ckb_p2p_port = 20814, 20815
    fiber1_rpc_port, fiber1_p2p_port = 20828, 20827
    fiber2_rpc_port, fiber2_p2p_port = 20829, 20830
    extra_fiber_rpc_port, extra_fiber_p2p_port = 20900, 21000
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 2}
    commitment_version = "v1"

    @classmethod
    def setup_class(cls):
        for port in (20814, 20815, 20828, 20827, 20829, 20830, 20900, 21000):
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", port))
        super().setup_class()
        cls.receiver = cls.fiber2
        cls.ckb = cls.node.getClient()
        cls.processes = None

    def setUp(self):
        cls = self.__class__
        if not hasattr(cls, "peer"):
            # 对端用框架默认版本（CURRENT_DEV）；双方都支持完整哈希才会协商出 V1。
            cls.peer = self.start_new_fiber(self.generate_account(5000))
            cls.processes = cls.node_processes()
        self.fiber1, self.fiber2 = cls.peer, cls.receiver
        self.fibers = [self.fiber1, self.fiber2]
        self.account1, self.account2 = (
            self.fiber1.get_account(),
            self.fiber2.get_account(),
        )

    @classmethod
    def node_processes(cls):
        records = super().node_processes()
        pid = subprocess.check_output(
            ["lsof", "-nP", "-t", "-iTCP:20900", "-sTCP:LISTEN"], text=True
        ).strip()
        started = subprocess.check_output(
            ["ps", "-p", pid, "-o", "lstart="], text=True
        ).strip()
        return records + [(pid, started)]

    # TEST-MAP: H32V2-04
    # TEST-EVIDENCE-BEGIN: H32V2-04
    # Evidence | covered | Restart process identity changes and nonparticipants unchanged; all persisted
    # fields/TLC unchanged; original raw hash except deps matches broadcast, 58/97 preserved, chain payout
    # and payment Success plus Closed.
    # TEST-EVIDENCE-END: H32V2-04
    # H32-05 证明链：V1 通道有待结算 TLC，本端及内置 Watchtower 用原库重启，对端重连后广播重启前
    # 保存的原承诺。
    # 1) 待结算输入：真实开通 V1 通道（本金按 V1 的 100 CKB 预留计算），并保留一笔两端都已
    #    Committed 的 hold TLC；记录两端 latest_commitment_transaction_hash 作为重启前基线。
    # 2) 双方原库重启：fiber2（诚实接收方 + 内置 Watchtower，检查间隔 2s）与原库重启，不是新建节点；
    #    对端也从自己的原库重启后重连，两侧的通道状态与签名承诺都必须来自持久化。
    # 3) 不降级、不重新协商：重启后两端 latest_commitment_transaction_hash 与基线完全相等；强关广播
    #    的 raw transaction 去掉 deps 后哈希保持一致，链上验证有效；不声称签名 witness 字节未变化。
    # 4) 仍为 V1：强关承诺锁 args 恰为 58 字节且末字节 0x01（Legacy 为 57）→ 已存通道未降为 Legacy。
    # 5) 重启后正确解析原条目：公布重启前的原像，assert_tlc_settlement 以 v1 解析 97 字节条目并核对
    #    金额与收款人到账，assert_settled 核对双方余额与总矿工费，付款 Success 且持有正确原像，
    #    节点最终正常关闭。
    # 6) 进程证据：CKB 与未参与本次重启的 fiber1 保持不变，本端与对端的 PID/启动时间都变化 →
    #    是真重启而不是同进程续跑。
    def test_stored_v1_settles_after_node_and_watchtower_restart(self):
        # 默认快照已部署升级后的 commitment-lock，直接用当前代码 cell。
        code_tx = self.current_contract_code_tx()
        deployed = self.ckb.get_transaction(code_tx)["transaction"]["outputs_data"][0]
        assert (
            deployed == "0x" + NEW_CONTRACT.read_bytes().hex()
        ), "Deployed contract differs from tested artifact"
        self.channel_id = self.open_channel(self.fiber1, self.fiber2, 1000 * CKB, 0)
        # 真实 V1 通道：commitment_version = "v1"，本金按 100 CKB 预留计算。
        channels = [self.channel(f) for f in self.fibers]
        outpoint = bytes.fromhex(channels[0]["channel_outpoint"][2:])
        assert outpoint[32:] == bytes(4)
        self.funding_tx = "0x" + outpoint[:32].hex()
        self.principals = [int(c["local_balance"], 16) + 100 * CKB for c in channels]
        self.wallet_before = self.wallet_balances()
        preimage = "0x" + secrets.token_hex(32)
        payment_hash = "0x" + hashlib.sha256(bytes.fromhex(preimage[2:])).hexdigest()
        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(CKB),
                "currency": "Fibd",
                "payment_hash": payment_hash,
                "hash_algorithm": "sha256",
                "final_expiry_delta": hex(9_600_000),
            }
        )
        self.fiber1.get_client().send_payment({"invoice": invoice["invoice_address"]})
        self.wait_invoice_state(self.fiber2, payment_hash, "Received")
        # 待结算输入：两端都 Committed 的 hold TLC；同时记下重启前的承诺哈希基线。
        for _ in range(60):
            channels = [self.channel(f) for f in self.fibers]
            if all(
                len(c["pending_tlcs"]) == 1
                and "Committed" in c["pending_tlcs"][0]["status"].values()
                for c in channels
            ):
                break
            time.sleep(1)
        else:
            self.fail(f"Hold TLC not committed on both ends: {channels}")
        # 合并版复用原用例：显式保留双方原通道与余额，不只比较承诺哈希。
        persisted_fields = (
            "channel_id",
            "channel_outpoint",
            "local_balance",
            "remote_balance",
            "latest_commitment_transaction_hash",
        )
        before_restart = [{key: c[key] for key in persisted_fields} for c in channels]
        for channel in channels:
            tlc = channel["pending_tlcs"][0]
            assert tlc["payment_hash"] == payment_hash, channel
            assert int(tlc["amount"], 16) == CKB, channel
        self.signed_hashes = [c["latest_commitment_transaction_hash"] for c in channels]
        old_processes = self.processes
        # Restart the honest process and its built-in watchtower from their original DB.
        # 本端用原库重启（不是新建节点），内置 Watchtower 随进程一起重启。
        self.fiber2.stop()
        self.fiber2.start(fnn_log_level=self.fnn_log_level)
        # 对端同样用原库重启后重连：承诺与通道状态必须来自持久化，而不是新建。
        self.peer.stop()
        self.peer.start(fnn_log_level=self.fnn_log_level)
        self.peer.connect_peer(self.fiber2)
        # 索引 0/1 是 CKB 与未参与本次重启的 fiber1；索引 2 是本端、索引 3 是对端，二者必须真重启。
        processes = self.node_processes()
        assert processes[:2] == old_processes[:2]
        assert processes[2] != old_processes[2] and processes[3] != old_processes[3]
        self.__class__.processes = processes
        # 重启后仍是重启前那份承诺：持久化的 V1 状态没有被重连改写。
        restored = [self.channel(f) for f in self.fibers]
        assert [
            {key: c[key] for key in persisted_fields} for c in restored
        ] == before_restart, restored
        for channel in restored:
            assert len(channel["pending_tlcs"]) == 1, channel
            tlc = channel["pending_tlcs"][0]
            assert tlc["payment_hash"] == payment_hash, channel
            assert int(tlc["amount"], 16) == CKB, channel
            assert "Committed" in tlc["status"].values(), channel
        # force_close compares the stored pre-restart commitment hash (excluding only deps).
        commitment = self.force_close(self.peer)
        args = bytes.fromhex(commitment["outputs"][0]["lock"]["args"][2:])
        # 58 字节 + 末字节 0x01 → 已存通道仍为 V1，没有被降级。
        assert len(args) == 58 and args[-1] == 1
        self.ckb.generate_epochs("0x1")
        # 用重启前的原像结算原 TLC：assert_tlc_settlement 以 v1 解析 97 字节条目、核对原像与到账。
        self.fiber2.get_client().settle_invoice(
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
        self.wait_payment_state(self.peer, payment_hash, "Success", timeout=660)
        assert (
            self.peer.get_client().get_payment({"payment_hash": payment_hash})[
                "payment_preimage"
            ]
            == preimage
        )
        self.assert_local_closed()
