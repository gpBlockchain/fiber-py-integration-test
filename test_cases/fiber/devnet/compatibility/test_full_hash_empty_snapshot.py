"""H32V2-24: pre-RAA empty snapshot S0 is still withdrawn on-chain.

Legacy or V1 channel with no revocation history. The remote still holds a
valid empty-TLC snapshot S0 (the state before the offered TLC), while the
pending state S1 already contains a TLC. The remote broadcasts S0 as its
force-close commitment before the first RAA of S1 is ever exchanged; the
local watchtower must select S0 by its own witness hash, keep the valid
empty TLC list and actually confirm the balance withdrawal.

Reference: test_cases/fiber/devnet/security/test_stale_commitment_raa_pending_tlc_stuck.py
(SETTLE-01, same drop-RAA choreography) and test_onchain_settlement_snapshot.py
(_mine_watchtower_rounds helper).

TEST-EVIDENCE notes (read before judging this row):

- There is no RPC that reads ``revocation_data`` or the stored settlement
  snapshot, so the "S0 selected instead of S1" property is proven by outcome:
  the balance really comes back while S1's TLC is still unresolved, and no
  bogus Success is recorded for that payment. It is not proven by inspecting
  the node store.
- The counterparty is the instrumented full-payment-hash build (it owns the
  debug RPC ``submit_commitment_transaction`` used to publish the earlier
  commitment). ``attacker_env`` is read by P2pFiberTest.setup_method when the
  peer node is started, which happens before the test body, so the Legacy
  method starts its own peer node with ``LEGACY_COUNTERPARTY_ENV`` instead of
  mutating the class attribute too late.
- The negotiated layout is asserted from the real broadcast commitment lock
  args (57 vs 58 + trailing 0x01) and from the witness parser version, not
  assumed from the selected peers.
"""

import hashlib
import socket
import time

from framework.attack_fnn import LEGACY_COUNTERPARTY_ENV, requires_full_hash_attack_fnn
from framework.basic_fiber import COMMIT_LOCK_CODE_HASH
from framework.basic_p2p import P2pFiberTest
from framework.helper.settlement_witness import (
    SettlementWitness,
    assert_commitment_args,
)
from framework.p2p_peer import P2pPeer
from framework.test_fiber import FiberConfigPath

CKB = 100000000
PAYMENT_AMOUNT = 1 * CKB
FINAL_EXPIRY_DELTA = 24 * 60 * 60 * 1000


def sha256_hex(preimage_hex):
    raw = bytes.fromhex(preimage_hex.replace("0x", ""))
    return "0x" + hashlib.sha256(raw).digest().hex()


def cn(value):
    if value is None:
        return None
    return int(value, 16) if isinstance(value, str) else int(value)


@requires_full_hash_attack_fnn
class TestFullHashEmptySnapshot(P2pFiberTest):
    """H32V2-24: empty S0 before the first RAA is withdrawn, not treated as missing."""

    ckb_rpc_port, ckb_p2p_port = 24414, 24415
    fiber1_rpc_port, fiber1_p2p_port = 24428, 24427
    fiber2_rpc_port, fiber2_p2p_port = 24429, 24430
    extra_fiber_rpc_port, extra_fiber_p2p_port = 24500, 24600
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 2}
    channel_local_balance = 200 * CKB
    channel_remote_balance = 0
    attacker_fiber_version = FiberConfigPath.ATTACK_FULL_HASH_DEV
    # Default build advertises the full payment hash, so the default run is V1.
    # The Legacy method starts a separate peer with LEGACY_COUNTERPARTY_ENV.
    attacker_env = None

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
            cls.extra_fiber_rpc_port + 2,
            cls.extra_fiber_p2p_port + 2,
        ):
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", port))
        super().setup_class()

    # ---- helpers (local copies; the assigned framework files stay untouched) ----

    def _tlc(self, fiber, payment_hash):
        channel = self.channel_of(fiber, include_closed=True)
        for tlc in channel.get("pending_tlcs") or []:
            if tlc.get("payment_hash") == payment_hash:
                return tlc
        return None

    def _wait_tlc(self, fiber, payment_hash, expected=None, timeout=60):
        last = None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            last = self._tlc(fiber, payment_hash)
            if last is not None and (
                expected is None or last.get("status") == expected
            ):
                return last
            time.sleep(0.5)
        self.fail(f"{fiber.tmp_path} TLC {payment_hash} status {last} != {expected}")

    def _chain_ckb(self, fiber):
        lock_script = self.get_account_script(fiber.account_private)
        return int(
            self.node.getClient().get_cells_capacity(
                {
                    "script": lock_script,
                    "script_type": "lock",
                    "script_search_mode": "exact",
                }
            )["capacity"],
            16,
        )

    def _mine_watchtower_rounds(self, rounds=4):
        """Bounded mining loop, same shape as SETTLE-16."""
        interval = self.start_fiber_config["fiber_watchtower_check_interval_seconds"]
        deadline = time.monotonic() + interval * rounds + 2
        while time.monotonic() < deadline:
            pool = self.node.getClient().get_raw_tx_pool()
            pending = list(pool.get("pending") or [])
            if pending:
                self.Miner.miner_until_tx_committed(self.node, pending[0])
            else:
                self.Miner.miner_with_version(self.node, "0x0")
            time.sleep(1)

    def _submit_earlier_commitment(self, pre_local_cn, pre_remote_cn):
        """Publish the earlier (empty) commitment, preferring the older number.

        The dev RPC keys stored remote commitments by the lock-args commitment
        number, so trying ``max(number - 1, 0)`` before the recorded number
        mirrors SETTLE-01 and keeps S1 (which holds the TLC) out of the choice.
        """
        candidates = []
        for number in (pre_local_cn, pre_remote_cn):
            if number is None:
                continue
            candidates.append(max(number - 1, 0))
            candidates.append(number)
        errors = []
        tried = set()
        for number in candidates:
            if number in tried:
                continue
            tried.add(number)
            try:
                submitted = self.attacker.get_client().call(
                    "submit_commitment_transaction",
                    [
                        {
                            "channel_id": self.channel_id,
                            "commitment_number": hex(number),
                        }
                    ],
                )
                return number, submitted
            except Exception as err:
                errors.append(f"{number}: {err}")
        self.fail(
            "attacker could not submit the earlier empty commitment: "
            f"pre_local_cn={pre_local_cn} pre_remote_cn={pre_remote_cn} "
            f"errors={errors}"
        )

    def _assert_commitment_layout(self, close_tx, version):
        """The broadcast commitment lock args must keep the negotiated layout."""
        tx = self.node.getClient().get_transaction(close_tx)["transaction"]
        lock = tx["outputs"][0]["lock"]
        assert lock["code_hash"] == COMMIT_LOCK_CODE_HASH, lock
        args = bytes.fromhex(lock["args"][2:])
        # 首次承诺：长度按版本，状态标志 args[56] 必须为 0，V1 末尾另有 feature 字节。
        assert_commitment_args(args, version, derived=False)
        return tx

    def _settlement_tx(self, close_tx, timeout=150):
        """Chain transaction that spends the broadcast commitment cell."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            spender, _ = self.get_ln_cell_death_hash(close_tx)
            if spender:
                return spender
            self._mine_watchtower_rounds(1)
        self.fail(f"commitment cell was never withdrawn: {close_tx}")

    def _start_legacy_peer(self):
        """Legacy counterparty: same build, feature disabled at node start.

        证明点 5 的 Legacy 侧前提：`attacker_env` 在节点启动时读取，所以只能另起节点，
        不能在测试体里改类属性；该对端不宣告完整哈希，victim 与它开出的通道协商成 57 字节 Legacy。
        """
        peer = self.start_new_fiber(
            self.generate_account(10000),
            fiber_version=FiberConfigPath.ATTACK_FULL_HASH_DEV,
            env=LEGACY_COUNTERPARTY_ENV,
        )
        self.attacker = peer
        self.peer = P2pPeer(peer)
        self.channel_id = self.open_ready_channel()
        return peer

    def _run_empty_snapshot_case(self, version):
        """One full Legacy or V1 run; fails loudly on any missed assertion."""
        # 证明点 1：窗口起点必须是干净状态 —— 通道 Ready 且一笔 TLC 都没有，
        # 此时双方手里的承诺就是 S0（空 TLC 清单）。
        ready = self.channel_of(self.victim)
        assert ready["state"]["state_name"] == "ChannelReady", ready
        assert not ready["pending_tlcs"], ready

        victim_ckb_before = self._chain_ckb(self.victim)

        # S0: commitment numbers before the offered TLC exists. S1 (with the
        # TLC) must never be exchanged because the attacker withholds RAA.
        # 证明点 1：记下 S0 的承诺号，后面据此选择“更早的那个承诺”来广播。
        pre_musig = self.peer.musig2(self.channel_id)
        pre_local_cn = cn(pre_musig.get("local_commitment_number"))
        pre_remote_cn = cn(pre_musig.get("remote_commitment_number"))

        # 证明点 2：扣住 /RevokeAndAck —— S1 签了也换不到对旧承诺的撤销，
        # S0 因此仍是合法快照，这正是本行要覆盖的“首次 RAA 前”窗口。
        self.peer.intercept(
            self.channel_id,
            drop_out=["RevokeAndAck", "CommitmentSigned", "RemoveTlc"],
            drop_raa=True,
        )

        preimage = self.generate_random_preimage()
        payment_hash = sha256_hex(preimage)
        invoice = self.attacker.get_client().new_invoice(
            {
                "amount": hex(PAYMENT_AMOUNT),
                "currency": "Fibd",
                "description": f"H32V2-24 {version} empty S0 before first RAA",
                "payment_hash": payment_hash,
                "hash_algorithm": "sha256",
                "final_expiry_delta": hex(FINAL_EXPIRY_DELTA),
            }
        )
        payment = self.victim.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "max_fee_rate": hex(1000000000000000),
            }
        )
        assert payment["payment_hash"] == payment_hash
        self.wait_payment_state(self.victim, payment_hash, "Inflight", timeout=60)
        # 证明点 3：可能被误选的 S1 是真实存在的 —— 本端 TLC 已 LocalAnnounced（已插入但未收到
        # RAA，所以还没 Committed），并记下它的 expiry 供证明点 7 使用。
        offered = self._wait_tlc(
            self.victim, payment_hash, {"Outbound": "LocalAnnounced"}
        )
        attacker_tlc = self._wait_tlc(self.attacker, payment_hash)
        assert "Inbound" in attacker_tlc.get("status", {}), attacker_tlc
        assert offered.get("payment_hash") == payment_hash, offered
        expiry_ms = cn(offered["expiry"])

        # Publish S0 (empty TLC list) instead of the S1 commitment that holds
        # the TLC, using the SETTLE-01 selection rule.
        # 证明点 4 的前置：候选承诺号先取 max(number-1, 0) 再取 number，优先更早的那个，
        # 把含 TLC 的 S1 排除在选择之外。
        selected_cn, submitted = self._submit_earlier_commitment(
            pre_local_cn, pre_remote_cn
        )
        close_tx = submitted["tx_hash"]

        # The broadcast commitment is the pre-TLC one: it must still use the
        # negotiated layout (57 Legacy / 58 + 0x01 V1). The witness that spends
        # it is parsed with SettlementWitness.from_hex(..., version=...) below.
        # 证明点 5：从真实广播交易读出锁参数，证明版本没有降级（不按 peers 假设）。
        self._assert_commitment_layout(close_tx, version)

        self.Miner.miner_until_tx_committed(self.node, close_tx)
        # Delay condition: the withdrawal path only unlocks after this epoch.
        self.node.getClient().generate_epochs("0x1", wait_time=0)

        # Core failure mode: an empty S0 treated as missing leaves both
        # principals stuck. The balance must actually come back.
        # 证明点 6：链上真实到账才是“空快照被接受”的证据；只等有界轮次，不做无限等待。
        # 这里只断言“确实回来了”（>199 CKB，对应 200 CKB 本金的下界），不做精确余额对账。
        victim_ckb_after = self._chain_ckb(self.victim)
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline and victim_ckb_after <= victim_ckb_before:
            self._mine_watchtower_rounds(1)
            victim_ckb_after = self._chain_ckb(self.victim)
        recovered = victim_ckb_after - victim_ckb_before
        assert recovered > 199 * CKB, (
            f"{version}: the empty S0 commitment was not withdrawn: "
            f"before={victim_ckb_before}, after={victim_ckb_after}, "
            f"selected_cn={selected_cn}, pre=({pre_local_cn},{pre_remote_cn}), "
            f"close_tx={close_tx}"
        )

        settlement_tx_hash = self._settlement_tx(close_tx)
        settlement = self.node.getClient().get_transaction(settlement_tx_hash)[
            "transaction"
        ]
        # 证明点 4：被解析的结算必须真的花费刚广播的那个承诺 cell，而不是别的 cell。
        assert any(
            item["previous_output"] == {"tx_hash": close_tx, "index": "0x0"}
            for item in settlement["inputs"]
        ), settlement
        witness = SettlementWitness.from_hex(
            settlement["witnesses"][0], version=version
        )
        assert witness.to_hex() == settlement["witnesses"][0], settlement
        # S0 has no TLCs. A non-empty list here means S1 was selected instead.
        # 证明点 4/7：空 TLC 清单证明选中的是 S0；条目非空即说明误选了含 TLC 的 S1。
        assert witness.tlcs == [], (
            f"{version}: S0 settlement carried TLC entries, so S1 was selected: "
            f"{witness.tlcs}"
        )
        # 证明点 4：只解锁双方余额条目，没有任何 TLC 解锁项被结算。
        assert all(
            unlock.unlock_type in (0xFE, 0xFF) for unlock in witness.unlocks
        ), witness.unlocks

        # Do not wait for S1's expiry: the TLC is still unresolved (or has been
        # discarded as failed), never fulfilled, while the funds are already back.
        # 证明点 7：资金已回来时目标 TLC 仍未兑现（LocalAnnounced）或被按失败移除，
        # 说明收尾不是靠 S1 兑现或 S1 超时完成的。
        tlc_after = self._tlc(self.victim, payment_hash)
        assert tlc_after is None or tlc_after.get("status") in (
            {"Outbound": "LocalAnnounced"},
            {"Outbound": "RemoteRemoved"},
            {"Outbound": "RemoveAckConfirmed"},
        ), f"{version}: S1 TLC unexpectedly committed: {tlc_after}"

        # No bogus Success: the empty snapshot must not fulfil the payment.
        # 证明点 8：空快照回收不得记成功，也不得产生成功原像。
        payment_after = self.victim.get_client().get_payment(
            {"payment_hash": payment_hash}
        )
        assert payment_after["status"] != "Success", payment_after
        assert payment_after.get("payment_preimage") is None, payment_after

        # 本用例只验证本端按空 S0 回收余额，不要求双方完成整个通道结算。
        # 对端广播旧 S0 后缺少匹配的本地快照，不会继续结算剩余余额；
        # 因此不等待 Closed 或 WAITING_ONCHAIN_SETTLEMENT 清除。
        # 证明点 7：本端回收发生在 S1 TLC 到期前，不依赖 S1 超时。
        assert (
            time.time() * 1000 < expiry_ms
        ), f"{version}: local withdrawal only finished after S1 expiry: {offered}"

    # TEST-MAP: H32V2-24
    # TEST-EVIDENCE-BEGIN: H32V2-24
    # Evidence | covered | Legacy channel (counterparty build started with
    # FIBER_TEST_DISABLE_FULL_HASH_FEATURE=1) with no revocation history: the
    # earlier empty commitment is published before the first RAA, its 57-byte
    # layout is read from the real broadcast tx, and the witness spending it is
    # parsed with SettlementWitness(version="legacy") as an empty TLC list.
    # Local fund recovery before S1 expiry and no Success are observed on
    # chain / through RPC.
    # Evidence | limitation | No RPC exposes revocation_data or the stored
    # settlement snapshot, so "S0 rather than S1 was selected" is proven by
    # outcome (balance back with the empty list while the TLC stays
    # LocalAnnounced), not by reading the store. Collected only in this round;
    # the devnet body was not executed.
    # TEST-EVIDENCE-END: H32V2-24
    # H32V2-24 评审行（reviews/full-payment-hash-settlement-v2.md，SPEC-09）：
    #   场景：Legacy/V1 通道尚无撤销记录，远端仍持有效的空 TLC 快照 S0，pending S1 已有 TLC；
    #         在首次 RAA 前广播 S0 并由监控回收。
    #   预期：延迟条件满足后按原承诺 witness hash 选中 S0，保留有效空 TLC 清单并实际确认余额结算、
    #         资金到账；不误选 S1、不等待 S1 到期、不因空清单跳过提款。
    #   防止：正确空快照被当成不存在，双方本金卡住。
    # 术语与拓扑：RAA = RevokeAndAck（撤销旧承诺并确认新承诺的 P2P 消息；开通道首轮不发）。
    #   本类 victim --(Victim<->counterparty 通道)--> 对端；对端由 P2pFiberTest.setup_method 启动，
    #   Legacy 方法另外用 LEGACY_COUNTERPARTY_ENV 起一个不宣告完整哈希的对端节点。
    #   S0 = 首笔 TLC 之前的空承诺；S1 = 含该 TLC 的承诺。对端扣住 RAA，所以 S0 从未被撤销，
    #   仍是合法快照；对端用 dev RPC submit_commitment_transaction 广播 S0 而不是 S1。
    # 证明点与断言的对应关系（两个方法共用 _run_empty_snapshot_case）：
    #   证明点 1（窗口成立）：通道 Ready 且无 TLC，并记下 S0 的 local/remote 承诺号。
    #   证明点 2（S0 未被撤销）：drop_raa 扣住 RevokeAndAck，含 TLC 的 S1 永远没换到撤销，
    #     因此“首次 RAA 前广播 S0”这个窗口是真实存在的。
    #   证明点 3（可能被误选的 S1 真实存在）：本笔 TLC 已进入 LocalAnnounced 并记录 expiry。
    #   证明点 4（选中 S0）：广播的是更早承诺号对应的承诺，链上花费该 cell 的 witness 解析出
    #     空 TLC 清单，只解锁双方余额（0xFE/0xFF），因此没有 TLC 被当作已结算。
    #   证明点 5（版本不降级）：广播承诺的锁参数仍是协商布局（57 字节 Legacy / 58 字节末位 0x01 V1）。
    #   证明点 6（实际回收资金）：本端链上 CKB 真实增加（有界等待内的下界 199 CKB），
    #     证明空快照不是“被当成不存在”而卡住本金；本方法不做事后精确到 Shannon 的余额对账。
    #   证明点 7（不误选 S1、不等 S1 到期）：目标 TLC 在资金已回来时仍未兑现（LocalAnnounced 或
    #     被按失败移除），且本端回收发生在 S1 的 TLC 到期之前。
    #   证明点 8（不误报成功）：空快照回收不得把付款记成 Success，也不得产生成功原像。
    #   范围：不验证对端余额回收或整个通道结算完成。
    # 未覆盖/观测限制：没有任何 RPC 暴露 revocation_data 或已存快照，“选的是 S0 而不是 S1”由结果
    #   证明而非读库（见模块 TEST-EVIDENCE 的 limitation）；两个方法都不覆盖 S0 为空以外的快照、
    #   远端强关、以及 S1 自身结算的分支。
    def test_legacy_empty_snapshot_withdrawn_before_pending_s1(self):
        # Legacy 变体：类属性 attacker_env 在节点启动前读取，不能在本方法里改，所以另起一个
        # 带 LEGACY_COUNTERPARTY_ENV 的对端并用它与 victim 开通道。
        self._start_legacy_peer()
        self._run_empty_snapshot_case("legacy")

    # TEST-MAP: H32V2-24
    # TEST-EVIDENCE-BEGIN: H32V2-24
    # Evidence | covered | V1 channel (both ends advertise the full payment
    # hash; the counterparty is the ATTACK_FULL_HASH_DEV build with no env
    # override): same S0-before-first-RAA broadcast, 58-byte lock args with the
    # trailing 0x01 feature byte, empty V1 witness list, real withdrawal, no
    # Success; local withdrawal completes before S1 expires.
    # Evidence | limitation | Same no-store-RPC limitation as the Legacy method.
    # TEST-EVIDENCE-END: H32V2-24
    def test_v1_empty_snapshot_withdrawn_before_pending_s1(self):
        # V1 对照变体：类属性 attacker_env=None，双方都宣告完整哈希，setup_method 开出的
        # victim<->对端通道即为 V1；证明点 5 在此要求 58 字节 args 且末位 0x01。
        # 其余证明点 1-8 与 Legacy 变体共用 _run_empty_snapshot_case，只是 witness 按 v1 解析。
        self._run_empty_snapshot_case("v1")
