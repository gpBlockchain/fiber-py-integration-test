"""H32V2-13: exact TLC identity for two same-20-byte-prefix hashes on one channel.

The two on-chain layouts under test differ in what actually identifies a TLC:

- V1 (`CURRENT_DEV` peers, 58-byte commitment lock arg ending in `0x01`): each
  97-byte witness TLC entry commits the **full 32-byte** payment hash, so the
  two hashes are distinguishable by the on-chain evidence itself.
- Legacy (`FiberConfigPath.V091_DEV` peer, 57-byte commitment lock arg): each
  85-byte witness TLC entry only commits the **20-byte prefix**, so the pair is
  indistinguishable by hash and identity can only be read off the unlock index
  plus the survival of the sibling.

The full-hash pair is always built as
``hash_a = ckb_hash(preimage)`` (32 bytes) and
``hash_b = hash_a[:20] + <different 12 bytes>``, asserting
``hash_a[:20] == hash_b[:20]`` and ``hash_a != hash_b``. The pre-fold helper in
``test_tlc_payment_hash_prefix_collision.py`` flips only the last hex byte (a
31-byte common prefix) and runs both ends on ``CURRENT_DEV``; this file states
and exercises the real 20-byte collision instead.

Negative control A ("未消费被监控 outpoint 的同前缀交易"): the closest
constructible form is implemented — a raw transaction that spends an unrelated
always-success cell and carries, as its first witness, a byte-copy of the real
sibling settlement witness (same 20-byte-prefix hash). The limitation is
explicit: the transaction's input lock does not reproduce the commitment-lock
search prefix (``commitment_lock.args[0..36]``), which in the watchtower is what
selects a transaction during preimage discovery, and the commitment-lock type
script rejects a synthetic commitment output that the lock itself did not
produce. So this control proves "an unrelated, unwatched transaction does not
change the pending sibling", not "the watchtower indexed it and still ignored
it". Building the indexed form needs a real commitment-lock cell fixture that
this framework only creates by opening another watched channel.

Negative control B ("不匹配快照"): the node persists its on-chain settlement
snapshot inside the channel store and exposes no RPC to read it, inject a
mismatching snapshot, or verify which snapshot was selected. There is therefore
no observation point today; this control is not implemented here rather than
being faked through log scraping.
"""

from dataclasses import replace

import os
import socket
import subprocess
import tempfile
import time

from framework.config import ALWAYS_SUCCESS_CONTRACT_PATH
from framework.helper.settlement_witness import SettlementWitness
from framework.test_fiber import FiberConfigPath
from framework.util import ckb_hash
from test_cases.fiber.devnet.compatibility.contract_upgrade_support import (
    CKB,
    ROOT,
    FullHashChannelSupport,
    tlc_is_terminal,
)

WATCHTOWER_INTERVAL = 2
FAKE_CELL_CAPACITY = 300 * CKB
FAKE_OUTPUT_CAPACITY = 61 * CKB
# devnet's tx-pool min_fee_rate is 1000 shannons/KB, so a multi-hundred-byte
# transaction needs a real fee; 0.01 CKB leaves a wide safety margin.
FAKE_TX_FEE = CKB // 100


def same_prefix_pair():
    """32-byte ckb_hash plus a sibling sharing exactly its first 20 bytes."""
    preimage = "0x" + os.urandom(32).hex()
    hash_a = ckb_hash(preimage)
    digest = bytes.fromhex(hash_a[2:])
    hash_b = "0x" + (digest[:20] + os.urandom(12)).hex()
    assert hash_a[:42] == hash_b[:42], (hash_a, hash_b)
    assert hash_a[42:] != hash_b[42:], (hash_a, hash_b)
    assert hash_a[:20] == hash_b[:20] and hash_a != hash_b
    return preimage, hash_a, hash_b


class TestFullHashExactIdentity(FullHashChannelSupport):
    """Only exact evidence updates a TLC; the same-prefix sibling keeps running."""

    ckb_rpc_port, ckb_p2p_port = 23814, 23815
    fiber1_rpc_port, fiber1_p2p_port = 23828, 23827
    fiber2_rpc_port, fiber2_p2p_port = 23829, 23830
    extra_fiber_rpc_port, extra_fiber_p2p_port = 23900, 24000
    start_fiber_config = {
        "fiber_watchtower_check_interval_seconds": WATCHTOWER_INTERVAL
    }

    @classmethod
    def teardown_class(cls):
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
        ):
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", port))
        assert "9a561b3" in subprocess.check_output(
            [ROOT / FiberConfigPath.V091_DEV.fiber_bin_path, "--version"], text=True
        )
        super().setup_class()
        cls.ckb = cls.node.getClient()
        cls.new1, cls.new2 = cls.fiber1, cls.fiber2

    def setUp(self):
        cls = self.__class__
        if not hasattr(cls, "legacy_peer"):
            # generate_account 是实例方法，旧对端在 setUp 里惰性启动一次。
            cls.legacy_peer = self.start_new_fiber(
                self.generate_account(5000), fiber_version=FiberConfigPath.V091_DEV
            )

    # ---------------------------------------------------------------- helpers

    def hold_invoice(self, payment_hash, description):
        return self.fiber2.get_client().new_invoice(
            {
                "amount": hex(CKB),
                "currency": "Fibd",
                "description": description,
                "payment_hash": payment_hash,
                "hash_algorithm": "ckb_hash",
                "final_expiry_delta": hex(9_600_000),
                "expiry": "0xe10",
            }
        )

    def pay_invoice(self, invoice, payment_hash):
        payment = self.fiber1.get_client().send_payment(
            {"invoice": invoice["invoice_address"], "max_fee_rate": hex(10**15)}
        )
        assert payment["payment_hash"] == payment_hash, payment
        self.wait_payment_state(self.fiber1, payment_hash, "Inflight")
        self.wait_invoice_state(self.fiber2, payment_hash, "Received")

    def tlc_of(self, fiber, payment_hash):
        for tlc in self.channel(fiber)["pending_tlcs"]:
            if tlc["payment_hash"] == payment_hash:
                return tlc
        self.fail(
            f"TLC {payment_hash} 不在端口 {fiber.rpc_port} 的通道里: {self.channel(fiber)}"
        )

    def wait_until(self, predicate, description, timeout=120, interval=1):
        for _ in range(timeout):
            value = predicate()
            if value:
                return value
            time.sleep(interval)
        self.fail(f"等待超时: {description}")

    def wait_for_new_pending_tx(self, previous_tx_hashes, timeout=120):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            pending = self.node.getClient().get_raw_tx_pool()["pending"]
            for tx_hash in pending:
                if tx_hash not in previous_tx_hashes:
                    return tx_hash
            time.sleep(1)
        self.fail(
            "等待新的待打包结算交易超时: previous="
            f"{previous_tx_hashes} now={self.node.getClient().get_raw_tx_pool()['pending']}"
        )

    def watchtower_rounds(self, rounds):
        interval = self.start_fiber_config["fiber_watchtower_check_interval_seconds"]
        deadline = time.monotonic() + interval * rounds + 2
        while time.monotonic() < deadline:
            pool = self.node.getClient().get_raw_tx_pool()["pending"]
            if pool:
                self.Miner.miner_until_tx_committed(self.node, pool[0])
            else:
                self.Miner.miner_with_version(self.node, "0x0")
            time.sleep(1)

    def assert_sibling_survives(self, hash_a, hash_b, description, timeout=420):
        """Wait for A's invoice reconciliation while continuously guarding B."""
        # Remote-close discovery runs every 300 seconds independently of the
        # watchtower. A confirmed claim need not update the invoice immediately.
        # Do not wait for all commitment cells or the settlement flag to clear.
        deadline = time.monotonic() + timeout
        while True:
            sibling = self.tlc_of(self.fiber2, hash_b)
            assert not tlc_is_terminal(
                sibling
            ), f"{description}: B 的 TLC 被误判为终态: {sibling}"
            payment_b = self.fiber1.get_client().get_payment({"payment_hash": hash_b})
            invoice_b = self.fiber2.get_client().get_invoice({"payment_hash": hash_b})
            assert payment_b["status"] == "Inflight", (description, payment_b)
            assert invoice_b["status"] == "Received", (description, invoice_b)
            # A's settlement is its own evidence; B is never satisfied by A's preimage.
            invoice_a = self.fiber2.get_client().get_invoice({"payment_hash": hash_a})
            if invoice_a["status"] == "Paid":
                return
            if time.monotonic() >= deadline:
                break
            time.sleep(min(1, max(0, deadline - time.monotonic())))
        assert invoice_a["status"] == "Paid", {
            "description": description,
            "timeout": timeout,
            "invoice_a": invoice_a,
            "payment_b": payment_b,
            "invoice_b": invoice_b,
            "receiver_channel": self.channel(self.fiber2),
        }

    def hours_past_expiry(self, tlc):
        expiry_ms = int(tlc["expiry"], 16)
        remain_seconds = expiry_ms / 1000.0 - time.time()
        return max(1, int(remain_seconds // 3600) + 2)

    def spend_of(self, tx_hash):
        """Committed spender of a commitment/derived cell, or None while still live."""
        spent_by, _ = self.get_ln_cell_death_hash(tx_hash)
        if not spent_by:
            return None
        result = self.node.getClient().get_transaction(spent_by)
        assert result["tx_status"]["status"] == "committed", result
        return result["transaction"]

    # ------------------------------------------------------- negative control A

    def always_success_lock(self):
        data = ALWAYS_SUCCESS_CONTRACT_PATH.read_bytes()
        return {
            "code_hash": ckb_hash("0x" + data.hex()),
            "hash_type": "data",
            "args": "0x",
        }

    def largest_wallet_cell(self, private_key):
        account = self.Ckb_cli.util_key_info_by_private_key(private_key)
        address = account["address"]["testnet"]
        live_cells = self.Ckb_cli.wallet_get_live_cells(
            address, api_url=self.node.rpcUrl
        ).get("live_cells", [])
        mature = [cell for cell in live_cells if cell.get("mature", True)]
        assert mature, f"没有可用 live cell: {address}"
        return max(
            mature,
            key=lambda cell: int(float(str(cell["capacity"]).split()[0]) * CKB),
        )

    def send_account_tx(self, private_key, outputs, outputs_data):
        fd, tx_file = tempfile.mkstemp(prefix="exact-identity-", suffix=".json")
        os.close(fd)
        try:
            self.Ckb_cli.tx_init(tx_file, self.node.rpcUrl)
            account = self.Ckb_cli.util_key_info_by_private_key(private_key)
            self.Ckb_cli.tx_add_multisig_config(
                account["address"]["testnet"], tx_file, self.node.rpcUrl
            )
            for output, output_data in zip(outputs, outputs_data):
                self.Ckb_cli.tx_add_output(output, output_data, tx_file)
            signatures = self.Ckb_cli.tx_sign_inputs(
                private_key, tx_file, self.node.rpcUrl
            )
            assert signatures
            for signature in signatures:
                self.Ckb_cli.tx_add_signature(
                    signature["lock-arg"],
                    signature["signature"],
                    tx_file,
                    self.node.rpcUrl,
                )
            return self.Ckb_cli.tx_send(tx_file, self.node.rpcUrl).strip()
        finally:
            os.remove(tx_file)

    def submit_unwatched_same_prefix_settlement(self, checkpoint):
        """Spend an always-success cell while carrying a real settlement witness.

        The witness is a byte-copy of the sibling settlement observed on chain,
        so it also names the watched commitment as its previous cell, yet this
        transaction consumes no watched outpoint. See the module docstring for
        what this control does and does not prove.
        """
        budget = FAKE_CELL_CAPACITY + 10 * CKB
        private_key = self.generate_account(budget // CKB)
        account_lock = self.get_account_script(private_key)
        source = self.largest_wallet_cell(private_key)
        source_capacity = int(float(str(source["capacity"]).split()[0]) * CKB)
        assert source_capacity > FAKE_CELL_CAPACITY + CKB, source
        funding_tx = self.send_account_tx(
            private_key,
            [
                {
                    "capacity": hex(FAKE_CELL_CAPACITY),
                    "lock": self.always_success_lock(),
                },
                {
                    "capacity": hex(source_capacity - FAKE_CELL_CAPACITY - FAKE_TX_FEE),
                    "lock": account_lock,
                },
            ],
            ["0x", "0x"],
        )
        self.Miner.miner_until_tx_committed(self.node, funding_tx)
        fake_tx = {
            "version": "0x0",
            "cell_deps": [
                {
                    "out_point": {"tx_hash": funding_tx, "index": "0x0"},
                    "dep_type": "code",
                }
            ],
            "header_deps": [],
            "inputs": [
                {
                    "since": "0x0",
                    "previous_output": {"tx_hash": funding_tx, "index": "0x0"},
                }
            ],
            "outputs": [
                {"capacity": hex(FAKE_OUTPUT_CAPACITY), "lock": account_lock},
                {
                    "capacity": hex(
                        FAKE_CELL_CAPACITY - FAKE_OUTPUT_CAPACITY - FAKE_TX_FEE
                    ),
                    "lock": account_lock,
                },
            ],
            "outputs_data": ["0x", "0x"],
            # First witness only: same pending TLC list and same unlock index as
            # the real sibling settlement, so a prefix-based reader would
            # reconcile B from this unrelated transaction.
            "witnesses": [checkpoint["witnesses"][0]],
        }
        tx_hash = self.node.getClient().send_transaction(fake_tx, "passthrough")
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        committed = self.node.getClient().get_transaction(tx_hash)
        assert committed["tx_status"]["status"] == "committed", committed
        assert committed["transaction"]["witnesses"][0] == checkpoint["witnesses"][0]
        assert not any(
            item["previous_output"] == {"tx_hash": checkpoint["hash"], "index": "0x0"}
            for item in committed["transaction"]["inputs"]
        ), "负对照交易不得消费被监控的承诺 outpoint"
        print("negative control A (unwatched same-prefix tx):", tx_hash)
        return tx_hash

    # ----------------------------------------------------------- shared driver

    def exact_identity_case(self, version):
        """Force close, settle A with the real preimage, then guard B's lifecycle."""
        sender, receiver = (
            (self.new1, self.new2) if version == "v1" else (self.new1, self.legacy_peer)
        )
        self.select_peers(sender, receiver, version)
        self.open_ready()
        preimage, hash_a, hash_b = same_prefix_pair()
        # B (no registered preimage) first, A second: A lands on a non-zero
        # unlock index, so the witness can only be right by exact evidence.
        self.pay_invoice(
            self.hold_invoice(hash_b, "same-prefix sibling without preimage"), hash_b
        )
        self.pay_invoice(
            self.hold_invoice(hash_a, "same-prefix TLC with the real preimage"), hash_a
        )

        def both_committed_and_ordered():
            tlcs = {
                tlc["payment_hash"]: tlc
                for tlc in self.channel(self.fiber2)["pending_tlcs"]
            }
            if hash_a not in tlcs or hash_b not in tlcs:
                return None
            if "Committed" not in tlcs[hash_a]["status"].values():
                return None
            if "Committed" not in tlcs[hash_b]["status"].values():
                return None
            if int(tlcs[hash_a]["id"], 16) <= int(tlcs[hash_b]["id"], 16):
                return None
            return tlcs

        tlcs = self.wait_until(
            both_committed_and_ordered,
            "两笔同前缀 TLC 都 Committed 且可兑现那笔位于非零索引",
            timeout=120,
        )
        assert tlcs[hash_a]["id"] != tlcs[hash_b]["id"], tlcs
        assert int(tlcs[hash_a]["amount"], 16) == int(tlcs[hash_b]["amount"], 16) == CKB
        # Both are inside the same signed commitment, so a single settlement can
        # only be correct by addressing the exact entry.
        assert "Committed" in tlcs[hash_b]["status"].values(), tlcs[hash_b]
        # Force close only after both TLCs are committed, so the published
        # commitment contains both entries and no payment targets a closing channel.
        # Refresh the expected hashes: open_ready cached the empty commitment.
        self.signed_hashes = [
            self.channel(fiber)["latest_commitment_transaction_hash"]
            for fiber in self.fibers
        ]
        # V1 keeps the full 32 bytes on chain; Legacy only the 20-byte prefix.
        commitment = self.force_close(self.fiber1)
        args = self.assert_commitment_layout(commitment)
        assert len(args) == (58 if version == "v1" else 57), (version, args)
        self.ckb.generate_epochs("0x1")

        # Real preimage only: publish P_A for hash_a and follow the committed
        # spend that the watchtower broadcasts for it.
        pending_before = set(self.node.getClient().get_raw_tx_pool()["pending"])
        self.fiber2.get_client().settle_invoice(
            {"payment_hash": hash_a, "payment_preimage": preimage}
        )
        settlement_tx_hash = self.wait_for_new_pending_tx(pending_before)
        # Commit the indexed settlement while the receiver is offline; on
        # restart it must rebuild the exact evidence from persisted channel data.
        self.fiber2.stop()
        self.Miner.miner_until_tx_committed(self.node, settlement_tx_hash)
        self.fiber2.start(fnn_log_level=self.fnn_log_level)
        self.fiber1.connect_peer(self.fiber2)
        self.wait_until(
            lambda: self.channel(self.fiber2)["state"]["state_name"]
            in ("ChannelReady", "Closed", "ShuttingDown"),
            "接收方重启后重新载入通道状态",
            timeout=120,
        )

        checkpoint = self.node.getClient().get_transaction(settlement_tx_hash)
        assert checkpoint["tx_status"]["status"] == "committed", checkpoint
        settlement = checkpoint["transaction"]
        # The settlement really executed the deployed commitment lock.
        code_tx = self.current_contract_code_tx()
        assert {
            "out_point": {"tx_hash": code_tx, "index": "0x0"},
            "dep_type": "code",
        } in settlement["cell_deps"], settlement["cell_deps"]
        assert settlement["inputs"][0]["previous_output"] == {
            "tx_hash": commitment["hash"],
            "index": "0x0",
        }, settlement

        witness = SettlementWitness.from_hex(
            settlement["witnesses"][0], version=self.commitment_version
        )
        assert witness.to_hex() == settlement["witnesses"][0], "witness 必须往返一致"
        assert len(witness.unlocks) == 1, witness.unlocks
        index = witness.unlocks[0].unlock_type
        # Two committed TLCs, B announced first: the fulfillable A is the later
        # entry, so the unlock index must be non-zero. An implementation that
        # takes the first entry whose 20-byte prefix matches would claim B.
        assert len(witness.tlcs) == 2, witness.tlcs
        assert index > 0, (index, witness.tlcs)
        assert index < len(witness.tlcs), (index, witness.tlcs)
        assert witness.unlocks[0].preimage == bytes.fromhex(
            preimage[2:]
        ), witness.unlocks
        hash_length = 20 if version == "legacy" else 32
        expected_claim_hash = bytes.fromhex(ckb_hash(preimage)[2:])[:hash_length]
        assert witness.tlcs[index].payment_hash == expected_claim_hash, witness.tlcs[
            index
        ]
        assert witness.tlcs[index].amount == CKB, witness.tlcs[index]
        # The transaction witness describes the pre-unlock list, including A.
        witness.assert_pending_tlcs([(hash_b, CKB), (hash_a, CKB)])
        # Derive the post-unlock list by index, never by the shared hash prefix.
        # Preserve the raw witness for round-trip and negative-control checks.
        remaining_witness = replace(
            witness,
            tlcs=[tlc for i, tlc in enumerate(witness.tlcs) if i != index],
            unlocks=[],
        )
        remaining_witness.assert_pending_tlcs([(hash_b, CKB)])
        if version == "v1":
            assert [tlc.payment_hash for tlc in remaining_witness.tlcs] == [
                bytes.fromhex(hash_b[2:])
            ], remaining_witness.tlcs
            assert remaining_witness.tlcs[0].payment_hash != expected_claim_hash
        # Derived commitment cell is still live, so B is still claimable.
        assert self.ckb.get_live_cell("0x0", settlement_tx_hash)["status"] == "live"

        # Along the derived chain and across a restart B is never removed.
        self.watchtower_rounds(4)
        self.assert_sibling_survives(hash_a, hash_b, "重启并派生扫描后")

        # B keeps its own lifecycle: fast-forward past its expiry so only the
        # timeout path can resolve it (no preimage exists anywhere for hash_b).
        self.__class__._clock_advanced = True
        sibling = self.tlc_of(self.fiber2, hash_b)
        self.add_time_and_generate_epoch(self.hours_past_expiry(sibling), 1)
        self.watchtower_rounds(6)
        settled = self.spend_of(settlement_tx_hash)
        if settled is not None:
            timeout_witness = SettlementWitness.from_hex(
                settled["witnesses"][0], version=self.commitment_version
            )
            # This spend's witness still contains B before applying its unlocks.
            timeout_witness.assert_pending_tlcs([(hash_b, CKB)])
            assert timeout_witness.unlocks, settled
            for unlock in timeout_witness.unlocks:
                assert unlock.preimage is None, unlock
                assert unlock.unlock_type in (0, 0xFE, 0xFF), unlock
            # An indexed timeout removes B; a balance sweep may retain B in
            # the witness. The final payment/invoice assertions below prove
            # that B's lifecycle actually terminates without a preimage.
        finished = self.wait_payment_finished(self.fiber1, hash_b, timeout=600)
        assert finished["status"] == "Failed", finished
        invoice_b = self.fiber2.get_client().get_invoice({"payment_hash": hash_b})
        # A hold invoice does not leave Received by itself: the node writes Paid
        # only on fulfilment and Cancelled only through an explicit cancel_invoice
        # RPC, while get_invoice maps only Open to Expired. B's termination is
        # therefore observed on the sender as Failed; on the receiver the only
        # identity guarantee is that A's evidence never marks B as Paid.
        assert invoice_b["status"] != "Paid", invoice_b
        # B never became usable from A's evidence: no preimage is recorded.
        assert not finished.get("payment_preimage"), finished
        return settlement, hash_a, hash_b

    # ------------------------------------------------------------------ tests

    # TEST-MAP: H32V2-13
    # TEST-EVIDENCE-BEGIN: H32V2-13
    # Evidence | partial | Covers V1 exact-identity settlement with the real preimage, non-zero witness
    # index, derived-cell liveness, receiver restart, watchtower rounds, sibling timeout with a
    # preimage-free witness, and negative control A in the closest constructible (unwatched, not
    # indexed) form. Not covered: negative control B has no RPC observation point (the persisted
    # settlement snapshot cannot be read, seeded or verified), and the indexed form of control A
    # (unwatched outpoint that still matches the commitment-lock search prefix) needs a
    # commitment-lock cell fixture the framework only creates by opening another watched channel.
    # TEST-EVIDENCE-END: H32V2-13
    # H32V2-13 证明链（V1）：承诺 cell 的 output#0 是派生 cell；结算 tx 的第一 witness 用真实
    # 原像 P 解开非零索引的 TLC；沿该派生 cell 继续消费必须只把该索引对应的 A 视为已结算，
    # 同 20 字节前缀的 B 仍留在待处理清单里，重启后依旧如此。
    # 1) 碰撞是真实的 20 字节：hash_a = ckb_hash(P) 全 32 字节，hash_b = hash_a[:20] + 12 字节，
    #    断言前 20 字节相等、完整 32 字节不等（不是现有用例那种只翻最后一字节的 31 字节前缀）。
    # 2) 非零索引由发送顺序控制：先发不可兑现的 B、再发可兑现的 A，等到 A 的 TLC id 大于 B，
    #    所以解锁索引必然非零；按“前缀撞上的第一个 TLC”处理会选错条目。
    # 3) V1 的链上身份是完整 32 字节：剩余清单里 B 的 32 字节哈希必须逐字节等于 hash_b，且
    #    不等于 A 的哈希；解锁索引选中的条目哈希必须是 P 的 ckb_hash 前 32 字节。
    # 4) 索引精确性：只更新该索引对应的 TLC；B 跨派生扫描与重启都不被移除，付款保持
    #    Inflight、发票保持 Received。
    # 5) 派生活跃：结算后派生 commitment cell 仍 live，B 仍可被后续交易结算。
    # 6) 重启判据：结算在接收方离线时上链，重启后由持久化的精确证据重建判断，B 不被误移除。
    # 7) 自生命周期：越过 B 的过期时间后只有超时路径能终结它，付款 Failed 且无原像，超时结算
    #    witness 的解锁没有原像（preimage is None）。接收方的 hold 发票不会因超时自动终结
    #    （只有兑现或显式 cancel_invoice 才离开 Received），故只要求它始终不为 Paid。
    # 8) 负对照 A：提交一笔消费无关 always-success cell、却携带同前缀“结算 witness”副本的交易，
    #    它不消费被监控 outpoint，B 的状态不得改变（局限见模块 docstring）。
    # 9) 负对照 B：节点持久化快照没有 RPC 观测点，不做日志抓取式伪造，明确记为未实现。
    def test_v1_prefix_pair_keeps_exact_identity(self):
        settlement, hash_a, hash_b = self.exact_identity_case("v1")
        self.submit_unwatched_same_prefix_settlement(settlement)
        self.watchtower_rounds(3)
        self.assert_sibling_survives(hash_a, hash_b, "负对照 A 上链后")

    # TEST-MAP: H32V2-13
    # TEST-EVIDENCE-BEGIN: H32V2-13
    # Evidence | partial | Legacy run of the same flow: 57-byte commitment lock, 85-byte witness TLC
    # entries, real preimage settlement at a non-zero index, live derived cell, sibling survival
    # across watchtower rounds and a receiver restart, and sibling timeout with a preimage-free
    # witness. Not covered: Legacy `payment_hash` is only the 20-byte prefix, so the witness itself
    # cannot distinguish A from B and the identity judgement is unlock index + sibling survival
    # rather than a full-hash comparison; negative control A is V1-only and B is not implemented for
    # the reasons stated above.
    # TEST-EVIDENCE-END: H32V2-13
    # H32V2-13 证明链（Legacy）：旧节点只把 20 字节前缀写进 85 字节 witness 条目，同前缀两笔
    # TLC 在链上无法用哈希区分；此时身份只能由解锁索引 + 另一笔仍然存活共同证明。
    # 1) 真实碰撞对与 V1 相同：hash_a 全 32 字节、hash_b 与其前 20 字节相同。
    # 2) A 在非零索引：先发 B 再发 A，等到 A 的 TLC id 大于 B；witness 解锁索引必须非零，且
    #    选中条目的前缀等于 P_A 的 ckb_hash 前 20 字节。
    # 3) 剩余清单仍含同前缀 B（assert_pending_tlcs 按 20 字节比较）。
    # 4) 派生 commitment cell 仍 live；跨 watchtower 轮次与接收方重启后 B 的 TLC 未被移除，
    #    付款保持 Inflight、发票保持 Received —— 另一笔仍走自己的生命周期。
    # 5) 与 V1 相同的超时收尾：越过 B 的过期时间后付款 Failed 且无原像，接收方 hold 发票不为
    #    Paid（不会自动 Cancelled/Expired），超时结算 witness 的解锁没有原像。
    def test_legacy_prefix_pair_keeps_exact_identity(self):
        self.exact_identity_case("legacy")
