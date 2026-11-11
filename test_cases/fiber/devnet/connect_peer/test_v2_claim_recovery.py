"""V2 入站款项：收款方重启后主动强关，核对两笔款、本金和实际费用。"""

import hashlib
import json
import secrets
import time

from framework.basic_fiber import COMMIT_LOCK_CODE_HASH, FiberTest
from framework.config import DEFAULT_MIN_DEPOSIT_CKB
from framework.helper.settlement_witness import (
    SettlementWitness,
    assert_commitment_args,
)
from framework.rpc import RPCClient
from framework.util import ckb_hash
from test_cases.fiber.devnet.compatibility.contract_upgrade_support import (
    commitment_hash_without_deps,
    tlc_is_terminal,
)

CKB = 100_000_000
SAFETY_MS = 9_600_000
PRIVACY_DELTA_MS = 14_400_000


class TestV2ClaimRecovery(FiberTest):
    tmp_path_name = "tmp-v2-claim-recovery"
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 2}
    fnn_log_level = "info"

    # TEST-MAP: RCLAIM-03
    def test_manual_force_close_claims_survive_restart(self):
        """
        1. 建立 V2 CKB 通道，记录本金和钱包，接收两笔先后到期的不同哈希持单。
        2. 付款方离线后各提交一次原像，正常重启收款方，不再次提交原像。
        3. 在最早保护阈值前至少五分钟主动强关一次，不调系统时间。
        4. 核对两笔款、本金及预留容量真实到账、费用正确和两张发票 Paid。
        """
        # 1. fiber1 是收款方；固定一 epoch，两个算法共用已有真实链核款方法。
        self._case_deadline = time.monotonic() + 25 * 60
        self.ckb = RPCClient(self.ckb.url, timeout=10, try_count=1)
        self.ckb.deadline = self._case_deadline
        for fiber in self.fibers:
            fiber.get_client().deadline = self._case_deadline
        self.channel_id = self.open_channel(
            self.fiber1,
            self.fiber2,
            fiber1_balance=500 * CKB,
            fiber2_balance=0,
            udt=None,
            other_config={"commitment_delay_epoch": "0x10000000001"},
        )
        self._check_initial_payments()
        self.wallet_before = self._wallet_assets()
        for index, algorithm in enumerate(("sha256", "ckb_hash")):
            claim = self._hold_payment(
                self.fiber2,
                self.fiber1,
                2 * CKB + 17 + index,
                SAFETY_MS + (600 + 300 * index) * 1000,
                algorithm,
            )
            self.claims[claim["payment_hash"]] = claim
        self._wait_committed_claims()
        expiries = [claim["expiry"] for claim in self.claims.values()]
        assert len(expiries) == 2 and expiries[0] < expiries[1], expiries

        # 2. 停付款方后才结算；重启只使用原目录，不修改原像或通道数据库。
        self.fiber2.stop()
        self._wait("付款方离线", lambda: not self._peer_present(), 30)
        for payment_hash, claim in self.claims.items():
            self.fiber1.get_client().settle_invoice(
                {"payment_hash": payment_hash, "payment_preimage": claim["preimage"]}
            )
        self.fiber1.stop()
        self.fiber1.start(fnn_log_level=self.fnn_log_level, timeout=120)

        # 3. 先排除自动关闭，再走单次公开 RPC；广播和领取共用有界预算。
        assert not self._peer_present(), "付款方意外恢复连接"
        assert self._channel(self.fiber1)["state"]["state_name"] == "ChannelReady"
        self._assert_unspent()
        protection_at = min(expiries) / 1000 - SAFETY_MS / 1000 - 60
        assert time.time() + 300 < protection_at, "已不足五分钟的主动关闭观察余量"
        close_started = time.monotonic()
        self._settlement_deadline = min(self._case_deadline, close_started + 660)
        rpc = self.fiber1.get_client()
        rpc.deadline = self._settlement_deadline
        rpc.shutdown_channel({"channel_id": self.channel_id, "force": True})
        assert time.monotonic() - close_started <= 10, "主动关闭 RPC 超过十秒"
        self.ckb.deadline = min(self._settlement_deadline, close_started + 60)
        commitment = self._wait(
            "主动关闭广播",
            lambda: self._spender(self.funding),
            max(0, self.ckb.deadline - time.monotonic()),
        )
        print("manual force close:", commitment["hash"], flush=True)

        # 4. 沿真实消费链逐笔核款，不以 Closed 或 RPC 成功代替资产到账。
        self.ckb.deadline = self._settlement_deadline
        funds = self._assert_claimed_funds(
            commitment, deadline=self._settlement_deadline
        )
        assert set(funds["claimed_hashes"]) == set(self.claims)
        expected_credit = (
            self.expected[0]
            + DEFAULT_MIN_DEPOSIT_CKB
            + sum(claim["amount"] for claim in self.claims.values())
            - funds["settlement_fees"]
        )
        assert funds["wallet_delta"] == (expected_credit, 0), funds
        for payment_hash in self.claims:
            self.wait_invoice_state(self.fiber1, payment_hash, "Paid", timeout=120)
        assert not self._peer_present(), "领取期间付款方意外恢复连接"
        assert time.monotonic() <= self._settlement_deadline, "链上回款预算已用尽"
        print(
            "manual close claims: 2 paid; wallet credit:", expected_credit, flush=True
        )

    def setup_method(self, method):
        super().setup_method(method)
        self.ckb = self.node.getClient()
        self.pubkeys = [f.get_pubkey() for f in self.fibers]
        self.locks = [self.get_account_script(f.account_private) for f in self.fibers]
        for fiber in self.fibers:
            fiber.get_client().try_count, fiber.get_client().timeout = 1, 10
        self.udt, self.claims = None, {}

    def _check_initial_payments(self):
        """已建通道双向付款并核账，记录故障前的本金、funding 和最后承诺。"""
        self._wait(
            "双方通道 Ready",
            lambda: all(
                self._channel(f)["state"]["state_name"] == "ChannelReady"
                for f in (self.fiber1, self.fiber2)
            ),
        )
        self.expected = [
            int(self._channel(f)["local_balance"], 16)
            for f in (self.fiber1, self.fiber2)
        ]
        for sender_index, amount in ((0, 100 * CKB), (1, CKB)):
            sender, receiver = (self.fiber1, self.fiber2)[sender_index], (
                self.fiber1,
                self.fiber2,
            )[1 - sender_index]
            self._pay_between(sender, receiver, amount, self.udt)
            self.expected[sender_index] -= amount
            self.expected[1 - sender_index] += amount
            last_channels = []

            def balances_match():
                channels = [self._channel(f) for f in (self.fiber1, self.fiber2)]
                last_channels[:] = channels
                return all(
                    c["state"]["state_name"] == "ChannelReady"
                    and int(c["local_balance"], 16) == self.expected[i]
                    and int(c["remote_balance"], 16) == self.expected[1 - i]
                    and int(c["offered_tlc_balance"], 16) == 0
                    and int(c["received_tlc_balance"], 16) == 0
                    and not c["pending_tlcs"]
                    for i, c in enumerate(channels)
                )

            try:
                self._wait("付款账本及 TLC 清空", balances_match)
            except AssertionError as exc:
                # 使用最后一次轮询的原始状态，不在超时后重新请求或追加等待。
                snapshot = {
                    "sender_index": sender_index,
                    "amount": amount,
                    "expected_local_balances": self.expected,
                    "channels": last_channels,
                }
                raise AssertionError(
                    f"{exc}\n付款后账本快照: {json.dumps(snapshot, ensure_ascii=False)}"
                ) from exc
        channel = self._channel(self.fiber1)
        point = bytes.fromhex(channel["channel_outpoint"][2:])
        assert int.from_bytes(point[32:], "little") == 0
        self.funding = self.ckb.get_transaction("0x" + point[:32].hex())["transaction"]
        self.latest_commitment = channel["latest_commitment_transaction_hash"]
        if not self.udt:
            assert (
                int(self.funding["outputs"][0]["capacity"], 16) - sum(self.expected)
                == 2 * DEFAULT_MIN_DEPOSIT_CKB
            )

    def _wait(self, description, observe, timeout=120):
        started = time.monotonic()
        deadlines = {
            "当前等待预算": started + timeout,
            "用例总预算": getattr(self, "_case_deadline", float("inf")),
            "链上回款预算": getattr(self, "_settlement_deadline", float("inf")),
        }
        budget, deadline = min(deadlines.items(), key=lambda item: item[1])
        value = None
        while True:
            now = time.monotonic()
            if now >= deadline:
                raise AssertionError(
                    f"{description}，{budget}已用尽；已等待 {now - started:.3f}s，"
                    f"本次等待上限 {timeout:.1f}s，最后观察值 {value!r}"
                )
            value = observe()
            now = time.monotonic()
            if now > deadline:
                raise AssertionError(
                    f"{description}，查询返回时已超过等待预算（{budget}）；"
                    f"已等待 {now - started:.3f}s，最后观察值 {value!r}"
                )
            if value:
                return value
            time.sleep(min(0.5, max(0, deadline - now)))

    def _channel(self, fiber, channel_id=None):
        channel_id = channel_id or self.channel_id
        rows = fiber.get_client().list_channels({"include_closed": True})["channels"]
        return next(c for c in rows if c["channel_id"] == channel_id)

    def _peer_present(self):
        return any(
            p["pubkey"] == self.pubkeys[1]
            for p in self.fiber1.get_client().list_peers()["peers"]
        )

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

        def payment_succeeded():
            result = sender.get_client().get_payment(
                {"payment_hash": payment["payment_hash"]}
            )
            assert result["status"] != "Failed", result
            return result["status"] == "Success"

        self._wait("探针付款成功", payment_succeeded)
        self._wait(
            "探针发票 Paid",
            lambda: receiver.get_client().get_invoice(
                {"payment_hash": payment["payment_hash"]}
            )["status"]
            == "Paid",
            60,
        )

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

    def _hold_payment(
        self, payer, payee, amount, expiry_delta, algorithm, session_timeout=28_800
    ):
        preimage = "0x" + secrets.token_hex(32)
        payment_hash = (
            "0x" + hashlib.sha256(bytes.fromhex(preimage[2:])).hexdigest()
            if algorithm == "sha256"
            else ckb_hash(preimage)
        )
        invoice = payee.get_client().new_invoice(
            {
                "amount": hex(amount),
                "currency": "Fibd",
                "payment_hash": payment_hash,
                "hash_algorithm": algorithm,
                "expiry": hex(session_timeout),
                "final_expiry_delta": hex(expiry_delta),
                "udt_type_script": self.udt,
            }
        )
        # 正式版会给所有跳共同追加至少 4 小时；收紧预算固定为一档。
        upstream_delta = 0 if payee is self.fiber1 else SAFETY_MS
        expiry_limit = expiry_delta + upstream_delta + PRIVACY_DELTA_MS + 1000
        sent_at_ms = time.time() * 1000
        payment = payer.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "max_parts": "0x1",
                "timeout": hex(session_timeout),
                "final_tlc_expiry_delta": hex(expiry_delta),
                "tlc_expiry_limit": hex(expiry_limit),
            }
        )
        assert payment["payment_hash"] == payment_hash
        self.wait_invoice_state(payee, payment_hash, "Received", timeout=120)
        self.wait_payment_state(payer, payment_hash, "Inflight", timeout=60)
        rows = payee.get_client().list_channels({})["channels"]
        tlc = next(
            t
            for c in rows
            for t in c["pending_tlcs"]
            if t["payment_hash"] == payment_hash
        )
        actual_expiry = int(tlc["expiry"], 16)
        expected_expiry = sent_at_ms + expiry_delta + PRIVACY_DELTA_MS
        assert expected_expiry - 1000 <= actual_expiry <= expected_expiry + 30_000, (
            actual_expiry,
            expected_expiry,
        )
        print(
            "actual final expiry:",
            actual_expiry,
            "privacy delta ms:",
            PRIVACY_DELTA_MS,
            flush=True,
        )
        return {
            "payment_hash": payment_hash,
            "preimage": preimage,
            "amount": amount,
            "expiry": actual_expiry,
        }

    def _pending(self, fiber, channel_id, payment_hash):
        return next(
            (
                t
                for t in self._channel(fiber, channel_id)["pending_tlcs"]
                if t["payment_hash"] == payment_hash
            ),
            None,
        )

    def _wait_committed_claims(self):
        end = time.monotonic() + 60
        while time.monotonic() < end:
            channel = self._channel(self.fiber1)
            assert int(channel["offered_tlc_balance"], 16) == 0
            assert int(channel["received_tlc_balance"], 16) == sum(
                c["amount"] for c in self.claims.values()
            )
            for payment_hash, claim in self.claims.items():
                tlc = self._pending(self.fiber1, self.channel_id, payment_hash)
                assert tlc and int(tlc["amount"], 16) == claim["amount"]
                claim["expiry"] = int(tlc["expiry"], 16)
                assert claim["expiry"] - time.time() * 1000 > SAFETY_MS + 120_000
            time.sleep(1)
        self.latest_commitment = channel["latest_commitment_transaction_hash"]

    def _assert_claimed_funds(self, commitment, deadline=None):
        """沿真实消费链逐笔核对应收款、本金、目的锁及费用，最后对账钱包增量。"""
        deadline = deadline if deadline is not None else time.monotonic() + 660
        self._wait(
            "承诺确认",
            lambda: self.ckb.get_transaction(commitment["hash"])["tx_status"]["status"]
            == "committed",
            max(0, deadline - time.monotonic()),
        )
        packed = self.ckb.get_transaction(commitment["hash"], "0x0")["transaction"]
        assert commitment_hash_without_deps(packed) == self.latest_commitment
        assert_commitment_args(
            bytes.fromhex(commitment["outputs"][0]["lock"]["args"][2:]), "v1"
        )
        # 本夹具承诺交易仅花 funding，无 B 钱包补充输入；费用留在未领取的对手份额。
        assert len(commitment["inputs"]) == len(commitment["outputs"]) == 1
        assert commitment["inputs"][0]["previous_output"] == {
            "tx_hash": self.funding["hash"],
            "index": "0x0",
        }
        commitment_fee = int(self.funding["outputs"][0]["capacity"], 16) - int(
            commitment["outputs"][0]["capacity"], 16
        )
        assert 0 < commitment_fee < 100_000, commitment_fee
        principal = self.expected[0] + (0 if self.udt else DEFAULT_MIN_DEPOSIT_CKB)
        claimed, got_principal, total_fees = set(), False, 0
        total_capacity, total_tokens = 0, 0
        current = commitment
        while not (got_principal and claimed == set(self.claims)):
            assert time.monotonic() < deadline, "链上回款预算已用尽"
            self.ckb.generate_epochs("0x2")
            tx = self._wait(
                "本金及应收款兑现",
                lambda: self._spender(current),
                max(1, deadline - time.monotonic()),
            )
            self._wait(
                "领取交易确认",
                lambda: self.ckb.get_transaction(tx["hash"])["tx_status"]["status"]
                == "committed",
                max(1, deadline - time.monotonic()),
            )
            witness = SettlementWitness.from_hex(tx["witnesses"][0], version="v1")
            released, principal_step = 0, False
            for unlock in witness.unlocks:
                if unlock.unlock_type < 0xFE:
                    tlc = witness.tlcs[unlock.unlock_type]
                    payment_hash = "0x" + tlc.payment_hash.hex()
                    assert payment_hash in self.claims and payment_hash not in claimed
                    claim = self.claims[payment_hash]
                    assert tlc.amount == claim["amount"]
                    assert unlock.preimage == bytes.fromhex(claim["preimage"][2:])
                    released += tlc.amount
                    claimed.add(payment_hash)
                else:
                    assert unlock.unlock_type == 0xFE and not got_principal
                    assert witness.remote_amount == principal, (
                        witness.remote_amount,
                        principal,
                    )
                    released += principal
                    got_principal, principal_step = True, True
            inputs, input_data = [], []
            for item in tx["inputs"]:
                point = item["previous_output"]
                previous = self.ckb.get_transaction(point["tx_hash"])["transaction"]
                index = int(point["index"], 16)
                inputs.append(previous["outputs"][index])
                input_data.append(previous["outputs_data"][index])
            assert tx["inputs"][0]["previous_output"] == {
                "tx_hash": current["hash"],
                "index": "0x0",
            }
            assert all(c["lock"] == self.locks[0] for c in inputs[1:])
            fee = sum(int(c["capacity"], 16) for c in inputs) - sum(
                int(c["capacity"], 16) for c in tx["outputs"]
            )
            assert 0 < fee < 100_000, fee
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
                    + 58
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
                net_tokens = sum(
                    int.from_bytes(bytes.fromhex(d[2:]), "little")
                    for c, d in zip(tx["outputs"], tx["outputs_data"])
                    if c["lock"] == self.locks[0] and c["type"] == self.udt
                )
                net_tokens -= sum(
                    int.from_bytes(bytes.fromhex(d[2:]), "little")
                    for c, d in zip(inputs[1:], input_data[1:])
                    if c["type"] == self.udt
                )
                assert net_tokens == released, (net_tokens, released)
                assert (
                    int.from_bytes(
                        bytes.fromhex(current["outputs_data"][0][2:]), "little"
                    )
                    - int.from_bytes(bytes.fromhex(tx["outputs_data"][0][2:]), "little")
                    == released
                )
            else:
                capacity_release, net_tokens = released, 0
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
            total_capacity += capacity_release
            total_tokens += net_tokens
            total_fees += fee
            print(
                "settlement:",
                tx["hash"],
                "released:",
                released,
                "fee:",
                fee,
                flush=True,
            )
            current = tx
            assert time.monotonic() < deadline
        expected_delta = total_capacity - total_fees, total_tokens
        self._wait(
            "钱包实际到账",
            lambda: tuple(
                a - b for a, b in zip(self._wallet_assets(), self.wallet_before)
            )
            == expected_delta,
            max(1, deadline - time.monotonic()),
        )
        if self.claims:
            self._wait(
                "本方 TLC 已终结",
                lambda: all(
                    tlc_is_terminal(t)
                    for t in self._channel(self.fiber1)["pending_tlcs"]
                ),
                max(1, deadline - time.monotonic()),
            )
        print(
            "wallet delta:",
            expected_delta,
            "claimed hashes:",
            sorted(claimed),
            flush=True,
        )
        return {
            "settlement_fees": total_fees,
            "commitment_fee": commitment_fee,
            "wallet_delta": expected_delta,
            "claimed_hashes": sorted(claimed),
        }
