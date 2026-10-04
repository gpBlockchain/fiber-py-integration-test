"""H32V2-07/14/15/16/17/18: prefix-only preimage claims on a Legacy/V1 channel.

Merged from ``test_full_hash_invalid_preimage.py``,
``test_full_hash_bad_preimage_contract.py`` and
``test_full_hash_weak_evidence.py`` (H32V2 group A merge).

Topology
--------
Honest victim (``CURRENT_DEV``) -- counterparty (``ATTACK_FULL_HASH_DEV``). When
the method selects ``LEGACY_COUNTERPARTY_ENV`` the counterparty does not
advertise the full-hash feature, so the channel negotiates the Legacy commitment
layout (57-byte args / 85-byte TLC entries) even though the honest node supports
the feature. The counterparty creates a hold invoice whose 32-byte payment hash
shares only its 20-byte prefix with ``sha256(preimage)``, force-closes, stores
that preimage under that hash (``create_preimage force=true``) and its
watchtower broadcasts the prefix-only settlement.

Rows
----
- H32V2-07: an invoice whose ``payment_hash`` is the digest of a known preimage
  with only the trailing bytes changed (identical 20-byte prefix, wrong full
  32-byte hash) is paid on a real channel; afterwards the counterparty's own
  watchtower submits the prefix-only claim. Legacy must keep accepting it
  (57-byte commitment args, 85-byte TLC entry / 20-byte hash witness), while V1
  must reject it: the commitment cell stays live, no payout reaches the
  counterparty account, and the local payment is neither fulfilled nor failed
  early (normal timeout path preserved).
- H32V2-14: a confirmed prefix-only on-chain claim fails the exact TLC of the
  direct payer before its expiry; no preimage is exposed as success and the
  already-spent on-chain funds are explicitly NOT recovered.
- H32V2-15: the same confirmed claim on the downstream leg of a payer -> victim
  (router) -> counterparty route; the relay propagates the failure to the
  original payer before the earliest recorded expiry and must not charge the
  upstream channel for the downstream on-chain loss.
- H32V2-16: the victim's outgoing Legacy channel enters on-chain close
  reconciliation and the counterparty's confirmed prefix-only claim consumes the
  exact outgoing TLC; the victim must close the matching *received* TLC from the
  payer as a failed consumption (no preimage published) while an unrelated
  received TLC on the same channel keeps its state.
- H32V2-17: on one Legacy channel, hold a same-20-byte-prefix pair where only
  sibling A has the real, full-hash-correct preimage and sibling B has none.
  Settle A on chain through the receiver's watchtower, then require that (a) A's
  target becomes Success with the correct preimage and (b) B is neither
  fulfilled nor failed while bounded watchtower rounds run. A separate exact,
  no-preimage offered TLC is the expiry control: it stays Inflight before the
  expiry and becomes Failed only after the chain clock passes the TLC expiry
  (SETTLE-04 pattern).
- H32V2-18: after the confirmed claim, both a restart before the failure
  notification and repeated scans while alive must replay the same confirmed
  spend without re-counting the already-spent funds and without resolving an
  unrelated still-pending TLC.

Counterparty wiring
-------------------
``P2pFiberTest`` starts the counterparty from the class attribute ``attacker_env``
before the test body runs, so the per-method environment is selected in
``setup_method`` from the running method name:

* Legacy methods disable the full-hash feature -> the channel negotiates Legacy;
* the V1 method additionally lets the counterparty build a V1 settlement whose
  preimage does not match the full 32-byte hash (``V1_PREFIX_CLAIM_ENV``), which
  is the only way the contract's rejection can actually be exercised.

The claim always comes from the counterparty node itself: ``create_preimage``
with ``force=true`` plus its built-in watchtower. No commitment argument or
witness is edited by hand, so the "other signatures and inputs stay valid"
property of H32V2-07 is preserved.

Not implemented here, and why (H32V2-17)
----------------------------------------
The remaining H32V2-17 branches are "only an old prefix-keyed record with no
preimage / a bad preimage / a full-hash-correct preimage", "exact identity or
algorithm mismatch" and "only a locally-known preimage". They all require
reading the watchtower's persisted preimage store: no Fiber RPC exposes it today
(there is no query for stored preimages/records, only ``create_preimage`` to
insert one), and log scraping is explicitly not accepted as proof. Those
branches therefore have no observation point in this harness and are not
implemented; only the observable parts are: the same-prefix sibling survival and
the exact no-preimage expiry control.

Reused choreography: the former ``test_legacy_invalid_preimage.py`` (direct
payer, relay, restart-before-notification) with its deleted fixtures replaced by
the adapted counterparty build, plus the bounded fault-injection loop and the
"failed before earliest expiry" assertion from
``security/test_stale_commitment_raa_pending_tlc_stuck.py``.
"""

import hashlib
import secrets
import socket
import time

import pytest

from framework.attack_fnn import (
    LEGACY_COUNTERPARTY_ENV,
    V1_PREFIX_CLAIM_ENV,
    requires_full_hash_attack_fnn,
)
from framework.basic_p2p import P2pFiberTest
from framework.helper.settlement_witness import SettlementWitness
from framework.test_fiber import FiberConfigPath
from framework.util import ckb_hash
from test_cases.fiber.devnet.compatibility.contract_upgrade_support import (
    CKB,
    commitment_hash_without_deps,
    tlc_is_terminal,
)

PAYMENT_AMOUNT = 1 * CKB
UNRELATED_AMOUNT = 2 * CKB
CHANNEL_FUND = 200 * CKB
# 160 minutes is the smallest final expiry delta both node builds accept.
FINAL_EXPIRY_DELTA = 9_600_000
# Routed / explicit two-hop payments need room for the intermediate hop's delta.
# The H32V2-14/15/18 choreography used 86_400_000 under the name
# FINAL_EXPIRY_DELTA; the H32V2-16 relay used the same value as
# ROUTED_FINAL_EXPIRY_DELTA. Both are the same constant here.
ROUTED_FINAL_EXPIRY_DELTA = 86_400_000
# One day keeps every hold invoice valid for the whole test method.
HOLD_INVOICE_EXPIRY = hex(86_400)
WATCHTOWER_INTERVAL = 2
CLOSE_TIMEOUT = 180
V1_REJECT_ROUNDS = 4
SIBLING_ROUNDS = 8
LEGACY_COMMITMENT_ARGS_LENGTH = 57
REMOVED_STATUSES = {
    "LocalRemoved",
    "RemoteRemoved",
    "RemoveWaitPrevAck",
    "RemoveWaitAck",
    "RemoveAckConfirmed",
}


def sha256_hex(preimage_hex):
    raw = bytes.fromhex(preimage_hex.removeprefix("0x"))
    return "0x" + hashlib.sha256(raw).hexdigest()


def craft_prefix_only_hash(preimage_hex, algorithm):
    """H = digest(P) with every trailing byte flipped: same 20-byte prefix only."""
    digest = (
        ckb_hash(preimage_hex) if algorithm == "ckb_hash" else sha256_hex(preimage_hex)
    )
    raw = bytes.fromhex(digest.removeprefix("0x"))
    crafted = raw[:20] + bytes(byte ^ 1 for byte in raw[20:])
    assert crafted[:20] == raw[:20], (crafted.hex(), raw.hex())
    assert crafted != raw, crafted.hex()
    return "0x" + crafted.hex()


def same_twenty_byte_prefix_hash(payment_hash):
    """Flip the last byte so only the 20-byte prefix is shared with ``payment_hash``."""
    raw = bytes.fromhex(payment_hash.removeprefix("0x"))
    changed = raw[:-1] + bytes((raw[-1] ^ 1,))
    assert changed != raw and changed[:20] == raw[:20], (changed.hex(), raw.hex())
    return "0x" + changed.hex()


@requires_full_hash_attack_fnn
class TestFullHashBadPreimage(P2pFiberTest):
    """Prefix-only preimage claims: Legacy vs V1, relay, restart and expiry."""

    attacker_fiber_version = FiberConfigPath.ATTACK_FULL_HASH_DEV
    # Default counterparty build hides the full-hash feature; setup_method selects
    # the per-method environment before the node starts.
    attacker_env = LEGACY_COUNTERPARTY_ENV
    start_fiber_config = {
        "fiber_watchtower_check_interval_seconds": WATCHTOWER_INTERVAL
    }
    ckb_rpc_port, ckb_p2p_port = 24214, 24215
    fiber1_rpc_port, fiber1_p2p_port = 24228, 24227
    fiber2_rpc_port, fiber2_p2p_port = 24229, 24230
    # extra_fiber_rpc_port/p2p_port and their +1 are consumed by the counterparty
    # (started by P2pFiberTest) and the extra payer of the relay cases
    # (start_new_fiber uses extra + len(new_fibers)).
    extra_fiber_rpc_port, extra_fiber_p2p_port = 24300, 24400
    channel_local_balance = CHANNEL_FUND
    channel_remote_balance = 0

    # The counterparty environment is read once, before the node starts, so the
    # class attribute cannot vary per method. Select it from the method name.
    _COUNTERPARTY_ENV = {
        "test_legacy_prefix_only_claim_accepted_each_algorithm_and_direction": (
            LEGACY_COUNTERPARTY_ENV
        ),
        "test_v1_prefix_only_claim_rejected_each_algorithm_and_direction": (
            V1_PREFIX_CLAIM_ENV
        ),
        "test_direct_payer_fails_confirmed_prefix_only_claim": (
            LEGACY_COUNTERPARTY_ENV
        ),
        "test_relay_fails_upstream_before_earliest_expiry": LEGACY_COUNTERPARTY_ENV,
        "test_relay_received_tlc_fails_after_confirmed_prefix_claim": (
            LEGACY_COUNTERPARTY_ENV
        ),
        "test_same_prefix_sibling_survives_confirmed_claim": LEGACY_COUNTERPARTY_ENV,
        "test_exact_no_preimage_offered_tlc_waits_for_expiry": LEGACY_COUNTERPARTY_ENV,
        "test_restart_before_failure_notification_rescans_confirmed_claim": (
            LEGACY_COUNTERPARTY_ENV
        ),
        "test_repeated_scan_while_alive_keeps_unrelated_tlc": (LEGACY_COUNTERPARTY_ENV),
    }

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
        # 本类只用 head 基线的对抗对端，不依赖 V091 二进制。
        super().setup_class()
        cls.ckb = cls.node.getClient()

    @classmethod
    def teardown_class(cls):
        try:
            if getattr(cls, "_clock_advanced", False):
                cls.restore_time()
        finally:
            super().teardown_class()

    def teardown_method(self, method):
        super().teardown_method(method)
        # 时间跳跃只在设置它的方法内生效：方法结束即恢复，避免同类其他方法在跑偏的时钟上执行。
        if getattr(self.__class__, "_clock_advanced", False):
            self.restore_time()
            self.__class__._clock_advanced = False

    def setup_method(self, method):
        self.attacker_env = self._COUNTERPARTY_ENV.get(method.__name__)
        super().setup_method(method)
        # P2pFiberTest retires the spare stock node; alias the live counterparty
        # so the two-node helpers keep pointing at a running node.
        self.fiber2 = self.attacker

    # ------------------------------------------------------------------ chain

    def _channel(self, fiber, channel_id=None):
        wanted = channel_id or self.channel_id
        channels = fiber.get_client().list_channels({"include_closed": True})[
            "channels"
        ]
        for channel in channels:
            if channel["channel_id"] == wanted:
                return channel
        raise AssertionError(
            f"{fiber.rpc_port} has no channel {wanted}: "
            f"{[c['channel_id'] for c in channels]}"
        )

    def _channel_by_id(self, fiber, channel_id):
        for channel in fiber.get_client().list_channels({"include_closed": True})[
            "channels"
        ]:
            if channel["channel_id"] == channel_id:
                return channel
        self.fail(f"channel {channel_id} not found on {fiber.tmp_path}")

    @staticmethod
    def _channel_outpoint(channel_outpoint_hex):
        raw = bytes.fromhex(channel_outpoint_hex.removeprefix("0x"))
        assert len(raw) == 36, channel_outpoint_hex
        return {
            "tx_hash": "0x" + raw[:32].hex(),
            "index": hex(int.from_bytes(raw[32:], "little")),
        }

    def _funding_outpoint(self, fiber):
        raw = bytes.fromhex(self._channel(fiber)["channel_outpoint"].removeprefix("0x"))
        assert len(raw) == 36, raw.hex()
        return {
            "tx_hash": "0x" + raw[:32].hex(),
            "index": hex(int.from_bytes(raw[32:], "little")),
        }

    def _spender_of(self, tx_hash, outpoint=None):
        """Confirmed transaction that spends ``<tx_hash>:0``, or None."""
        outpoint = outpoint or {"tx_hash": tx_hash, "index": "0x0"}
        spent_by, _ = self.get_ln_cell_death_hash(tx_hash)
        if not spent_by:
            return None
        result = self.node.getClient().get_transaction(spent_by)
        if result["tx_status"]["status"] != "committed":
            return None
        tx = result["transaction"]
        if not any(item["previous_output"] == outpoint for item in tx["inputs"]):
            return None
        return tx

    def _wait_spender(self, tx_hash, description, timeout=CLOSE_TIMEOUT):
        deadline = time.time() + timeout
        while time.time() < deadline:
            spent = self._spender_of(tx_hash)
            if spent is not None:
                return spent
            time.sleep(1)
        self.fail(f"等待 {description} 超时: {tx_hash}:0 未被已确认交易消费")

    def _packed_transaction(self, tx_hash):
        """去掉 deps 后重算承诺哈希需要 Molecule 打包字节，不是普通调用返回的解码 dict。

        `get_transaction(hash, "0x0")` 才返回原始 hex；`_spender_of` 用的是无 verbosity
        的调用（返回 {"cell_deps": ...} 形式的 dict），直接传给
        `commitment_hash_without_deps` 会在 `packed[2:]` 上抛 KeyError。
        """
        packed = self.node.getClient().get_transaction(tx_hash, "0x0")["transaction"]
        assert isinstance(packed, str), f"需要打包 hex，实际拿到 {type(packed)}"
        return packed

    def _force_close(self, closer):
        """Broadcast the closer's latest signed commitment and return the chain tx."""
        recorded = self._channel(closer)["latest_commitment_transaction_hash"]
        funding = self._funding_outpoint(closer)
        closer.get_client().shutdown_channel(
            {"channel_id": self.channel_id, "force": True}
        )
        commitment = self._wait_spender(
            funding["tx_hash"], f"{closer.rpc_port} 的强关承诺"
        )
        onchain_hash = commitment_hash_without_deps(
            self._packed_transaction(commitment["hash"])
        )
        assert onchain_hash == recorded, (
            "链上强关交易去掉 deps 后必须等于该端自己签名的承诺，否则条目方向断言不成立: "
            f"onchain={onchain_hash} stored={recorded}"
        )
        return commitment

    def _force_close_attacker(self):
        channel = self.channel_of(self.attacker)
        funding = self._channel_outpoint(channel["channel_outpoint"])
        self.rpc_shutdown(self.attacker, force=True)
        commitment = self._wait_spend(funding, label="funding outpoint")
        args = bytes.fromhex(commitment["outputs"][0]["lock"]["args"][2:])
        assert len(args) == LEGACY_COMMITMENT_ARGS_LENGTH, (
            "LEGACY_COUNTERPARTY_ENV must negotiate the Legacy commitment layout "
            f"({LEGACY_COMMITMENT_ARGS_LENGTH}-byte args); got {len(args)}: "
            f"{commitment['outputs'][0]['lock']}"
        )
        return commitment

    def _wait_spend(self, outpoint, label, timeout=180):
        """Return the committed transaction that spends ``outpoint``.

        Checks the tx pool first (so a settlement can be mined explicitly even
        while the automatic miner is stopped) and then the committed index.
        """
        spent = self.ckb.get_transaction(outpoint["tx_hash"])["transaction"]
        lock = spent["outputs"][int(outpoint["index"], 16)]["lock"]
        search_key = {
            "script": lock,
            "script_type": "lock",
            "script_search_mode": "exact",
        }
        deadline = time.monotonic() + timeout
        candidates = None
        while time.monotonic() < deadline:
            pool = self.ckb.get_raw_tx_pool()
            for view in ("pending", "proposed"):
                for tx_hash in list(pool.get(view) or []):
                    result = self.ckb.get_transaction(tx_hash)
                    if result["tx_status"]["status"] not in ("pending", "proposed"):
                        continue
                    if any(
                        item["previous_output"] == outpoint
                        for item in result["transaction"]["inputs"]
                    ):
                        self.Miner.miner_until_tx_committed(self.node, tx_hash)
                        return self.ckb.get_transaction(tx_hash)["transaction"]
            candidates = self.ckb.get_transactions(search_key, "asc", "0xff", None)[
                "objects"
            ]
            for item in candidates:
                if item["tx_hash"] == outpoint["tx_hash"]:
                    continue
                result = self.ckb.get_transaction(item["tx_hash"])
                if result["tx_status"]["status"] != "committed":
                    continue
                if any(
                    entry["previous_output"] == outpoint
                    for entry in result["transaction"]["inputs"]
                ):
                    return result["transaction"]
            time.sleep(0.5)
        self.fail(
            f"{label} {outpoint} was not spent within {timeout}s; candidates={candidates}"
        )

    def _chain_median_time(self):
        tip = self.ckb.get_tip_header()
        return int(self.ckb.get_block_median_time(tip["hash"]), 16)

    def _mine_watchtower_rounds(self, rounds=1):
        """Give the built-in watchtower bounded review rounds and confirm pending txs."""
        deadline = time.time() + WATCHTOWER_INTERVAL * rounds + 2
        while time.time() < deadline:
            pending = list(self.node.getClient().get_raw_tx_pool()["pending"])
            for tx_hash in pending:
                self.Miner.miner_until_tx_committed(self.node, tx_hash)
            if not pending:
                self.Miner.miner_with_version(self.node, "0x0")
            time.sleep(1)

    def _mine_watchtower_rounds_from_config(self, rounds=4):
        # Former test_full_hash_invalid_preimage.py helper: its interval comes from
        # start_fiber_config and it mines at most one pending transaction per round,
        # so it is kept separate from _mine_watchtower_rounds.
        interval = self.start_fiber_config["fiber_watchtower_check_interval_seconds"]
        deadline = time.monotonic() + interval * rounds + 2
        while time.monotonic() < deadline:
            pool = self.ckb.get_raw_tx_pool()
            pending = list(pool.get("pending") or [])
            if pending:
                self.Miner.miner_until_tx_committed(self.node, pending[0])
            else:
                self.Miner.miner_with_version(self.node, "0x0")
            time.sleep(1)

    def _fiber_wallet_capacity(self, fiber):
        # Former test_full_hash_invalid_preimage.py _wallet_capacity(fiber).
        result = self.ckb.get_cells_capacity(
            {
                "script": self.get_account_script(fiber.account_private),
                "script_type": "lock",
                "script_search_mode": "exact",
            }
        )
        return int(result["capacity"], 16)

    def _lock_arg_wallet_capacity(self, lock_arg):
        # Former test_full_hash_bad_preimage_contract.py _wallet_capacity(lock_arg).
        lock = {
            "code_hash": self.Config.CKB_DEFAULT_CONFIG[
                "ckb_block_assembler_code_hash"
            ],
            "hash_type": "type",
            "args": lock_arg,
        }
        result = self.node.getClient().get_cells_capacity(
            {"script": lock, "script_type": "lock", "script_search_mode": "exact"}
        )
        return int(result["capacity"], 16)

    # ------------------------------------------------------------------- tlcs

    def _tlcs(self, fiber, payment_hash, channel_id=None):
        channel = self._channel(fiber, channel_id)
        return [
            tlc
            for tlc in channel.get("pending_tlcs") or []
            if tlc["payment_hash"] == payment_hash
        ]

    def _tlc_in(self, fiber, channel_id, payment_hash):
        return [
            tlc
            for tlc in self._channel_by_id(fiber, channel_id).get("pending_tlcs") or []
            if tlc.get("payment_hash") == payment_hash
        ]

    def _tlc_status(self, fiber, payment_hash, side, channel_id=None):
        for tlc in self._tlcs(fiber, payment_hash, channel_id):
            if side in tlc["status"]:
                return tlc["status"][side]
        return None

    def _assert_not_removed(self, fiber, payment_hash, side, channel_id=None):
        for tlc in self._tlcs(fiber, payment_hash, channel_id):
            if side not in tlc["status"]:
                continue
            status = tlc["status"][side]
            assert not tlc_is_terminal(
                tlc
            ), f"{side} TLC {payment_hash} 不应被处理: {tlc}"
            assert (
                status not in REMOVED_STATUSES
            ), f"{side} TLC {payment_hash} 不应被处理: status={status}"
            return status
        self.fail(f"{side} TLC {payment_hash} 从 {fiber.rpc_port} 的通道消失")

    def _wait_tlc_removed(
        self, fiber, payment_hash, side, channel_id=None, timeout=CLOSE_TIMEOUT
    ):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = self._tlc_status(fiber, payment_hash, side, channel_id)
            if last in REMOVED_STATUSES or last is None:
                return last
            time.sleep(1)
        self.fail(
            f"{fiber.rpc_port} 上 {payment_hash} 的 {side} TLC 未按消费/失败收尾: {last}"
        )

    def _wait_tlc_removed_after_expiry(
        self, fiber, payment_hash, side, channel_id=None, timeout=CLOSE_TIMEOUT
    ):
        # Former test_full_hash_weak_evidence.py _wait_tlc_removed: it also accepts a
        # terminal record, so it is kept separate from _wait_tlc_removed.
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            matches = [
                tlc
                for tlc in self._tlcs(fiber, payment_hash, channel_id)
                if side in tlc["status"]
            ]
            if not matches:
                return None
            last = matches[0]["status"][side]
            if all(
                tlc_is_terminal(tlc) or tlc["status"][side] in REMOVED_STATUSES
                for tlc in matches
            ):
                return last
            time.sleep(1)
        self.fail(
            f"{fiber.rpc_port} 上 {payment_hash} 的 {side} TLC 未按消费/超时收尾: {last}"
        )

    def _wait_committed_tlc(self, fiber, payment_hash, channel_id=None, timeout=120):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            for tlc in self._tlcs(fiber, payment_hash, channel_id):
                last = tlc
                if "Committed" in tlc["status"].values():
                    return tlc
            time.sleep(1)
        self.fail(f"{fiber.rpc_port} 上 {payment_hash} 的 TLC 未进入 Committed: {last}")

    def _wait_tlc_committed(self, fiber, channel_id, payment_hash, timeout=150):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            last = self._tlc_in(fiber, channel_id, payment_hash)
            if len(last) == 1 and "Committed" in last[0]["status"].values():
                return last[0]
            time.sleep(0.5)
        self.fail(
            f"TLC {payment_hash} not Committed in {channel_id} on "
            f"{fiber.tmp_path}: {last}"
        )

    def _wait_target_tlc_terminal(self, fiber, payment_hash, timeout=120):
        last = None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            last = self._tlc_in(fiber, self.channel_id, payment_hash)
            if all(tlc_is_terminal(tlc) for tlc in last):
                return last
            time.sleep(0.5)
        self.fail(f"target TLC {payment_hash} not terminal on {fiber.tmp_path}: {last}")

    def _wait_channel_ready(self, fiber, channel_id, timeout=120):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            try:
                last = self._channel(fiber, channel_id)["state"]["state_name"]
            except AssertionError as err:
                last = str(err)
            if last == "ChannelReady":
                return
            time.sleep(1)
        self.fail(f"{fiber.rpc_port} 上 {channel_id} 未就绪: {last}")

    def _channel_ready(self, channel_id, timeout=120):
        for fiber in (self.victim, self.attacker):
            self._wait_channel_ready(fiber, channel_id, timeout=timeout)

    # --------------------------------------------------------------- payments

    def _craft_prefix_hash(self):
        preimage = self.generate_random_preimage()
        digest = hashlib.sha256(bytes.fromhex(preimage[2:])).digest()
        bad_hash = "0x" + (digest[:20] + bytes(b ^ 1 for b in digest[20:])).hex()
        assert bytes.fromhex(bad_hash[2:])[:20] == digest[:20], bad_hash
        assert bad_hash != "0x" + digest.hex(), bad_hash
        return preimage, bad_hash

    def _hold_invoice_with_amount(self, payment_hash, amount, description):
        # Former test_full_hash_invalid_preimage.py _hold_invoice.
        return self.attacker.get_client().new_invoice(
            {
                "amount": hex(amount),
                "currency": "Fibd",
                "description": description,
                "payment_hash": payment_hash,
                "hash_algorithm": "sha256",
                "final_expiry_delta": hex(ROUTED_FINAL_EXPIRY_DELTA),
            }
        )

    def _hold_invoice_with_algorithm(self, payment_hash, algorithm, description):
        # Former test_full_hash_weak_evidence.py _hold_invoice.
        return self.attacker.get_client().new_invoice(
            {
                "amount": hex(PAYMENT_AMOUNT),
                "currency": "Fibd",
                "description": description,
                "payment_hash": payment_hash,
                "hash_algorithm": algorithm,
                "final_expiry_delta": hex(FINAL_EXPIRY_DELTA),
                "expiry": HOLD_INVOICE_EXPIRY,
            }
        )

    def _wait_previous_combination_gone(self, timeout=660):
        """等本端把上一条组合的通道移出 ChannelReady，再开下一条组合的新通道。

        上一条组合的通道此时已在链上被对端强关；若本端仍把它列为 ChannelReady，
        新通道会让付款落点不唯一，后面按 ``self.channel_id`` 做的归属断言就不成立。

        本端只在 ``CHECK_CHANNELS_SHUTDOWN_INTERVAL``（fiber-lib ``network.rs``，固定
        300 秒）那一轮才扫描 ChannelReady 通道并调用 ``check_channel_shutdown`` →
        ``get_shutdown_tx`` 发现 funding 已被花掉；这与 2 秒的 watchtower 间隔无关。
        因此这里按 ``assert_local_closed`` 的口径等两轮并留 60 秒余量：等不够一轮就
        断言"仍然就绪"会把正常的周期检查当成异常。
        超时按观测到的真实状态失败，不把"仍然就绪"当成可接受的通过。
        """
        observations = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            observations = [
                (
                    channel["channel_id"],
                    channel["state"]["state_name"],
                    str(channel["state"].get("state_flags") or ""),
                )
                for channel in self.victim.get_client().list_channels(
                    {"pubkey": self.attacker.get_pubkey()}
                )["channels"]
                if channel["state"]["state_name"] == "ChannelReady"
            ]
            if not observations:
                return
            time.sleep(1)
        self.fail(
            f"上一条组合的通道已在链上强关，但本端 {timeout}s（含两轮 300s 周期检查）"
            f"内仍将其列为 ChannelReady: {observations}"
        )

    def _finish_legacy_combination(self, commitment, timeout=660):
        """Drain the previous Legacy settlement before reusing its funding wallets.

        Leaving ChannelReady does not mean the balance sweeps have finished.
        Wait for all successor commitment cells and outstanding pool transactions,
        then observe two quiet watchtower intervals before opening another channel.
        Do not use this for V1 rejection cases, which intentionally retain live TLCs.
        """
        lock = commitment["outputs"][0]["lock"]
        args = bytes.fromhex(lock["args"].removeprefix("0x"))
        assert len(args) == LEGACY_COMMITMENT_ARGS_LENGTH, lock
        search_key = {
            "script": {**lock, "args": "0x" + args[:36].hex()},
            "script_type": "lock",
            "script_search_mode": "prefix",
        }
        deadline = time.monotonic() + timeout
        quiet_since = None
        cells, pool = None, None
        while time.monotonic() < deadline:
            cells = self.ckb.get_cells(search_key, "asc", "0x1", None)["objects"]
            pool = self.ckb.get_raw_tx_pool()
            transactions = list(
                dict.fromkeys(
                    list(pool.get("pending") or []) + list(pool.get("proposed") or [])
                )
            )
            if not cells and not transactions:
                if quiet_since is None:
                    quiet_since = time.monotonic()
                if time.monotonic() - quiet_since >= 2 * WATCHTOWER_INTERVAL:
                    return
            else:
                quiet_since = None
            # This test owns an isolated devnet. Confirm pending wallet spends before
            # opening another channel; merely polling ChannelReady cannot advance them.
            for tx_hash in transactions:
                self.Miner.miner_until_tx_committed(self.node, tx_hash)
            if not transactions:
                self.Miner.miner_with_version(self.node, "0x0")
            time.sleep(1)
        self.fail(
            f"Previous Legacy combination did not finish settlement within {timeout}s: "
            f"channel={self.channel_id}, commitment={commitment['hash']}, "
            f"live_cells={cells}, tx_pool={pool}"
        )

    def _combination_channel(self, index):
        """Combination 0 reuses the channel opened by setup_method; later ones open fresh."""
        if index:
            self._wait_previous_combination_gone()
            self.channel_id = self.open_channel(
                self.victim, self.attacker, self.channel_local_balance, 0
            )
        self._channel_ready(self.channel_id)
        ready = [
            channel
            for channel in self.victim.get_client().list_channels(
                {"pubkey": self.attacker.get_pubkey()}
            )["channels"]
            if channel["state"]["state_name"] == "ChannelReady"
        ]
        assert len(ready) == 1, (
            "付款必须落在唯一一条就绪通道上: " f"{[c['channel_id'] for c in ready]}"
        )

    def _hold_bad_payment(self, algorithm):
        """Pay an invoice whose hash matches the preimage digest only in its prefix."""
        preimage = "0x" + secrets.token_hex(32)
        bad_hash = craft_prefix_only_hash(preimage, algorithm)
        invoice = self.attacker.get_client().new_invoice(
            {
                "amount": hex(PAYMENT_AMOUNT),
                "currency": "Fibd",
                "description": f"H32V2-07 prefix-only {algorithm} invoice",
                "payment_hash": bad_hash,
                "hash_algorithm": algorithm,
                "final_expiry_delta": hex(FINAL_EXPIRY_DELTA),
                "expiry": HOLD_INVOICE_EXPIRY,
            }
        )
        payment = self.victim.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "max_fee_rate": hex(1000000000000000),
            }
        )
        assert payment["payment_hash"] == bad_hash, payment
        self.wait_payment_state(self.victim, bad_hash, "Inflight", timeout=120)
        self.wait_invoice_state(self.attacker, bad_hash, "Received", timeout=120)
        self._wait_committed_tlc(self.victim, bad_hash)
        self._wait_committed_tlc(self.attacker, bad_hash)
        return preimage, bad_hash

    def _send_explicit_route(self, payer, hops, invoice, amount):
        """Explicit route disables retries; an on-chain loss is not a refund."""
        route = None
        last_error = None
        for _ in range(90):
            try:
                route = payer.get_client().build_router(
                    {
                        "amount": hex(amount),
                        "hops_info": hops,
                        "final_tlc_expiry_delta": hex(ROUTED_FINAL_EXPIRY_DELTA),
                    }
                )
                break
            except Exception as err:  # gossip for the exact outpoint may lag
                last_error = err
                time.sleep(1)
        assert route is not None, f"explicit route not available: {last_error}"
        return payer.get_client().send_payment_with_router(
            {
                "router": route["router_hops"],
                "invoice": invoice["invoice_address"],
            }
        )

    def _send_routed_payment(self, payer, invoice, payment_hash, hops_info):
        """Pay a multi-hop invoice over the exact selected channel outpoints."""
        last_error = None
        route = None
        deadline = time.time() + 90
        while time.time() < deadline:
            try:
                route = payer.get_client().build_router(
                    {
                        "amount": hex(PAYMENT_AMOUNT),
                        "hops_info": hops_info,
                        "final_tlc_expiry_delta": hex(ROUTED_FINAL_EXPIRY_DELTA),
                    }
                )
                break
            except (
                Exception
            ) as err:  # gossip for the second hop may not have arrived yet
                last_error = err
                time.sleep(1)
        assert route is not None, f"显式路由在 gossip 同步后仍不可用: {last_error}"
        payment = payer.get_client().send_payment_with_router(
            {
                "router": route["router_hops"],
                "invoice": invoice["invoice_address"],
            }
        )
        if "payment_hash" in payment:
            assert payment["payment_hash"] == payment_hash, payment
        return payment

    def _pay_hold_invoice(self, invoice, payment_hash):
        payment = self.victim.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "max_fee_rate": hex(1000000000000000),
            }
        )
        assert payment["payment_hash"] == payment_hash, payment
        self.wait_payment_state(self.victim, payment_hash, "Inflight", timeout=120)
        self.wait_invoice_state(self.attacker, payment_hash, "Received", timeout=120)
        return payment

    def _inject_prefix_preimage(self, payment_hash, preimage):
        """Force the counterparty to store a preimage its own full hash never verified.

        The adapted build stores a preimage under a hash it does not hash to.
        """
        return self.attacker.get_client().call(
            "create_preimage",
            [{"payment_hash": payment_hash, "preimage": preimage, "force": True}],
        )

    def _hours_past_expiry(self, tlc):
        remaining = int(tlc["expiry"], 16) / 1000.0 - time.time()
        return max(1, int(remaining // 3600) + 2)

    # ------------------------------------------------------------- assertions

    def _assert_node_refuses_preimage(self, payment_hash, preimage):
        """The preimage does not hash to the full payment hash: both RPCs must refuse it."""
        with pytest.raises(Exception) as settle_err:
            self.attacker.get_client().settle_invoice(
                {"payment_hash": payment_hash, "payment_preimage": preimage}
            )
        settle_text = str(settle_err.value)
        assert (
            "preimage" in settle_text.lower() or "hash" in settle_text.lower()
        ), settle_text
        with pytest.raises(Exception) as watchtower_err:
            self.attacker.get_client().call(
                "create_preimage",
                [{"payment_hash": payment_hash, "preimage": preimage}],
            )
        watchtower_text = str(watchtower_err.value)
        assert "preimage" in watchtower_text.lower(), watchtower_text

    def _assert_no_success_record(self, payment_hash):
        payment = self.victim.get_client().get_payment({"payment_hash": payment_hash})
        assert payment["status"] != "Success", f"仅前缀消费不得记为本端兑现: {payment}"
        assert payment.get("payment_preimage") is None, payment
        invoice = self.attacker.get_client().get_invoice({"payment_hash": payment_hash})
        assert (
            invoice["status"] != "Paid"
        ), f"对端不得为它从未知道的完整 hash 记录收款成功: {invoice}"

    def _assert_legacy_prefix_claim(
        self, spend, payment_hash, preimage, algorithm, received_entry
    ):
        """Legacy accepts the prefix-only claim: 57/85/20 layout plus direction/algorithm bits."""
        witness = SettlementWitness.from_hex(spend["witnesses"][0], version="legacy")
        assert witness.to_hex() == spend["witnesses"][0], "Legacy witness 往返重建失败"
        raw = bytes.fromhex(spend["witnesses"][0].removeprefix("0x"))
        expected_len = (
            16
            + 2
            + 85 * len(witness.tlcs)
            + 72  # remote hash/amount + local hash/amount
            + sum(
                2 + 65 + (32 if unlock.preimage is not None else 0)
                for unlock in witness.unlocks
            )
        )
        assert (
            len(raw) == expected_len
        ), f"Legacy witness 不是 85 字节 TLC 条目布局: {len(raw)} != {expected_len}"
        witness.assert_single_tlc_claim([(payment_hash, PAYMENT_AMOUNT)], preimage)
        assert len(witness.unlocks) == 1, witness.unlocks
        assert witness.unlocks[0].unlock_type == 0, witness.unlocks
        tlc = witness.tlcs[witness.unlocks[0].unlock_type]
        assert len(tlc.payment_hash) == 20, tlc
        assert (
            tlc.payment_hash == bytes.fromhex(payment_hash.removeprefix("0x"))[:20]
        ), tlc
        # 链上方向位按“被结算承诺的 settlement 快照”方向编码，而不是本地 TLCId 视角：
        # fiber-lib `tracked_settlement_tlcs` 在 for_remote=false 时对快照 tlc_id 取 flip，
        # 其单测 test_tracked_settlement_tlcs_extraction 断言同一快照 for_remote=true 为
        # Offered(5)、本地视角为 Received(5)。因此强关端“收到”的条目在链上是 bit0=0
        # （Offered 分支，remote_htlc 公钥 + 原像），强关端“给出”的条目才是 bit0=1。
        expected_direction = 0 if received_entry else 1
        assert (
            tlc.tlc_type & 1 == expected_direction
        ), f"tlc_type 方向位与强关端不符: tlc_type={tlc.tlc_type} received_entry={received_entry}"
        assert bool(tlc.tlc_type & 2) == (algorithm == "sha256"), (
            f"tlc_type 算法位与 invoice hash_algorithm 不符: "
            f"tlc_type={tlc.tlc_type} algorithm={algorithm}"
        )
        self._assert_recipient_payout(spend, self.attacker.get_account()["lock_arg"])
        return witness

    def _assert_recipient_payout(self, spend, lock_arg):
        message = self.get_tx_message(spend["hash"])
        received = sum(
            int(output["capacity"], 16)
            for output in spend["outputs"]
            if output["lock"]["args"] == lock_arg
        )
        spent = 0
        for item in spend["inputs"]:
            point = item["previous_output"]
            cell = self.node.getClient().get_transaction(point["tx_hash"])[
                "transaction"
            ]["outputs"][int(point["index"], 16)]
            if cell["lock"]["args"] == lock_arg:
                spent += int(cell["capacity"], 16)
        assert received - spent == PAYMENT_AMOUNT - message["fee"], (
            "收款人到账不等于 TLC 金额减矿工费: "
            f"received={received} spent={spent} amount={PAYMENT_AMOUNT} fee={message['fee']}"
        )

    def _assert_v1_rejected(self, commitment, payment_hash):
        """V1 must reject the prefix-only claim: cell stays live and nothing is paid out."""
        lock_arg = self.attacker.get_account()["lock_arg"]
        before = self._lock_arg_wallet_capacity(lock_arg)
        observed = []
        for round_index in range(V1_REJECT_ROUNDS):
            self._mine_watchtower_rounds(1)
            live = self.node.getClient().get_live_cell("0x0", commitment["hash"])
            observed.append(
                {
                    "round": round_index,
                    "status": live["status"],
                    "death_hash": self.get_ln_cell_death_hash(commitment["hash"])[0],
                }
            )
            # The rejected submission is only observable through the surviving
            # commitment cell; no exact contract wording is asserted here.
            assert live["status"] == "live", (
                "V1 承诺 cell 被消费，仅前缀结算未被合约拒绝: "
                f"{observed[-1]} spender={self._spender_of(commitment['hash'])}"
            )
            assert not self.get_ln_cell_death_hash(commitment["hash"])[0], observed[-1]
        after = self._lock_arg_wallet_capacity(lock_arg)
        assert (
            after <= before
        ), f"V1 拒绝后对端链上账户仍收到付款: {before} -> {after} observed={observed}"
        payment = self.victim.get_client().get_payment({"payment_hash": payment_hash})
        assert (
            payment["status"] == "Inflight"
        ), f"V1 拒绝后本端既不得记兑现也不得提前失败: {payment}"
        assert payment.get("payment_preimage") is None, payment

    def _assert_prefix_only_claim(self, spend, bad_hash, preimage, pending):
        """Parse the Legacy settlement witness and prove the claim is prefix-only."""
        assert spend.get("witnesses"), spend
        witness = SettlementWitness.from_hex(spend["witnesses"][0], version="legacy")
        assert (
            witness.to_hex() == spend["witnesses"][0]
        ), "legacy witness did not round-trip"
        witness.assert_pending_tlcs(pending)
        assert len(witness.unlocks) == 1, witness.unlocks
        unlock = witness.unlocks[0]
        assert 0 <= unlock.unlock_type < len(witness.tlcs), unlock
        claimed = witness.tlcs[unlock.unlock_type]
        assert unlock.preimage == bytes.fromhex(preimage[2:]), unlock
        digest = hashlib.sha256(bytes.fromhex(preimage[2:])).digest()
        assert claimed.payment_hash == digest[:20], claimed
        assert claimed.payment_hash == bytes.fromhex(bad_hash[2:])[:20], claimed
        assert digest != bytes.fromhex(bad_hash[2:]), (
            "the committed spend must use a preimage whose full hash differs from "
            "the invoice hash; only the 20-byte Legacy prefix matches"
        )
        return witness

    def _assert_failed_without_recovery(self, bad_hash, unrelated_hash, expiry):
        """Shared H32V2-18 assertions: terminal failure, repeated scans idempotent.

        证明点对应（被 H32V2-18 两个方法共用）：
        - 目标按失败收尾：Failed、无成功原像、失败时刻早于记录的 TLC 到期、目标 TLC 终态。
        - 重复扫描幂等：剩余扫描中状态、fee、本端钱包容量都不再变化（不重复计账、不追回）。
        - 无关 TLC 不受影响：记录仍在、非终态，其付款仍 Inflight。
        """
        self.wait_payment_state(self.victim, bad_hash, "Failed", timeout=660)
        failed = self.victim.get_client().get_payment({"payment_hash": bad_hash})
        assert failed.get("payment_preimage") is None, failed
        assert int(failed["last_updated_at"], 16) < expiry, failed
        # 失败由消费证据触发而非超时：观察时刻也必须在到期之前。
        assert int(time.time() * 1000) < expiry, failed
        self._wait_target_tlc_terminal(self.victim, bad_hash)

        unrelated = self._tlc_in(self.victim, self.channel_id, unrelated_hash)
        assert unrelated, "the unrelated still-pending TLC disappeared"
        unrelated_status = unrelated[0]["status"]
        assert not tlc_is_terminal(unrelated[0]), unrelated
        unrelated_payment = self.victim.get_client().get_payment(
            {"payment_hash": unrelated_hash}
        )
        assert unrelated_payment["status"] == "Inflight", unrelated_payment

        # The already-spent on-chain funds belong to the counterparty now. The
        # honest node must stop carrying the target payment; it must not credit or
        # recover those funds on any later scan of the same confirmed claim.
        self._mine_watchtower_rounds_from_config(
            1
        )  # let any on-chain credit for this node land
        baseline_wallet = self._fiber_wallet_capacity(self.victim)
        # 基线采样后连续三轮扫描：任何一次“重新挂起/重复计账”都会改变这些值。
        for _ in range(3):
            self._mine_watchtower_rounds_from_config(1)
            again = self.victim.get_client().get_payment({"payment_hash": bad_hash})
            assert again["status"] == "Failed", again
            assert again.get("payment_preimage") is None, again
            assert again["fee"] == failed["fee"], again
            assert self._fiber_wallet_capacity(self.victim) == baseline_wallet, (
                "repeated scans of the same confirmed prefix-only claim must not "
                "re-count or recover the already-spent on-chain funds: "
                f"baseline={baseline_wallet} now={self._fiber_wallet_capacity(self.victim)}"
            )
            # 无关 TLC 必须逐项保持不变：记录仍在、状态与基线完全相同、非终态、付款仍 Inflight。
            unrelated_again = self._tlc_in(self.victim, self.channel_id, unrelated_hash)
            assert (
                unrelated_again
            ), "the unrelated TLC was removed by the target failure"
            assert unrelated_again[0]["status"] == unrelated_status, unrelated_again
            assert not tlc_is_terminal(unrelated_again[0]), unrelated_again
            unrelated_again_payment = self.victim.get_client().get_payment(
                {"payment_hash": unrelated_hash}
            )
            assert (
                unrelated_again_payment["status"] == "Inflight"
            ), unrelated_again_payment
        return failed

    def _assert_sibling_survives(self, payment_hash, rounds=SIBLING_ROUNDS):
        """Neither fulfil nor fail the same-prefix sibling during bounded watchtower rounds."""
        deadline = time.time() + WATCHTOWER_INTERVAL * rounds
        while time.time() < deadline:
            self._mine_watchtower_rounds(1)
            tlcs = self._tlcs(self.attacker, payment_hash)
            assert tlcs, f"同前缀其他目标 {payment_hash} 的记录消失"
            for tlc in tlcs:
                assert not tlc_is_terminal(tlc), f"同前缀其他目标被错误收尾: {tlc}"
                value = tlc["status"][next(iter(tlc["status"]))]
                assert value not in REMOVED_STATUSES, f"同前缀其他目标被错误移除: {tlc}"
            payment = self.victim.get_client().get_payment(
                {"payment_hash": payment_hash}
            )
            assert (
                payment["status"] == "Inflight"
            ), f"同前缀其他目标不得被成功或失败处理: {payment}"
            assert payment.get("payment_preimage") is None, payment
            invoice = self.attacker.get_client().get_invoice(
                {"payment_hash": payment_hash}
            )
            assert invoice["status"] == "Received", invoice
        # Final state after the last watchtower round.
        final_tlcs = self._tlcs(self.attacker, payment_hash)
        assert final_tlcs, f"同前缀其他目标 {payment_hash} 的记录消失"
        assert not tlc_is_terminal(final_tlcs[0]), final_tlcs[0]
        assert (
            self.victim.get_client().get_payment({"payment_hash": payment_hash})[
                "status"
            ]
            == "Inflight"
        )

    def _open_target_and_unrelated_tlcs(self):
        """One crafted-hash target plus one unrelated hold TLC in the same channel."""
        preimage, bad_hash = self._craft_prefix_hash()
        unrelated_preimage = self.generate_random_preimage()
        unrelated_hash = (
            "0x" + hashlib.sha256(bytes.fromhex(unrelated_preimage[2:])).hexdigest()
        )
        target_invoice = self._hold_invoice_with_amount(
            bad_hash, PAYMENT_AMOUNT, "H32V2-18 target prefix-only claim"
        )
        unrelated_invoice = self._hold_invoice_with_amount(
            unrelated_hash, UNRELATED_AMOUNT, "H32V2-18 unrelated hold TLC"
        )
        downstream = self.channel_of(self.victim)
        hops = [
            {
                "pubkey": self.attacker.get_pubkey(),
                "channel_outpoint": downstream["channel_outpoint"],
            }
        ]
        unrelated_payment = self._send_explicit_route(
            self.victim, hops, unrelated_invoice, UNRELATED_AMOUNT
        )
        target_payment = self._send_explicit_route(
            self.victim, hops, target_invoice, PAYMENT_AMOUNT
        )
        assert unrelated_payment["payment_hash"] == unrelated_hash, unrelated_payment
        assert target_payment["payment_hash"] == bad_hash, target_payment
        for payment_hash in (bad_hash, unrelated_hash):
            self.wait_invoice_state(
                self.attacker, payment_hash, "Received", timeout=120
            )
            self.wait_payment_state(self.victim, payment_hash, "Inflight", timeout=60)
            self._wait_tlc_committed(self.victim, self.channel_id, payment_hash)
            self._wait_tlc_committed(self.attacker, self.channel_id, payment_hash)
        target_tlc = self._wait_tlc_committed(self.victim, self.channel_id, bad_hash)
        return bad_hash, preimage, unrelated_hash, int(target_tlc["expiry"], 16)

    # --------------------------------------------------------------- H32V2-07

    # TEST-MAP: H32V2-07
    # TEST-EVIDENCE-BEGIN: H32V2-07
    # Evidence | covered | Legacy channel (57-byte commitment args); algorithms {ckb_hash, sha256}
    # x entry directions {received = counterparty commitment, offered = victim commitment}; the
    # on-chain prefix-only claim is accepted end to end: strict 85-byte/20-byte Legacy witness,
    # unlock index selects the exact TLC entry, direction bit (tlc_type & 1) and algorithm bit
    # (tlc_type & 2) match the closers' commitment, recipient payout equals the TLC amount minus
    # fee, and neither end records Success/Paid or a preimage. Off-chain settle_invoice and a
    # non-forced create_preimage both refuse the mismatching preimage first.
    # TEST-EVIDENCE-END: H32V2-07
    def test_legacy_prefix_only_claim_accepted_each_algorithm_and_direction(self):
        combinations = [
            (algorithm, received_entry)
            for algorithm in ("ckb_hash", "sha256")
            for received_entry in (True, False)
        ]
        for index, (algorithm, received_entry) in enumerate(combinations):
            with self.subTest(algorithm=algorithm, received_entry=received_entry):
                self._combination_channel(index)
                preimage, bad_hash = self._hold_bad_payment(algorithm)
                self._assert_node_refuses_preimage(bad_hash, preimage)
                closer = self.attacker if received_entry else self.victim
                commitment = self._force_close(closer)
                args = bytes.fromhex(
                    commitment["outputs"][0]["lock"]["args"].removeprefix("0x")
                )
                assert (
                    len(args) == 57
                ), f"Legacy 承诺 args 必须 57 字节，实际 {len(args)}: 通道未协商成 Legacy"
                # 承诺 cell 的结算受承诺延迟约束：与 H32V2-14 / settle_held_channel 同一口径，
                # 先推进一个 epoch，对端才会构造并广播仅前缀结算；少了这一步交易池始终为空。
                self.ckb.generate_epochs("0x1", wait_time=0)
                self._inject_prefix_preimage(bad_hash, preimage)
                # 对端把仅前缀结算放进交易池后不会自动被打包，而 _wait_spender 只看已上链索引
                # （get_ln_cell_death_hash 要求恰好两条记录），池中的结算对它不可见。
                # 这里用会扫描交易池并主动打包的 _wait_spend，与 H32V2-14 同一做法。
                spend = self._wait_spend(
                    {"tx_hash": commitment["hash"], "index": "0x0"},
                    f"仅前缀结算 ({algorithm}, received={received_entry})",
                )
                self._assert_legacy_prefix_claim(
                    spend, bad_hash, preimage, algorithm, received_entry
                )
                self._assert_no_success_record(bad_hash)
                if index + 1 < len(combinations):
                    self._finish_legacy_combination(commitment)

    # TEST-MAP: H32V2-07
    # TEST-EVIDENCE-BEGIN: H32V2-07
    # Evidence | partial | V1 channel (58-byte args with trailing 0x01) plus V1_PREFIX_CLAIM_ENV, so the
    # counterparty is configured to build a V1 settlement whose preimage does not match the full 32-byte
    # hash; algorithms {ckb_hash, sha256} x entry directions {received, offered}. After bounded watchtower
    # rounds the commitment cell is still live, get_ln_cell_death_hash stays empty, the counterparty's
    # on-chain account never grows and the victim's payment stays Inflight without a preimage.
    # Attribution limit: a script-rejected tx never reaches the pool/chain and the counterparty exposes the
    # attempt only in its log, so "no consumption" is consistent with the contract rejecting the V1 claim
    # but does not by itself prove an attempt happened. The discriminating positive control is the Legacy
    # method above, where the same forced preimage is accepted on chain by the 20-byte prefix rule.
    # TEST-EVIDENCE-END: H32V2-07
    def test_v1_prefix_only_claim_rejected_each_algorithm_and_direction(self):
        combinations = [
            (algorithm, received_entry)
            for algorithm in ("ckb_hash", "sha256")
            for received_entry in (True, False)
        ]
        for index, (algorithm, received_entry) in enumerate(combinations):
            with self.subTest(algorithm=algorithm, received_entry=received_entry):
                self._combination_channel(index)
                preimage, bad_hash = self._hold_bad_payment(algorithm)
                self._assert_node_refuses_preimage(bad_hash, preimage)
                closer = self.attacker if received_entry else self.victim
                commitment = self._force_close(closer)
                args = bytes.fromhex(
                    commitment["outputs"][0]["lock"]["args"].removeprefix("0x")
                )
                assert (
                    len(args) == 58 and args[-1] == 1
                ), f"V1 承诺 args 必须 58 字节且末位 0x01，实际 {len(args)}/{args[-1]:#x}"
                self._inject_prefix_preimage(bad_hash, preimage)
                self._assert_v1_rejected(commitment, bad_hash)

    # --------------------------------------------------------------- H32V2-14

    # TEST-MAP: H32V2-14
    # TEST-EVIDENCE-BEGIN: H32V2-14
    # Evidence | mapped (static reading of this method; no devnet execution recorded) | Single-hop
    # explicit route to a Legacy counterparty (LEGACY_COUNTERPARTY_ENV -> 57-byte commitment args)
    # whose hold invoice hash shares only the 20-byte prefix with sha256(preimage); the explicit route
    # disables retries. The counterparty force-closes, and after create_preimage(force=true) its own
    # watchtower confirms a Legacy settlement of exactly that TLC.
    # Verification points:
    # 1. The confirmed spend is prefix-only: the witness parses as Legacy, lists exactly
    #    [(bad_hash, PAYMENT_AMOUNT)], has one unlock, unlock.preimage == preimage and
    #    claimed.payment_hash == sha256(preimage)[:20] == bad_hash[:20], while sha256(preimage) !=
    #    bad_hash is asserted rather than assumed.
    # 2. The claim is confirmed strictly before the recorded TLC expiry, on the local clock and on
    #    chain median time, so the later failure cannot be attributed to the timeout path.
    # 3. The payer's payment reaches Failed inside the bounded 660 s window with payment_preimage
    #    null and last_updated_at < expiry: no successful preimage is returned.
    # 4. The target TLC on the payer's channel becomes terminal, so it is not left Inflight forever.
    # 5. Three further watchtower scans keep the payment Failed, the fee unchanged and the payer
    #    wallet capacity flat: the already-spent on-chain funds are neither recounted nor recovered.
    # Partial: "other on-chain items settle and the channel winds down" is not asserted - this
    #   channel carries a single claimed TLC and the method stops at the terminal TLC plus a flat
    #   wallet, not at a locally Closed channel.
    # Not covered: V1 commitment layout; counter-hash algorithms other than sha256; xUDT; MPP; a
    #   second concurrent TLC, restart and repeated-scan idempotence (H32V2-18); the relay path
    #   (H32V2-15); received-side finalization (H32V2-16).
    # TEST-EVIDENCE-END: H32V2-14
    def test_direct_payer_fails_confirmed_prefix_only_claim(self):
        # 前提：对手端不宣告 full hash，通道按 Legacy 布局协商；hold invoice 的 32 字节 hash 只与
        # sha256(preimage) 共享 20 字节前缀；付款走显式单跳路由，不提供重试机会。
        preimage, bad_hash = self._craft_prefix_hash()
        invoice = self._hold_invoice_with_amount(
            bad_hash, PAYMENT_AMOUNT, "H32V2-14 direct prefix-only claim"
        )
        downstream = self.channel_of(self.victim)
        hops = [
            {
                "pubkey": self.attacker.get_pubkey(),
                "channel_outpoint": downstream["channel_outpoint"],
            }
        ]
        payment = self._send_explicit_route(self.victim, hops, invoice, PAYMENT_AMOUNT)
        assert payment["payment_hash"] == bad_hash, payment
        self.wait_invoice_state(self.attacker, bad_hash, "Received", timeout=120)
        self.wait_payment_state(self.victim, bad_hash, "Inflight", timeout=60)
        payer_tlc = self._wait_tlc_committed(self.victim, self.channel_id, bad_hash)
        self._wait_tlc_committed(self.attacker, self.channel_id, bad_hash)
        expiry = int(payer_tlc["expiry"], 16)

        commitment = self._force_close_attacker()
        self.ckb.generate_epochs("0x1", wait_time=0)
        assert (
            self.victim.get_client().get_payment({"payment_hash": bad_hash})["status"]
            == "Inflight"
        ), "the target payment must not resolve before the on-chain claim confirms"
        self._inject_prefix_preimage(bad_hash, preimage)
        spend = self._wait_spend(
            {"tx_hash": commitment["hash"], "index": "0x0"}, label="commitment cell"
        )
        # 验证点 1：确认的链上消费确实是 Legacy 前缀原像，而不是完整 hash 匹配。
        self._assert_prefix_only_claim(
            spend, bad_hash, preimage, [(bad_hash, PAYMENT_AMOUNT)]
        )

        # 验证点 2：消费在记录的 TLC 到期前确认，失败只能来自链上证据而不是超时路径。
        # The claim is confirmed strictly before the recorded TLC expiry, so the
        # failure must come from the confirmed on-chain evidence, not the timeout.
        assert (
            int(time.time() * 1000) < expiry
        ), f"claim confirmed after the recorded TLC expiry {expiry}"
        assert (
            self._chain_median_time() < expiry
        ), f"chain median time is already past the recorded TLC expiry {expiry}"
        # 验证点 3/4：到期前有界窗口内付款 Failed、不返回成功原像，目标 TLC 收尾。
        self.wait_payment_state(self.victim, bad_hash, "Failed", timeout=660)
        failed = self.victim.get_client().get_payment({"payment_hash": bad_hash})
        assert failed.get("payment_preimage") is None, failed
        assert int(failed["last_updated_at"], 16) < expiry, failed
        assert int(time.time() * 1000) < expiry, failed
        self._wait_target_tlc_terminal(self.victim, bad_hash)

        # The confirmed prefix-only spend already transferred the channel funds to
        # the counterparty. This test asserts the payer stops carrying the payment;
        # it does NOT and must not show the already-spent on-chain funds recovered.
        # 验证点 5：重复扫描不得重复计账或追回已付链上资金。
        self._mine_watchtower_rounds_from_config(
            1
        )  # let any on-chain credit for the payer land
        baseline_wallet = self._fiber_wallet_capacity(self.victim)
        for _ in range(3):
            self._mine_watchtower_rounds_from_config(1)
            again = self.victim.get_client().get_payment({"payment_hash": bad_hash})
            assert again["status"] == "Failed", again
            assert again.get("payment_preimage") is None, again
            assert again["fee"] == failed["fee"], again
            assert self._fiber_wallet_capacity(self.victim) == baseline_wallet, (
                "repeated scans must not recover the already-spent on-chain funds; "
                f"payer baseline={baseline_wallet} now={self._fiber_wallet_capacity(self.victim)}"
            )

    # --------------------------------------------------------------- H32V2-15

    # TEST-MAP: H32V2-15
    def test_relay_fails_upstream_before_earliest_expiry(self):
        # Payer -- Victim(router) -- Attacker; the auto-opened channel is downstream.
        payer = self.start_new_fiber(self.generate_account(10000))
        downstream = self.channel_of(self.victim)
        upstream_id = self.open_channel(payer, self.victim, 200 * CKB, 0)
        upstream_before = self._channel_by_id(payer, upstream_id)
        router_upstream_before = self._channel_by_id(self.victim, upstream_id)
        assert upstream_before["state"]["state_name"] == "ChannelReady", upstream_before
        assert (
            router_upstream_before["state"]["state_name"] == "ChannelReady"
        ), router_upstream_before
        upstream_balances = {
            "payer": (
                upstream_before["local_balance"],
                upstream_before["remote_balance"],
            ),
            "router": (
                router_upstream_before["local_balance"],
                router_upstream_before["remote_balance"],
            ),
        }

        preimage, bad_hash = self._craft_prefix_hash()
        invoice = self._hold_invoice_with_amount(
            bad_hash, PAYMENT_AMOUNT, "H32V2-15 relay prefix-only claim"
        )
        hops = [
            {
                "pubkey": self.victim.get_pubkey(),
                "channel_outpoint": upstream_before["channel_outpoint"],
            },
            {
                "pubkey": self.attacker.get_pubkey(),
                "channel_outpoint": downstream["channel_outpoint"],
            },
        ]
        payment = self._send_explicit_route(payer, hops, invoice, PAYMENT_AMOUNT)
        assert payment["payment_hash"] == bad_hash, payment
        self.wait_invoice_state(self.attacker, bad_hash, "Received", timeout=120)
        self.wait_payment_state(payer, bad_hash, "Inflight", timeout=60)
        payer_upstream = self._wait_tlc_committed(payer, upstream_id, bad_hash)
        router_upstream = self._wait_tlc_committed(self.victim, upstream_id, bad_hash)
        router_downstream = self._wait_tlc_committed(
            self.victim, self.channel_id, bad_hash
        )
        self._wait_tlc_committed(self.attacker, self.channel_id, bad_hash)
        earliest_expiry = min(
            int(tlc["expiry"], 16)
            for tlc in (payer_upstream, router_upstream, router_downstream)
        )

        commitment = self._force_close_attacker()
        self.ckb.generate_epochs("0x1", wait_time=0)
        assert (
            payer.get_client().get_payment({"payment_hash": bad_hash})["status"]
            == "Inflight"
        ), "the payer must stay Inflight until the downstream claim confirms"
        self._inject_prefix_preimage(bad_hash, preimage)
        spend = self._wait_spend(
            {"tx_hash": commitment["hash"], "index": "0x0"}, label="commitment cell"
        )
        self._assert_prefix_only_claim(
            spend, bad_hash, preimage, [(bad_hash, PAYMENT_AMOUNT)]
        )

        # Bounded propagation loop: the upstream channel must stay live while the
        # failed downstream evidence travels back to the original payer.
        deadline = time.monotonic() + 660
        upstream_channels = []
        payer_payment = None
        while True:
            downstream_channel = self.channel_of(self.victim, include_closed=True)
            payer_payment = payer.get_client().get_payment({"payment_hash": bad_hash})
            downstream_terminal = all(
                tlc_is_terminal(tlc)
                for tlc in self._tlc_in(self.victim, self.channel_id, bad_hash)
            )
            upstream_channels = [
                self._channel_by_id(payer, upstream_id),
                self._channel_by_id(self.victim, upstream_id),
            ]
            for channel in upstream_channels:
                assert channel["state"]["state_name"] == "ChannelReady", (
                    "upstream channel left ChannelReady during failure propagation: "
                    f"{channel}; payment={payer_payment}"
                )
                assert channel.get("shutdown_transaction_hash") is None, channel
            upstream_states = []
            for channel, direction in zip(upstream_channels, ("Outbound", "Inbound")):
                states = [
                    tlc["status"]
                    for tlc in channel["pending_tlcs"]
                    if tlc["payment_hash"] == bad_hash
                ]
                upstream_states.append([direction, states])
            upstream_resolved = all(
                states
                and all(state == {direction: "RemoveAckConfirmed"} for state in states)
                for direction, states in upstream_states
            )
            if (
                downstream_terminal
                and upstream_resolved
                and payer_payment["status"] == "Failed"
            ):
                break
            assert time.monotonic() < deadline, (
                "upstream failure propagation incomplete: "
                f"downstream_terminal={downstream_terminal}, "
                f"upstream_states={upstream_states}; downstream={downstream_channel}; "
                f"payment={payer_payment}; upstream={upstream_channels}"
            )
            self._mine_watchtower_rounds_from_config(1)

        assert payer_payment.get("payment_preimage") is None, payer_payment
        # Failure must come from the confirmed downstream claim, not from the
        # normal TLC-expiry path of either hop.
        assert (
            int(time.time() * 1000) < earliest_expiry
        ), f"failure propagated after the earliest recorded TLC expiry {earliest_expiry}"
        assert (
            self._chain_median_time() < earliest_expiry
        ), f"chain median time is already past the earliest expiry {earliest_expiry}"
        for name, channel in (
            ("payer", self._channel_by_id(payer, upstream_id)),
            ("router", self._channel_by_id(self.victim, upstream_id)),
        ):
            assert (
                channel["local_balance"],
                channel["remote_balance"],
            ) == upstream_balances[name], (
                "the honest relay must not charge the "
                f"{name} upstream channel for a downstream on-chain loss: {channel}"
            )
        # The already-spent funds moved on the downstream chain to the counterparty;
        # the upstream failure propagation must not present them as recovered.
        assert (
            payer.get_client().get_payment({"payment_hash": bad_hash})["status"]
            == "Failed"
        ), payer_payment

    # TEST-EVIDENCE-BEGIN H32V2-15
    # covered: explicit two-hop route payer -> victim(router) -> attacker; the
    #   confirmed Legacy prefix-only claim landed on the downstream channel while
    #   the upstream channel stayed ChannelReady with no shutdown_transaction_hash;
    #   both upstream TLCs reached RemoveAckConfirmed, the payer payment ended
    #   Failed without a preimage, and the upstream balances did not change on
    #   either end. The failure timestamp is checked against the earliest of the
    #   three recorded TLC expiries and against the chain median time.
    # partial: "the relay neither caches nor forwards the wrong preimage as
    #   success" is inferred from Failed + payment_preimage null +
    #   RemoveAckConfirmed; the relay's stored preimage record is not read directly.
    # not covered: more than two hops; V1 commitment layout; UDT (xUDT); retry-enabled
    #   routes (the row's precondition excludes retries).
    # TEST-EVIDENCE-END H32V2-15

    # --------------------------------------------------------------- H32V2-16

    # TEST-MAP: H32V2-16
    # TEST-EVIDENCE-BEGIN: H32V2-16
    # Evidence | covered | Relay payer -> victim -> counterparty over one Legacy channel; the
    # victim force-closes its outgoing channel with one prefix-only target TLC and one unrelated
    # received/forwarded TLC, then the counterparty's confirmed prefix-only claim is parsed as a
    # Legacy witness that lists both TLCs and unlocks exactly the target entry. The victim's
    # received TLC (inbound from the payer) is closed as a failed consumption: the payer's payment
    # becomes Failed with no payment_preimage, while the unrelated received TLC keeps its own state
    # (still pending, payer still Inflight without a preimage, receiver invoice still Received).
    # TEST-EVIDENCE-END: H32V2-16
    # H32V2-16 评审行（reviews/full-payment-hash-settlement-v2.md，SPEC-10）：
    #   场景：本端 Legacy 通道已进入链上关闭核对，received TLC 仍未收尾且其他结算条件满足；
    #         监控持有该 TLC 身份准确、已确认的仅前缀匹配而完整 hash 错误原像消费证据。
    #   预期：received TLC 按失败消费收尾且不 fulfill；其他 received TLC 不受影响。
    #   防止：接收侧被遗漏或错误公开原像。
    # 拓扑：payer --(上游 Legacy 57/85)--> victim(本端，中继) --(下游 Legacy 57/85)--> 对端。
    #   两条通道都由对手端不宣告 full hash 协商成 Legacy。本端先强关自己的下游承诺，
    #   再由对端内置 watchtower 广播“仅前缀原像消费”，本端据此收尾中继的 received TLC。
    # 证明点与断言的对应关系：
    #   证明点 1（场景成立）：上游与下游都是 Legacy，且两条通道各有一笔已承诺未完成 TLC。
    #   证明点 2（进入核对且尚未收尾）：强关承诺已确认后，两笔 received TLC 都还没被处理，
    #     付款仍 Inflight，保证后面的 Failed 只能来自消费证据而不是超时或提前收尾。
    #   证明点 3（证据身份准确）：链上消费的 witness 按 Legacy 解析，条目集合恰为目标加无关
    #     兄弟两笔，只解锁目标那一笔；原像完整 hash 与 invoice hash 不相等，只有 20 字节前缀相同。
    #   证明点 4（失败消费收尾且不 fulfill）：付款 Failed、无成功原像，目标 TLC 在上下游两侧
    #     都按失败移除。
    #   证明点 5（其他 received TLC 不受影响）：无关兄弟在上游 inbound / 下游 outbound 都未被
    #     移除，其付款仍 Inflight、发票仍 Received。
    # 未覆盖：V1 承诺布局、xUDT 资产、MPP 分片、多跳（>2 跳）、到期路径；本方法也不校验
    # 已花链上资金的追回（对端已实际取得该资金）。
    def test_relay_received_tlc_fails_after_confirmed_prefix_claim(self):
        victim = self.victim
        counterparty = self.attacker
        payer = self.start_new_fiber(
            self.generate_account(10000), fiber_version=FiberConfigPath.CURRENT_DEV
        )
        # setup_method already opened the victim -> counterparty (downstream) channel.
        downstream_id = self.channel_id
        self._channel_ready(downstream_id)
        upstream_id = self.open_channel(payer, victim, CHANNEL_FUND, 0)
        for fiber in (payer, victim):
            self._wait_channel_ready(fiber, upstream_id)
        self.wait_graph_channels_sync(payer, 2, timeout=90)

        downstream_outpoint = self._channel(victim, downstream_id)["channel_outpoint"]
        upstream_outpoint = self._channel(victim, upstream_id)["channel_outpoint"]
        hops_info = [
            {"pubkey": victim.get_pubkey(), "channel_outpoint": upstream_outpoint},
            {
                "pubkey": counterparty.get_pubkey(),
                "channel_outpoint": downstream_outpoint,
            },
        ]

        # 证明点 3 的素材：目标 hash 只与 preimage 摘要共享 20 字节前缀；兄弟用完整 ckb_hash。
        target_preimage = "0x" + secrets.token_hex(32)
        target_hash = craft_prefix_only_hash(target_preimage, "sha256")
        other_preimage = "0x" + secrets.token_hex(32)
        other_hash = ckb_hash(other_preimage)
        invoices = {}
        for payment_hash, algorithm, description in (
            (target_hash, "sha256", "H32V2-16 prefix-only forwarded target"),
            (other_hash, "ckb_hash", "H32V2-16 unrelated forwarded sibling"),
        ):
            invoices[payment_hash] = counterparty.get_client().new_invoice(
                {
                    "amount": hex(PAYMENT_AMOUNT),
                    "currency": "Fibd",
                    "description": description,
                    "payment_hash": payment_hash,
                    "hash_algorithm": algorithm,
                    "final_expiry_delta": hex(ROUTED_FINAL_EXPIRY_DELTA),
                    "expiry": HOLD_INVOICE_EXPIRY,
                }
            )
            self._send_routed_payment(
                payer, invoices[payment_hash], payment_hash, hops_info
            )

        # 证明点 1：两笔付款都已送达对端（Received）且本端仍 Inflight，两跳四条通道记录上
        # 都出现了该 TLC 的 Committed 条目；本端收到的 inbound 条目尚未被移除。
        for payment_hash in (target_hash, other_hash):
            self.wait_payment_state(payer, payment_hash, "Inflight", timeout=120)
            self.wait_invoice_state(counterparty, payment_hash, "Received", timeout=120)
            for fiber, channel_id in (
                (payer, upstream_id),
                (victim, upstream_id),
                (victim, downstream_id),
                (counterparty, downstream_id),
            ):
                self._wait_committed_tlc(fiber, payment_hash, channel_id)
            self._assert_not_removed(victim, payment_hash, "Inbound", upstream_id)

        commitment = self._force_close(victim)
        args = bytes.fromhex(
            commitment["outputs"][0]["lock"]["args"].removeprefix("0x")
        )
        # 证明点 1：下游按 57 字节 Legacy 承诺强关，witness 20 字节前缀语义才有意义。
        assert (
            len(args) == 57
        ), f"下游通道必须是 Legacy 承诺，实际 args={len(args)} 字节"
        # 结算受承诺延迟约束：与 H32V2-14 / settle_held_channel 同一口径，先推进一个 epoch，
        # 对端才会构造并广播仅前缀结算（少了这一步交易池始终为空）。
        self.ckb.generate_epochs("0x1", wait_time=0)
        self._mine_watchtower_rounds(1)

        # 证明点 2：On-chain close reconciliation has started but no exact consumption exists yet:
        # both received TLCs must still be unresolved and neither payment may fail early.
        for payment_hash in (target_hash, other_hash):
            self._assert_not_removed(victim, payment_hash, "Inbound", upstream_id)
            waiting = payer.get_client().get_payment({"payment_hash": payment_hash})
            assert (
                waiting["status"] == "Inflight"
            ), f"无精确消费证据前不得收尾 received TLC: {waiting}"

        # 证明点 3：Only the target gets a (prefix-only) preimage; the sibling has none.
        # _wait_spender 同时证明该消费是已确认交易、且输入是刚强关的那个承诺 outpoint；
        # from_hex(version="legacy") 固定按本端承诺版本解析，而不是按长度猜测。
        self._inject_prefix_preimage(target_hash, target_preimage)
        claim = self._wait_spend(
            {"tx_hash": commitment["hash"], "index": "0x0"}, "对端仅前缀消费"
        )
        witness = SettlementWitness.from_hex(claim["witnesses"][0], version="legacy")
        # 消费证据列出的条目集合必须恰为目标加兄弟两笔（含金额），证明身份来自精确承诺快照。
        witness.assert_pending_tlcs(
            [(target_hash, PAYMENT_AMOUNT), (other_hash, PAYMENT_AMOUNT)]
        )
        assert len(witness.unlocks) == 1, witness.unlocks
        unlocked = witness.tlcs[witness.unlocks[0].unlock_type]
        assert (
            unlocked.payment_hash == bytes.fromhex(target_hash.removeprefix("0x"))[:20]
        ), f"确认消费必须精确指向目标 TLC: {unlocked}"
        # 链上方向位是结算快照里 tlc_id 的 is_offered()，快照方向取决于“谁结算谁的承诺”：
        # 本例由对端（该 TLC 的收款方）结算受害端的承诺，fiber-lib tracked_settlement_tlcs 在
        # for_remote=true 时不取 flip（见其单测 test_tracked_settlement_tlcs_extraction），
        # 快照 id 即对端本地的 Received，因此 bit0=1。真正证明"解锁的是精确目标条目"的是上面的
        # payment_hash 断言，这里只额外固定方向位，避免把别的条目当成付款目标。
        assert (
            unlocked.tlc_type & 1 == 1
        ), f"对端结算受害端承诺时条目方向位应为 Received(1): {unlocked}"
        remaining = [tlc.payment_hash for tlc in witness.tlcs]
        # 证明点 5：兄弟条目仍在承诺快照里但未被解锁，因此不得被这次消费顺带处理。
        assert bytes.fromhex(other_hash.removeprefix("0x"))[:20] in remaining, remaining

        # 证明点 4：The received target TLC is closed as a failed consumption and never fulfilled.
        self.wait_payment_state(payer, target_hash, "Failed", timeout=660)
        failed = payer.get_client().get_payment({"payment_hash": target_hash})
        assert failed.get("payment_preimage") is None, failed
        # 本端 inbound（接收侧）与 outbound（转发出去）两侧的目标 TLC 都按失败移除，
        # 证明收尾发生在 received 侧而不是只清掉出站记录。
        self._wait_tlc_removed(victim, target_hash, "Inbound", upstream_id, timeout=660)
        self._wait_tlc_removed(
            victim, target_hash, "Outbound", downstream_id, timeout=660
        )

        # 证明点 5：The unrelated received TLC on the same upstream channel keeps its own state.
        # 兄弟在上游与下游都未被移除，其付款仍 Inflight 且无原像，对端发票保持 Received：
        # 同前缀的无关 TLC 没有被这次链上消费错误 fulfill 或 fail。
        self._assert_not_removed(victim, other_hash, "Inbound", upstream_id)
        self._assert_not_removed(victim, other_hash, "Outbound", downstream_id)
        other_payment = payer.get_client().get_payment({"payment_hash": other_hash})
        assert other_payment["status"] == "Inflight", other_payment
        assert other_payment.get("payment_preimage") is None, other_payment
        other_invoice = counterparty.get_client().get_invoice(
            {"payment_hash": other_hash}
        )
        assert other_invoice["status"] == "Received", other_invoice

    # --------------------------------------------------------------- H32V2-17

    # TEST-MAP: H32V2-17
    # TEST-EVIDENCE-BEGIN: H32V2-17
    # Evidence | covered | Legacy channel, one same-20-byte-prefix pair (A has the real
    # full-hash-correct preimage, B has none, distinct TLC ids). A is settled on chain through the
    # receiver's watchtower: the strict 85-byte/20-byte Legacy witness lists both TLCs and unlocks
    # only A's entry. Covered outcomes: A's payment becomes Success with the correct preimage
    # (full-hash-correct old record), while bounded watchtower rounds leave B's TLC unremoved, B's
    # payment Inflight with no preimage and B's invoice Received (the prefix-shared record must not
    # resolve the sibling in either direction). The prefix-store branches with no preimage / bad
    # preimage / only a locally-known preimage are not observable through any RPC, see module
    # docstring.
    # TEST-EVIDENCE-END: H32V2-17
    # H32V2-17 评审行（reviews/full-payment-hash-settlement-v2.md，SPEC-11）覆盖多个分支，
    # 本方法对应其中的“完整 hash 正确则可 fulfill”与“同前缀其他目标不被误处理”两项：
    #   场景：同通道内两笔 TLC 前 20 字节相同但完整 hash 不同；分别只有旧 prefix-keyed 记录。
    #   预期：旧记录只有完整 hash 正确时才可 fulfill，同前缀的其他目标不被成功或失败处理。
    #   防止：为消除悬挂而误杀未被精确消费的 TLC。
    # 证明点与断言的对应关系：
    #   证明点 1（前缀碰撞前提成立）：hash_a 与 hash_b 完整值不同、20 字节前缀相同，且链上两条
    #     目 id 不同，排除“同一笔 TLC 被核对两次”的替代解释。
    #   证明点 2（版本前提）：本端按 57 字节 Legacy 承诺强关，20 字节前缀匹配语义才有意义。
    #   证明点 3（精确身份）：链上消费只解锁 A 这一条，B 虽在承诺快照里却未被解锁；若实现仅按
    #     20 字节前缀匹配，两条目都会命中，A 的消费可能兑付 B。
    #   证明点 4（完整 hash 正确即成功）：A 付款 Success 且返回的原像就是真实 preimage_a，
    #     对端发票 Paid —— fulfill 由完整 hash 正确性决定。
    #   证明点 5（同前缀目标存续）：B 在多轮有界 watchtower 扫描后仍未被 fulfill 或 fail，
    #     其付款仍 Inflight、无原像，对端发票仍 Received。
    # 未覆盖：模块 docstring 已说明——只有空前缀记录／坏原像／仅本地已知原像、身份或算法不符
    # 等分支需要读取 watchtower 持久化的原像库，当前 RPC 没有该观测点；Exact 无原像的到期
    # 对照见下一个方法（test_exact_no_preimage_offered_tlc_waits_for_expiry）。
    def test_same_prefix_sibling_survives_confirmed_claim(self):
        self._wait_channel_ready(self.victim, self.channel_id)
        self._wait_channel_ready(self.attacker, self.channel_id)

        # 证明点 1：构造一对“前 20 字节相同、完整 32 字节不同”的支付 hash。
        preimage_a = "0x" + secrets.token_hex(32)
        hash_a = ckb_hash(preimage_a)
        hash_b = same_twenty_byte_prefix_hash(hash_a)
        assert hash_a != hash_b, (hash_a, hash_b)
        assert hash_a[:42] == hash_b[:42], (hash_a, hash_b)

        invoice_a = self._hold_invoice_with_algorithm(
            hash_a,
            "ckb_hash",
            "H32V2-17 sibling A with the real full-hash-correct preimage",
        )
        invoice_b = self._hold_invoice_with_algorithm(
            hash_b, "ckb_hash", "H32V2-17 sibling B without any preimage"
        )
        self._pay_hold_invoice(invoice_a, hash_a)
        self._pay_hold_invoice(invoice_b, hash_b)

        tlc_a = self._wait_committed_tlc(self.victim, hash_a)
        tlc_b = self._wait_committed_tlc(self.victim, hash_b)
        self._wait_committed_tlc(self.attacker, hash_a)
        self._wait_committed_tlc(self.attacker, hash_b)
        # 证明点 1：两条目是通道里各自独立的已承诺 TLC，不是同一笔的记录重复。
        assert tlc_a["id"] != tlc_b["id"], (tlc_a, tlc_b)
        assert hash_a[:42] == hash_b[:42] and hash_a != hash_b

        commitment = self._force_close(self.victim)
        args = bytes.fromhex(
            commitment["outputs"][0]["lock"]["args"].removeprefix("0x")
        )
        # 证明点 2：57 字节 args 确认是 Legacy 承诺，witness 才按 20 字节 hash 解析。
        assert len(args) == 57, f"Legacy 通道承诺 args 必须 57 字节，实际 {len(args)}"
        # 结算受承诺延迟约束：与 H32V2-14 / settle_held_channel 同一口径，先推进一个 epoch，
        # 对端才会构造并广播结算（少了这一步交易池始终为空）。
        self.ckb.generate_epochs("0x1", wait_time=0)
        self._mine_watchtower_rounds(1)

        # 证明点 3：Only A has a real preimage, so only A can be consumed on chain.
        # 这里用对端自己结算（settle_invoice）触发 watchtower 发链上结算，不手工改 witness。
        self.attacker.get_client().settle_invoice(
            {"payment_hash": hash_a, "payment_preimage": preimage_a}
        )
        claim = self._wait_spend(
            {"tx_hash": commitment["hash"], "index": "0x0"}, "A 的完整原像链上消费"
        )
        witness = SettlementWitness.from_hex(claim["witnesses"][0], version="legacy")
        # 消费证据列出 A 与 B 两条（含金额），证明 B 确实在同一个承诺快照里。
        witness.assert_pending_tlcs(
            [(hash_a, PAYMENT_AMOUNT), (hash_b, PAYMENT_AMOUNT)]
        )
        assert len(witness.unlocks) == 1, witness.unlocks
        # 唯一解锁必须指向 A 的 20 字节前缀；若实现只按前缀匹配，这条断言把歧义暴露出来。
        assert (
            witness.tlcs[witness.unlocks[0].unlock_type].payment_hash
            == bytes.fromhex(hash_a.removeprefix("0x"))[:20]
        ), witness.tlcs

        # 证明点 4：A 的完整 hash 与原像相符，必须按真实原像兑现成功。
        self.wait_payment_state(self.victim, hash_a, "Success", timeout=300)
        settled = self.victim.get_client().get_payment({"payment_hash": hash_a})
        assert settled["payment_preimage"] == preimage_a, settled
        self.wait_invoice_state(self.attacker, hash_a, "Paid", timeout=120)

        # 证明点 5：B 与 A 共享 20 字节前缀但不得被这次消费影响。
        # _assert_sibling_survives 在多轮 _mine_watchtower_rounds 中重复核对：B 的 TLC 记录
        # 仍存在、非终态、状态不在 REMOVED_STATUSES，其付款仍 Inflight 且无原像，发票仍
        # Received —— 即没有被错误 fulfill，也没有被错误 fail。
        self._assert_sibling_survives(hash_b)

    # TEST-MAP: H32V2-17
    # TEST-EVIDENCE-BEGIN: H32V2-17
    # Evidence | covered | Legacy channel, one exact offered TLC whose full 32-byte hash is known
    # but for which no preimage exists. The victim force-closes its own commitment (on-chain close
    # reconciliation). Before the expiry: chain median time is still below the TLC expiry, the
    # payment stays Inflight and the TLC stays present through bounded watchtower rounds (no
    # immediate failure from the missing preimage). After advancing the chain clock past the
    # expiry: the payment becomes Failed with no preimage and the offered TLC is closed (SETTLE-04
    # timeout control). Prefix-store/algorithm-mismatch branches are unobservable, see docstring.
    # TEST-EVIDENCE-END: H32V2-17
    # H32V2-17 评审行（reviews/full-payment-hash-settlement-v2.md，SPEC-11）在本方法对应
    # “普通无原像超时保留到期条件”这一项，作为上一个方法的对照：
    #   场景：只有一笔 Exact 身份、完整 hash 已知但没有任何原像的普通 TLC。
    #   预期：无原像不得触发本次新增的立即失败，必须继续遵守 TLC 到期条件，到期后才失败。
    #   防止：把“没有原像”当成“坏原像精确消费”，把未被消费的 TLC 误杀。
    # 证明点与断言的对应关系：
    #   证明点 1（前提）：TLC 已 Committed 且本端按 57 字节 Legacy 承诺强关，进入链上关闭核对。
    #   证明点 2（到期前不得提前失败）：链中位时间与本地时钟都在到期之前，连续多轮有界
    #     watchtower 扫描中付款保持 Inflight、无原像、TLC 记录仍在 —— 缺失原像本身不是失败证据。
    #   证明点 3（到期后按超时路径收尾）：把链上时钟推过到期后，付款才 Failed 且无原像，
    #     offered TLC 才被移除；说明收尾由到期条件而非原像库状态驱动。
    # 未覆盖：同上前一个方法——空前缀记录／坏原像／仅本地已知原像、身份或算法不符等分支缺少
    # watchtower 原像库的 RPC 观测点，见模块 docstring。
    def test_exact_no_preimage_offered_tlc_waits_for_expiry(self):
        self._wait_channel_ready(self.victim, self.channel_id)
        self._wait_channel_ready(self.attacker, self.channel_id)

        # 证明点 1 的素材：完整 32 字节 hash 由已知原像算出，但本端从不公开/注入该原像。
        preimage = "0x" + secrets.token_hex(32)
        payment_hash = ckb_hash(preimage)
        invoice = self._hold_invoice_with_algorithm(
            payment_hash,
            "ckb_hash",
            "H32V2-17 exact no-preimage offered timeout control",
        )
        self._pay_hold_invoice(invoice, payment_hash)
        offered = self._wait_committed_tlc(self.victim, payment_hash)
        expiry_ms = int(offered["expiry"], 16)

        commitment = self._force_close(self.victim)
        args = bytes.fromhex(
            commitment["outputs"][0]["lock"]["args"].removeprefix("0x")
        )
        # 证明点 1：57 字节 args 确认 Legacy 承诺；此后进入链上关闭核对。
        assert len(args) == 57, f"Legacy 通道承诺 args 必须 57 字节，实际 {len(args)}"
        self._mine_watchtower_rounds(2)

        # 证明点 2：Before expiry: the exact TLC is on chain but has no preimage evidence, so the
        # payment must keep waiting instead of failing early.
        tip = self.node.getClient().get_tip_header()
        assert (
            int(self.node.getClient().get_block_median_time(tip["hash"]), 16)
            < expiry_ms
        ), "对照必须在 TLC 到期前观察"
        for _ in range(6):
            self._mine_watchtower_rounds(1)
            payment = self.victim.get_client().get_payment(
                {"payment_hash": payment_hash}
            )
            assert payment["status"] == "Inflight", f"到期前不得提前失败: {payment}"
            assert payment.get("payment_preimage") is None, payment
            assert self._tlcs(self.victim, payment_hash), "到期前 TLC 不得被移除"
        assert int(time.time() * 1000) < expiry_ms, "对照必须在 TLC 到期前完成"

        # 证明点 3：Past the expiry: only the normal timeout path may resolve it.
        # _clock_advanced 让 teardown_method 在方法结束后恢复系统时间，避免污染同类其他方法。
        self.__class__._clock_advanced = True
        self.add_time_and_generate_epoch(self._hours_past_expiry(offered), 1)
        self._mine_watchtower_rounds(4)
        self.wait_payment_state(self.victim, payment_hash, "Failed", timeout=300)
        after = self.victim.get_client().get_payment({"payment_hash": payment_hash})
        assert after.get("payment_preimage") is None, after
        # _wait_tlc_removed_after_expiry 接受终态或 REMOVED_STATUSES，专门覆盖超时收尾路径。
        self._wait_tlc_removed_after_expiry(
            self.victim, payment_hash, "Outbound", self.channel_id, timeout=180
        )

    # --------------------------------------------------------------- H32V2-18

    # TEST-MAP: H32V2-18
    # H32V2-18 评审行（reviews/full-payment-hash-settlement-v2.md，SPEC-12）：
    #   场景：Legacy 异常原像花费已确认且付款无重试机会，在失败通知完成前重启本端；
    #         保留无关未结算 TLC。
    #   预期：恢复后完成原 TLC 失败通知和移除、付款最终 Failed；已处理目标不重复计账或重新挂起，
    #         无关 TLC 保持自身状态。
    #   防止：崩溃丢失失败通知、重复计账或误结算其他 TLC。
    # 本方法走“通知完成前重启”分支；“处理完成后重启”分支见下一个方法
    # （test_repeated_scan_while_alive_keeps_unrelated_tlc，用反复扫描近似）。
    # 证明点与断言的对应关系：
    #   证明点 1（场景成立）：目标（仅前缀坏原像）+ 无关两笔 TLC 都已 Committed、发票 Received、
    #     付款 Inflight，且强关承诺 57 字节确认是 Legacy 布局。
    #   证明点 2（崩溃点正确）：链上消费确认前付款必须仍 Inflight；停矿后注入原像，确保消费
    #     不会在进程宕机期间被挖出。
    #   证明点 3（证据本身是精确前缀消费）：_wait_spend 找到已 committed 的花费，且其输入是
    #     刚强关的承诺 outpoint；_assert_prefix_only_claim 证明原像完整 hash 与 invoice hash
    #     不等、只有 20 字节前缀相同。
    #   证明点 4（重启前的失败通知不可能已完成）：用 socket 断言 victim RPC 在消费确认时确实
    #     不可达，排除“其实已经通知完再重启”的替代解释。
    #   证明点 5（恢复后按失败收尾）：重启后付款 Failed、无原像、在记录到期前，目标 TLC 终态。
    #   证明点 6（不重复计账、不重新挂起）：_assert_failed_without_recovery 在多轮有界扫描后
    #     复核 Failed、无原像、fee 不变、本端钱包容量不变；无关 TLC 仍在、非终态、付款仍
    #     Inflight —— 已处理目标不重复计账，无关 TLC 未被误结算。
    # 未覆盖：模块末条 TEST-EVIDENCE 所述——“通知完成后才重启”的第二个重启点未单独执行；
    #   崩溃是停矿后的干净 stop，不是撕裂写；多笔无关 TLC、V1 布局、xUDT、完全 Closed 后重启。
    def test_restart_before_failure_notification_rescans_confirmed_claim(self):
        bad_hash, preimage, unrelated_hash, expiry = (
            self._open_target_and_unrelated_tlcs()
        )
        # The automatic miner runs until the commitment is confirmed, then stops:
        # the prefix-only claim must not be mined while the victim is down, and the
        # failure notification must not complete before the crash.
        commitment = self._force_close_attacker()
        self.ckb.generate_epochs("0x1", wait_time=0)
        # 证明点 2：链上消费确认之前，本端不得提前把目标付款判死。
        assert (
            self.victim.get_client().get_payment({"payment_hash": bad_hash})["status"]
            == "Inflight"
        ), "the target payment must not resolve before the on-chain claim confirms"
        self.victim.stop()
        self.node.stop_miner()
        try:
            # 证明点 2：进程已停、矿工已停，此时注入原像再显式挖块，消费确认时本端必然不在线。
            self._inject_prefix_preimage(bad_hash, preimage)
            spend = self._wait_spend(
                {"tx_hash": commitment["hash"], "index": "0x0"},
                label="commitment cell",
            )
            # 证明点 3：消费必须是已 committed 的链上交易，而不是仍留在池中。
            assert (
                self.ckb.get_transaction(spend["hash"])["tx_status"]["status"]
                == "committed"
            ), spend
            self._assert_prefix_only_claim(
                spend,
                bad_hash,
                preimage,
                [
                    (bad_hash, PAYMENT_AMOUNT),
                    (unrelated_hash, UNRELATED_AMOUNT),
                ],
            )
            # The honest process was down before the claim was mined: the failure
            # notification cannot have completed, so the restart must replay the
            # same confirmed spend.
            # 证明点 4：崩溃点由端口探测证明，而不是靠时间先后假设。
            with socket.socket() as sock:
                assert (
                    sock.connect_ex(("127.0.0.1", int(self.victim.rpc_port))) != 0
                ), "victim RPC must be down while the prefix-only claim confirms"
        finally:
            self.victim.start(fnn_log_level=self.fnn_log_level)
            self.node.start_miner()
        # 证明点 5/6：重启后恢复处理该已确认花费，且不重复计账、不重新挂起无关 TLC。
        self._assert_failed_without_recovery(bad_hash, unrelated_hash, expiry)

    # TEST-MAP: H32V2-18
    # H32V2-18 的“处理完成后重启”分支：本变体让本端全程在线，用反复扫描同一已确认花费近似
    # “失败通知完成后重启再重复扫描”，重点证明幂等：
    #   预期：已处理目标不重复计账或重新挂起，付款终态及余额稳定，无关 TLC 不受影响。
    # 证明点与断言的对应关系：
    #   证明点 1（场景成立）：同 _open_target_and_unrelated_tlcs，两笔 TLC 已 Committed、
    #     发票 Received、付款 Inflight。
    #   证明点 2（进入处理前仍 Inflight）：链上消费确认前本端不得提前判死。
    #   证明点 3（证据精确）：消费输入是刚强关的承诺 outpoint；原像完整 hash 与 invoice hash
    #     不等、仅 20 字节前缀相同，且条目集合恰为目标加无关两笔。
    #   证明点 4（不是超时路径）：消费确认时刻早于记录的 TLC 到期，失败只能来自消费证据。
    #   证明点 5（反复扫描幂等）：_assert_failed_without_recovery 在多轮扫描后复核 Failed、
    #     无原像、fee 与钱包容量不变、目标 TLC 终态，同时无关 TLC 仍在且付款仍 Inflight。
    # 未覆盖：与上一个方法相同——本变体不做重启，因此不能替代“通知完成后重启”的独立执行；
    #   多笔无关 TLC、V1 布局、xUDT 亦未覆盖（见模块末条 TEST-EVIDENCE 的 partial/not covered）。
    def test_repeated_scan_while_alive_keeps_unrelated_tlc(self):
        bad_hash, preimage, unrelated_hash, expiry = (
            self._open_target_and_unrelated_tlcs()
        )
        commitment = self._force_close_attacker()
        self.ckb.generate_epochs("0x1", wait_time=0)
        # 证明点 2：链上消费确认之前，付款必须仍 Inflight。
        assert (
            self.victim.get_client().get_payment({"payment_hash": bad_hash})["status"]
            == "Inflight"
        ), "the target payment must not resolve before the on-chain claim confirms"
        self._inject_prefix_preimage(bad_hash, preimage)
        spend = self._wait_spend(
            {"tx_hash": commitment["hash"], "index": "0x0"}, label="commitment cell"
        )
        # 证明点 3：确认的消费是精确指向目标条目的仅前缀消费，无关条目未被解锁。
        self._assert_prefix_only_claim(
            spend,
            bad_hash,
            preimage,
            [(bad_hash, PAYMENT_AMOUNT), (unrelated_hash, UNRELATED_AMOUNT)],
        )
        # 证明点 4：消费在记录到期前确认，排除“失败来自超时”的解释。
        assert (
            int(time.time() * 1000) < expiry
        ), f"claim confirmed after the recorded TLC expiry {expiry}"
        # 证明点 5：反复扫描不重复计账、不重新挂起，无关 TLC 保持自身状态。
        self._assert_failed_without_recovery(bad_hash, unrelated_hash, expiry)

    # TEST-EVIDENCE-BEGIN H32V2-18
    # covered: one crafted-hash target plus one unrelated hold TLC in the same
    #   channel. (a) restart variant: automatic mining stopped, victim process
    #   stopped before the prefix-only claim was mined and restarted after the
    #   claim confirmed, then automatic mining resumed and the same confirmed spend
    #   replayed. (b) alive variant: the victim stays up through confirmation and
    #   repeated watchtower scans. In both: target payment Failed with
    #   payment_preimage null before the recorded expiry, target TLC terminal, fee
    #   and wallet capacity flat across three further scans, and the unrelated
    #   TLC present, non-terminal, status unchanged, with its payment still
    #   Inflight.
    # partial: the row's "restart after the failure notification completed" is
    #   only approximated by the alive variant's repeated scans, which never
    #   restart; a second restart point is not exercised. The crash is a clean
    #   stop before mining, not a torn store write.
    # not covered: multiple unrelated TLCs; V1 commitment layout; UDT (xUDT); restart
    #   after the channel has already reached fully Closed.
    # TEST-EVIDENCE-END H32V2-18
