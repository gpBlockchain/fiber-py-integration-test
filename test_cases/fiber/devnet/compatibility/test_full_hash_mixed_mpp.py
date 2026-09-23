"""H32V2-28..31: mixed V1/Legacy commitment versions in MPP and long paths.

Layout facts used throughout (SPEC-01/04): the negotiated commitment version only
changes the ON-CHAIN layout.

* V1     = 58-byte commitment lock args ending in ``0x01``, 97-byte settlement
           TLC entry, full 32-byte payment hash.
* Legacy = 57-byte args, 85-byte TLC entry, 20-byte payment-hash prefix.

Off-chain forwarding and MPP aggregation are version-agnostic: a payee can take
one part over a V1 channel and another over a Legacy channel, and a relay only
has to read the layout of the channel the TLC actually lives on. The topology is
derived from the framework's own node configuration and then verified on chain,
never assumed:

* head nodes   = ``FiberConfigPath.CURRENT_DEV``    -> announce the full-hash feature
* legacy nodes = ``FiberConfigPath.V091_DEV``       -> announce nothing -> Legacy
* the adversary = ``FiberConfigPath.ATTACK_FULL_HASH_DEV`` started with
  ``LEGACY_COUNTERPARTY_ENV`` -> announces nothing -> Legacy

    head <-> head            = V1
    head <-> legacy/adversary = Legacy

The negotiated version is not exposed by any RPC. It is proven after a force
close by the commitment lock args length (58 + ``0x01`` vs 57) and, for a settled
TLC, by parsing the settlement witness with the matching entry width
(97 vs 85 bytes).

MPP splitting: fiber has no "multi-part explicit route" RPC -- ``build_router``
returns exactly ONE path (``router_hops``) and ``send_payment_with_router`` sends
ONE part. Deterministic per-channel amount assignment is therefore NOT reachable.
These suites constrain the capacities so the only two/multi-path choices are the
intended channels, then read the split off the per-channel TLC observations. The
amount that landed on each channel is asserted from that observation, never
assumed; a router that does not produce the intended split fails loudly with the
observed per-channel state.

Two classes share this file: the same-hash MPP cases H32V2-28/29 need only head +
``V091_DEV`` nodes, while H32V2-30/31 need the instrumented full-payment-hash
counterparty and are therefore isolated in a second class carrying
``@requires_full_hash_attack_fnn`` and its own ports. A single class cannot mix
those markers without starting the adversary for the plain methods too.
"""

import hashlib
import socket
import subprocess
import time

import pytest

from framework.attack_fnn import LEGACY_COUNTERPARTY_ENV, requires_full_hash_attack_fnn
from framework.helper.settlement_witness import (
    SettlementWitness,
    assert_commitment_args,
    tlc_entry_size,
    witness_size,
)
from framework.onchain_tlc_query import onchain_tlc_query_enabled
from framework.test_fiber import FiberConfigPath
from framework.util import ckb_hash
from test_cases.fiber.devnet.compatibility.contract_upgrade_support import (
    CKB,
    ROOT,
    FullHashChannelSupport,
    tlc_is_terminal,
)

WATCHTOWER_INTERVAL = 2
FINAL_EXPIRY_DELTA = 86_400_000
# H32V2-28/29: one direct V1 channel that is too small for the whole invoice plus
# one Legacy 2-hop branch, so the router must split over exactly those channels.
MPP_AMOUNT = 1500 * CKB
MPP_DIRECT_CAPACITY = 600 * CKB
MPP_BRIDGE_CAPACITY = 1000 * CKB
# H32V2-31: three intended parts -- direct V1, adversary Legacy branch, honest
# Legacy branch -- each branch capped below the invoice amount.
MIXED_AMOUNT = 2500 * CKB
MIXED_DIRECT_CAPACITY = 600 * CKB
MIXED_BRANCH_CAPACITY = 1000 * CKB
# H32V2-30: three-hop route, every hop capped at the same value so the amount is
# carried end-to-end by the single explicit route. H32V2-32 复用同一常量。
LONG_PATH_AMOUNT = 1 * CKB
LONG_PATH_CAPACITY = 200 * CKB
# H32V2-32: “仍在进行中”的 TLC 状态名。付款一旦被链上证据推动，这些名字必须全部消失
# （无论走成功还是撤销路径），因此不能把某个具体终态名写死成成功路径的唯一证据。
COMMITTED_STATUSES = {"LocalAnnounced", "Committed", "RemoteRemoved"}


class _MixedVersionMppSupport(FullHashChannelSupport):
    """Shared helpers only; no test methods live here."""

    # ------------------------------------------------------------------ topology

    def _start_extra(self, attribute, version, env=None, balance=10000):
        """Start an extra node once per class; lifecycle stays class-level.

        ``generate_account`` / ``start_new_fiber`` are instance methods, so this
        must never be called from ``setup_class`` -- every caller is a test body
        or ``setUp``.
        """
        cls = self.__class__
        fiber = getattr(cls, attribute, None)
        if fiber is None:
            kwargs = {"fiber_version": version}
            if env is not None:
                kwargs["env"] = env
            fiber = self.start_new_fiber(self.generate_account(balance), **kwargs)
            setattr(cls, attribute, fiber)
        return fiber

    def _open_mixed_topology(self, payer, payee, legacy_bridge):
        """payer --(V1)--> payee plus payer --(Legacy)--> legacy_bridge --(Legacy)--> payee."""
        direct_id = self.open_channel(payer, payee, MPP_DIRECT_CAPACITY, 0)
        payer_bridge_id = self.open_channel(
            payer, legacy_bridge, MPP_BRIDGE_CAPACITY, 0
        )
        bridge_payee_id = self.open_channel(
            legacy_bridge, payee, MPP_BRIDGE_CAPACITY, 0
        )
        outpoints = [
            self._channel(payer, direct_id)["channel_outpoint"],
            self._channel(payer, payer_bridge_id)["channel_outpoint"],
            self._channel(legacy_bridge, bridge_payee_id)["channel_outpoint"],
        ]
        for fiber in (payer, payee, legacy_bridge):
            self._wait_graph_outpoints(fiber, outpoints)
        return direct_id, payer_bridge_id, bridge_payee_id

    def _wait_graph_outpoints(self, fiber, outpoints, timeout=180):
        wanted = {outpoint for outpoint in outpoints if outpoint}
        assert len(wanted) == len(outpoints), outpoints
        deadline = time.monotonic() + timeout
        last = []
        seen = set()
        while time.monotonic() < deadline:
            last = fiber.get_client().graph_channels().get("channels") or []
            seen = {channel.get("channel_outpoint") for channel in last}
            if wanted <= seen:
                return
            time.sleep(1)
        self.fail(
            f"graph on {fiber.rpc_port} never advertised {sorted(wanted - seen)} "
            f"within {timeout}s; last graph_channels={last}"
        )

    # ------------------------------------------------------------------- reading

    def _channel(self, fiber, channel_id, include_closed=True):
        channels = fiber.get_client().list_channels({"include_closed": include_closed})[
            "channels"
        ]
        for channel in channels:
            if channel["channel_id"] == channel_id:
                return channel
        self.fail(
            f"channel {channel_id} not found on {fiber.tmp_path}; "
            f"known={[c['channel_id'] for c in channels]}"
        )

    @staticmethod
    def _funding_tx(channel):
        outpoint = channel.get("channel_outpoint")
        assert outpoint is not None, channel
        raw = bytes.fromhex(outpoint.removeprefix("0x"))
        assert len(raw) == 36, outpoint
        assert int.from_bytes(raw[32:], "little") == 0, outpoint
        return "0x" + raw[:32].hex()

    def _tlc_in(self, fiber, channel_id, payment_hash):
        return [
            tlc
            for tlc in (self._channel(fiber, channel_id).get("pending_tlcs") or [])
            if tlc.get("payment_hash") == payment_hash
        ]

    def _wait_until(self, predicate, description, timeout=180, interval=1):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            last = predicate()
            if last:
                return last
            time.sleep(interval)
        self.fail(f"timed out waiting for {description}; last observed={last}")

    def _wait_inbound_parts(self, fiber, channel_ids, payment_hash, timeout=180):
        """One Committed inbound MPP part per channel; returns {channel_id: amount}."""

        def observed():
            parts = {}
            for channel_id in channel_ids:
                tlcs = self._tlc_in(fiber, channel_id, payment_hash)
                if len(tlcs) != 1:
                    return None
                if "Committed" not in tlcs[0]["status"].values():
                    return None
                parts[channel_id] = int(tlcs[0]["amount"], 16)
            return parts

        return self._wait_until(
            observed,
            f"one Committed MPP part per channel on {fiber.rpc_port} for {payment_hash}",
            timeout=timeout,
        )

    def _wait_outbound_parts(self, fiber, channel_ids, payment_hash, timeout=180):
        def observed():
            parts = {}
            for channel_id in channel_ids:
                tlcs = self._tlc_in(fiber, channel_id, payment_hash)
                if len(tlcs) != 1:
                    return None
                if "Committed" not in tlcs[0]["status"].values():
                    return None
                parts[channel_id] = int(tlcs[0]["amount"], 16)
            return parts

        return self._wait_until(
            observed,
            f"one Committed outbound MPP part per channel on {fiber.rpc_port} for {payment_hash}",
            timeout=timeout,
        )

    # ------------------------------------------------------------ chain plumbing

    def _force_close_commitment(self, fiber, channel_id):
        """Force close and return ``(funding_tx_hash, commitment_tx)``."""
        channel = self._channel(fiber, channel_id)
        funding_tx = self._funding_tx(channel)
        fiber.get_client().shutdown_channel({"channel_id": channel_id, "force": True})
        tx = self.wait_for_spend(funding_tx)
        self.wait_for_channel_state(
            fiber.get_client(),
            channel["pubkey"],
            "Closed",
            include_closed=True,
            channel_id=channel_id,
        )
        return funding_tx, tx

    def _assert_commitment_layout(self, commitment, version):
        lock = commitment["outputs"][0]["lock"]
        args = bytes.fromhex(lock["args"].removeprefix("0x"))
        assert_commitment_args(args, version)
        return args

    def _parse_settlement(self, transaction, version, pending):
        """Parse a settlement witness and pin the entry width for that version."""
        assert transaction.get("witnesses"), transaction
        witness = SettlementWitness.from_hex(
            transaction["witnesses"][0], version=version
        )
        assert (
            witness.to_hex() == transaction["witnesses"][0]
        ), f"{version} settlement witness did not round-trip"
        witness.assert_pending_tlcs(pending)
        raw = bytes.fromhex(transaction["witnesses"][0].removeprefix("0x"))
        expected = witness_size(version, len(witness.tlcs))
        assert len(raw) == expected, (
            f"{version} settlement witness is {len(raw)} bytes, expected {expected} "
            f"({tlc_entry_size(version)}-byte TLC entries): {witness.tlcs}"
        )
        return witness

    def _wait_spend(self, outpoint, label, timeout=180):
        """Return the committed transaction that spends ``outpoint``."""
        spent = self.ckb.get_transaction(outpoint["tx_hash"])["transaction"]
        lock = spent["outputs"][int(outpoint["index"], 16)]["lock"]
        search_key = {
            "script": lock,
            "script_type": "lock",
            "script_search_mode": "exact",
        }
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            last = self.ckb.get_raw_tx_pool()
            for view in ("pending", "proposed"):
                for tx_hash in list(last.get(view) or []):
                    result = self.ckb.get_transaction(tx_hash)
                    if result["tx_status"]["status"] not in ("pending", "proposed"):
                        continue
                    if any(
                        item["previous_output"] == outpoint
                        for item in result["transaction"]["inputs"]
                    ):
                        self.Miner.miner_until_tx_committed(self.node, tx_hash)
                        return self.ckb.get_transaction(tx_hash)["transaction"]
            for item in self.ckb.get_transactions(search_key, "asc", "0xff", None)[
                "objects"
            ]:
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
        self.fail(f"{label} {outpoint} was not spent within {timeout}s; pool={last}")

    def _mine_watchtower_rounds(self, rounds=4):
        interval = self.start_fiber_config["fiber_watchtower_check_interval_seconds"]
        deadline = time.monotonic() + interval * rounds + 2
        while time.monotonic() < deadline:
            pending = list(self.ckb.get_raw_tx_pool().get("pending") or [])
            if pending:
                self.Miner.miner_until_tx_committed(self.node, pending[0])
            else:
                self.Miner.miner_with_version(self.node, "0x0")
            time.sleep(1)

    def _chain_median_time(self):
        tip = self.ckb.get_tip_header()
        return int(self.ckb.get_block_median_time(tip["hash"]), 16)

    def _restart(self, fiber, peers):
        fiber.start(fnn_log_level=self.fnn_log_level)
        for peer in peers:
            fiber.connect_peer(peer)
        time.sleep(2)

    # ------------------------------------------------------- adversary plumbing

    def _craft_prefix_hash(self):
        """A 32-byte hash sharing only the first 20 bytes with sha256(preimage)."""
        preimage = self.generate_random_preimage()
        digest = hashlib.sha256(bytes.fromhex(preimage.removeprefix("0x"))).digest()
        bad_hash = "0x" + (digest[:20] + bytes(byte ^ 1 for byte in digest[20:])).hex()
        assert bad_hash != "0x" + digest.hex(), bad_hash
        return preimage, bad_hash

    def _inject_prefix_preimage(self, adversary, payment_hash, preimage):
        adversary.get_client().call(
            "create_preimage",
            [{"payment_hash": payment_hash, "preimage": preimage, "force": True}],
        )

    def _assert_exact_tlc_claim(
        self, spend, payment_hash, preimage, pending, expect_full_hash_match=False
    ):
        """核对一笔 Legacy 链上消费精确指向清单里的目标 TLC 并解锁它。

        ``expect_full_hash_match=False``（H32V2-30）：invoice hash 是坏 hash，原像摘要只匹配
        其 20 字节前缀、完整 32 字节不相等。
        ``expect_full_hash_match=True``（H32V2-32）：invoice hash 就是 sha256(preimage)，
        完整 32 字节相等 —— 两种输入共用同一套“精确身份”断言，差别只在最后一处 hash 比较。
        """
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
        assert unlock.preimage == bytes.fromhex(preimage.removeprefix("0x")), unlock
        digest = hashlib.sha256(bytes.fromhex(preimage.removeprefix("0x"))).digest()
        assert claimed.payment_hash == digest[:20], claimed
        invoice_hash = bytes.fromhex(payment_hash.removeprefix("0x"))
        if expect_full_hash_match:
            assert digest == invoice_hash, (
                "the full-hash control must claim with a preimage whose complete "
                f"32-byte digest equals the invoice hash: {digest.hex()} != {invoice_hash.hex()}"
            )
        else:
            assert digest != invoice_hash, (
                "the committed spend must use a preimage whose full hash differs from "
                "the invoice hash; only the 20-byte Legacy prefix matches"
            )
        return witness

    def _hold_mpp_invoice(self, payee, payment_hash, amount, algorithm, description):
        return payee.get_client().new_invoice(
            {
                "amount": hex(amount),
                "currency": "Fibd",
                "description": description,
                "payment_hash": payment_hash,
                "hash_algorithm": algorithm,
                "expiry": "0xe10",
                "final_cltv": "0x28",
                "allow_mpp": True,
            }
        )

    def _hold_single_invoice(self, payee, payment_hash, amount, algorithm, description):
        return payee.get_client().new_invoice(
            {
                "amount": hex(amount),
                "currency": "Fibd",
                "description": description,
                "payment_hash": payment_hash,
                "hash_algorithm": algorithm,
                "final_expiry_delta": hex(FINAL_EXPIRY_DELTA),
            }
        )


class TestMixedVersionMpp(_MixedVersionMppSupport):
    """H32V2-28/29: mixed-version MPP over head + V091_DEV nodes only."""

    ckb_rpc_port, ckb_p2p_port = 25814, 25815
    fiber1_rpc_port, fiber1_p2p_port = 25828, 25827
    fiber2_rpc_port, fiber2_p2p_port = 25829, 25830
    extra_fiber_rpc_port, extra_fiber_p2p_port = 25900, 26000
    start_fiber_config = {
        "fiber_watchtower_check_interval_seconds": WATCHTOWER_INTERVAL
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
            cls.extra_fiber_rpc_port + 2,
            cls.extra_fiber_p2p_port + 2,
            cls.extra_fiber_rpc_port + 3,
            cls.extra_fiber_p2p_port + 3,
        ):
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", port))
        # The Legacy layout is fixed by the PR-base binary; the mixed-version
        # cases are only meaningful against that exact build.
        assert "9a561b3" in subprocess.check_output(
            [ROOT / FiberConfigPath.V091_DEV.fiber_bin_path, "--version"], text=True
        )
        super().setup_class()
        cls.ckb = cls.node.getClient()

    def setUp(self):
        # generate_account/start_new_fiber are instance methods: the Legacy bridge
        # is started lazily here, once per class.
        self._start_extra("legacy_bridge", FiberConfigPath.V091_DEV)

    # ------------------------------------------------------------------ H32V2-28

    # TEST-MAP: H32V2-28
    # H32V2-28 评审行（reviews/full-payment-hash-settlement-v2.md）：
    #   场景：MPP 发票的分片分别经 V1 与 Legacy 通道完成链下付款（同收款方、两种版本通道并存）。
    #   预期：收齐全部分片后收款端才公开原像，付款 Success 且原像正确；两条通道余额各按所属分片
    #         金额变动，不出现提前兑现或分片丢失。
    #   防止：混合版本破坏 MPP 聚合或提前公开原像。
    # 拓扑（payee 同时是两条路径的收款方）：
    #   payer(head) --V1--> payee(head)                        直连分片
    #   payer(head) --Legacy--> legacy_bridge(9a561b3) --Legacy--> payee   桥接分片
    #   发票金额 MPP_AMOUNT 大于任一条路径的单通道容量，所以路由必须拆成恰好两片。
    # 证明点与断言的对应关系：
    #   证明点 1（混合版本共存）：先建好三条通道且图同步可见；分片路径确实落在 V1 与 Legacy 两种
    #     通道上，而不是只走其中一版 —— 由分片所在通道集合和结尾的链上布局复核共同证明。
    #   证明点 2（必须真的拆成两片）：两片都 > 0 且金额之和等于发票金额；只走一条路径（另一片为 0）
    #     会直接失败，而不是静默通过。
    #   证明点 3（收齐前不提前兑现）：settle_invoice 之前两片在两条通道上都是 Committed，付款仍
    #     Inflight、payment_preimage 为空、发票仍是 Received —— 收款端没有提前公开原像。
    #   证明点 4（聚合完成后成功）：settle_invoice 之后付款 Success 且返回的原像等于测试持有的
    #     preimage，发票 Paid。
    #   证明点 5（余额各按所属分片变动、分片不丢）：收款端每条通道 local_balance 的增量恰好等于
    #     该通道收到的分片金额、且 received_tlc_balance 归零；付款端 offered_tlc_balance 清空；
    #     直连通道付款端支出等于收款端收入（收款方不付中转费），桥接通道付款端支出 >= 收款端收入
    #     （桥接费只抬高付款方的出账，不从收款方分片里扣）。
    #   证明点 6（版本未被破坏、可链上复核）：list_channels 不暴露协商版本，所以收尾时分别强关两条
    #     通道并按链上承诺 args 复核 —— 直连 58 字节且末位 0x01（V1）、桥接 57 字节（Legacy）。
    # 未覆盖：xUDT 资产、超过两片、重试、MPP 部分失败/重平衡分支；分片“具体金额”由路由决定，
    #   不由本测试固定（能固定的是通道归属：每条路径各自小于发票金额且只存在这两条路径）。
    def test_mixed_version_mpp_completes_offchain(self):
        payer, payee = self.fiber1, self.fiber2
        bridge = self.__class__.legacy_bridge
        # 证明点 1：建立“直连 V1 + 两跳 Legacy 桥接”的混合拓扑，并等三条通道都进图再发款。
        direct_id, payer_bridge_id, bridge_payee_id = self._open_mixed_topology(
            payer, payee, bridge
        )

        preimage = self.generate_random_preimage()
        payment_hash = ckb_hash(preimage)
        invoice = self._hold_mpp_invoice(
            payee, payment_hash, MPP_AMOUNT, "ckb_hash", "H32V2-28 mixed-version MPP"
        )
        # 证明点 5 的基线：先记下收款端两条通道、付款端两条通道的余额。
        payee_before = {
            channel_id: int(self._channel(payee, channel_id)["local_balance"], 16)
            for channel_id in (direct_id, bridge_payee_id)
        }
        payer_before = {
            channel_id: int(self._channel(payer, channel_id)["local_balance"], 16)
            for channel_id in (direct_id, payer_bridge_id)
        }

        # max_parts=2 限定最多两片；金额大于单通道容量，所以必须同时用这两条路径。
        payment = payer.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "max_parts": hex(2),
                "max_fee_rate": hex(10**15),
            }
        )
        assert payment["payment_hash"] == payment_hash, payment
        self.wait_payment_state(payer, payment_hash, "Inflight", timeout=60)
        self.wait_invoice_state(payee, payment_hash, "Received", timeout=120)
        # 证明点 1/2：分片金额从链下通道状态读取（不假设），两条通道各有一片。
        inbound = self._wait_inbound_parts(
            payee, (direct_id, bridge_payee_id), payment_hash, timeout=240
        )
        # A router that does not use both versions' channels is a failure of the
        # scenario, not a pass: no part may be zero and the two parts together are
        # the invoice amount.
        # 证明点 2：两片非零、合计等于发票金额 —— 混合版本没有破坏 MPP 聚合，也没有丢片。
        assert len(inbound) == 2, inbound
        assert all(amount > 0 for amount in inbound.values()), inbound
        assert sum(inbound.values()) == MPP_AMOUNT, inbound
        outbound = self._wait_outbound_parts(
            payer, (direct_id, payer_bridge_id), payment_hash, timeout=240
        )

        # Both parts are Committed on both channels and the invoice is only held:
        # the payee has not revealed anything yet.
        # 证明点 3：收齐两片后仍停留在 hold 状态，收款端尚未公开原像。
        for channel_id in (direct_id, bridge_payee_id):
            tlcs = self._tlc_in(payee, channel_id, payment_hash)
            assert len(tlcs) == 1 and "Committed" in tlcs[0]["status"].values(), tlcs
        held = payer.get_client().get_payment({"payment_hash": payment_hash})
        assert held["status"] == "Inflight", held
        assert held["payment_preimage"] is None, held
        assert (
            payee.get_client().get_invoice({"payment_hash": payment_hash})["status"]
            == "Received"
        )

        # 证明点 4：只有显式 settle 之后才公开原像并完成付款。
        payee.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )
        self.wait_payment_state(payer, payment_hash, "Success", timeout=360)
        result = payer.get_client().get_payment({"payment_hash": payment_hash})
        assert result["payment_preimage"] == preimage, result
        self.wait_invoice_state(payee, payment_hash, "Paid", timeout=360)

        # Each channel moved exactly by the part that landed on it. The payee pays
        # no forwarding fee; the bridge fee only inflates the payer's outbound
        # amount on the bridged channel.
        # 证明点 5：两条通道的余额各按自己那一片变动，没有串片、没有丢片。
        def payee_balances_match():
            return all(
                int(self._channel(payee, channel_id)["local_balance"], 16)
                == payee_before[channel_id] + inbound[channel_id]
                and int(self._channel(payee, channel_id)["received_tlc_balance"], 16)
                == 0
                for channel_id in (direct_id, bridge_payee_id)
            )

        self._wait_until(
            payee_balances_match,
            "the payee balances to move by exactly each part",
            timeout=120,
        )
        self._wait_until(
            lambda: all(
                int(self._channel(payer, channel_id)["offered_tlc_balance"], 16) == 0
                for channel_id in (direct_id, payer_bridge_id)
            ),
            "the payer offered-TLC balances to clear",
            timeout=120,
        )
        # 证明点 5：直连片两端金额相等（收款方不付中转费）；桥接片付款方出账不小于收款方入账
        # （差额是桥接费，且不得把费用算到收款方头上）。
        assert outbound[direct_id] == inbound[direct_id], (outbound, inbound)
        assert outbound[payer_bridge_id] >= inbound[bridge_payee_id], (
            outbound,
            inbound,
        )
        assert (
            payer_before[direct_id]
            - int(self._channel(payer, direct_id)["local_balance"], 16)
            == outbound[direct_id]
        ), (payer_before, outbound)

        # The negotiated versions are unchanged: force-closing each of the two
        # channels at the end proves V1 for the direct one and Legacy for the
        # bridged one (list_channels exposes no version).
        # 证明点 6：协商版本没有被混合场景破坏 —— 用链上承诺布局复核，而不是靠 RPC 自述。
        _, direct_commitment = self._force_close_commitment(payer, direct_id)
        self._assert_commitment_layout(direct_commitment, "v1")
        _, bridged_commitment = self._force_close_commitment(bridge, bridge_payee_id)
        self._assert_commitment_layout(bridged_commitment, "legacy")

    # TEST-EVIDENCE-BEGIN H32V2-28
    # covered: payer(head) --V1--> payee(head) plus payer --Legacy--> V091 bridge
    #   --Legacy--> payee; invoice larger than the direct channel, so the MPP
    #   router must split. Both parts were Committed on both payee channels and the
    #   invoice was still Received before settle_invoice; no preimage was reported
    #   on the payer side before that call; the payment then reached Success with
    #   the exact preimage and the invoice Paid. The part amounts were read from
    #   the per-channel inbound TLCs (never assumed), they sum to the invoice
    #   amount, the payee local balance on each channel moved by exactly its part,
    #   the payer offered-TLC balances cleared, and the payer outbound on the
    #   direct channel equals the payee inbound (no fee) while the bridged
    #   outbound is >= the bridged inbound (bridge fee). Versions were proven by
    #   force-closing both channels after settlement: 58-byte args ending 0x01 for
    #   the direct channel and 57-byte args for the bridged one.
    # partial: the split *amounts* are not deterministic -- the router decides
    #   which path takes how much. Only the channel assignment is forced (each
    #   single path is smaller than the invoice and only these two paths exist).
    #   A router that does not split over both channels fails with the observed
    #   per-channel state instead of silently passing.
    # not covered: UDT (xUDT); more than two parts; retries; the MPP partial
    #   failure/rebalance branches.
    # TEST-EVIDENCE-END H32V2-28

    # ------------------------------------------------------------------ H32V2-29

    # TEST-MAP: H32V2-29
    # H32V2-29 评审行（reviews/full-payment-hash-settlement-v2.md）：
    #   场景：MPP 分片分别落在 V1 与 Legacy 通道，其中一片已承诺未完成时对端强关该片所在通道
    #         并按该通道版本链上结算，其余分片仍锁定。
    #   预期：未收齐全部分片前不公开原像、不标记成功；已结算分片按自身版本核对（V1 全哈希 /
    #         Legacy 20 字节前缀）；其余分片继续按自身版本等待兑现或超时，付款终态与各分片证据一致。
    #   防止：用单个分片的链上结算提前完成 MPP 或泄露原像。
    # 拓扑与 H32V2-28 相同（payer --V1--> payee，payer --Legacy--> bridge(9a561b3) --Legacy--> payee），
    # 但收款方（payee）持有原像，因此由它强关、它结算；桥梁节点被临时停掉，
    # 让 Legacy 那一片无法在链下被兑现。
    # 证明点与断言的对应关系：
    #   证明点 1（场景成立）：发票确实拆成两片（V1 直连片 + Legacy 桥接片），两片金额之和等于发票金额。
    #   证明点 2（任何结算之前）：付款仍 Inflight、payment_preimage 为空、发票仍 Received —— 收款端
    #     还没有为任何一片公开原像。
    #   证明点 3（强关的正是 V1 片所在通道且版本未降级）：从真实强关交易的承诺 lock args 复核
    #     58 字节且末位 0x01；链上确认前付款不得提前改变终态。
    #   证明点 4（按该片自身版本核对）：结算交易带当前合约 code dep，witness 按 v1 严格解析 ——
    #     97 字节条目宽度、精确 32 字节 payment_hash、清单恰为该片金额、解锁原像等于真实 preimage。
    #     也就是说链上结算只兑付这一片，没有把另一片算进来。
    #   证明点 5（其余分片仍锁定，付款不得被提前标成功）：Legacy 片在付款端与收款端两个方向上
    #     都仍未终态，付款状态 != Success。
    #   证明点 6（已结算片自身终态、发票不得被提前写 Paid）：V1 片在付款端终态；因为另一片仍
    #     锁定且无法兑现，收款端发票不得离开 Received 变成 Paid。
    #   证明点 7（装置而非契约）：bridge.stop()/finally 恢复只是让 Legacy 片无法链下兑现的测试手段，
    #     不代表协议要求某个节点必须离线。
    # 未覆盖/待确认（与既有 TEST-EVIDENCE 一致）：Legacy 片链上结算而 V1 片仍锁定的反向组合
    #   （强关方必须持有原像，即总是收款方侧）、xUDT 资产、第二片仍 pending 的更复杂情形；
    #   以及“一片已链上结算、另一片仍锁定时整个 MPP 付款最终应处于什么状态”这一产品问题——
    #   本方法只断言“未标记 Success 且不公开原像”，不固定会话终态。
    def test_mixed_version_mpp_single_part_onsettled_does_not_complete(self):
        payer, payee = self.fiber1, self.fiber2
        bridge = self.__class__.legacy_bridge
        # 证明点 1：与 H32V2-28 相同的混合版本拓扑（V1 直连 + Legacy 两跳桥接）。
        direct_id, payer_bridge_id, bridge_payee_id = self._open_mixed_topology(
            payer, payee, bridge
        )

        preimage = self.generate_random_preimage()
        payment_hash = ckb_hash(preimage)
        invoice = self._hold_mpp_invoice(
            payee,
            payment_hash,
            MPP_AMOUNT,
            "ckb_hash",
            "H32V2-29 mixed-version MPP hold",
        )
        payment = payer.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "max_parts": hex(2),
                "max_fee_rate": hex(10**15),
            }
        )
        assert payment["payment_hash"] == payment_hash, payment
        self.wait_payment_state(payer, payment_hash, "Inflight", timeout=60)
        self.wait_invoice_state(payee, payment_hash, "Received", timeout=120)
        # 证明点 1：分片金额从两条收款通道的链下状态读取，两片合计等于发票金额。
        inbound = self._wait_inbound_parts(
            payee, (direct_id, bridge_payee_id), payment_hash, timeout=240
        )
        assert len(inbound) == 2 and sum(inbound.values()) == MPP_AMOUNT, inbound

        # Before anything is settled: the payment is not Success and no preimage
        # is reported anywhere (the payee only holds the invoice).
        # 证明点 2：结算前不得公开原像，也不得标记成功。
        held = payer.get_client().get_payment({"payment_hash": payment_hash})
        assert held["status"] == "Inflight", held
        assert held["payment_preimage"] is None, held

        # The counterparty (the payee) force-closes the V1 channel. Only that part
        # goes on chain; the Legacy part must stay locked.
        # 证明点 3：强关的是 V1 片所在通道，链上承诺 args 仍是 58 字节 V1。
        _, commitment = self._force_close_commitment(payee, direct_id)
        self._assert_commitment_layout(commitment, "v1")
        part_on_direct = inbound[direct_id]
        self.ckb.generate_epochs("0x1", wait_time=0)
        assert (
            payer.get_client().get_payment({"payment_hash": payment_hash})["status"]
            == "Inflight"
        ), "the payment must not resolve before the V1 claim confirms"

        # The bridge is taken offline so the other part cannot be fulfilled
        # off-chain; the V1 part is settled on chain with the real preimage.
        # 证明点 7：停掉桥梁是“让另一片无法链下兑现”的装置，不是产品契约。
        bridge.stop()
        try:
            payee.get_client().settle_invoice(
                {"payment_hash": payment_hash, "payment_preimage": preimage}
            )
            settlement = self.wait_for_spend(commitment["hash"])
            code_tx = self.current_contract_code_tx()
            # 证明点 4：结算确实执行当前部署的 commitment-lock。
            assert {
                "out_point": {"tx_hash": code_tx, "index": "0x0"},
                "dep_type": "code",
            } in settlement["cell_deps"], settlement["cell_deps"]
            # 证明点 4：按 V1 自身版本核对（97 字节条目、精确 32 字节 hash、清单恰为该片）。
            witness = self._parse_settlement(
                settlement, "v1", [(payment_hash, part_on_direct)]
            )
            # 证明点 4：链上消费用的是真实原像，且只兑付 V1 这一片。
            assert witness.unlocks[0].preimage == bytes.fromhex(
                preimage.removeprefix("0x")
            ), witness.unlocks

            # The other (Legacy) part is still locked on both ends and the
            # payment has not been marked Success while that part is unsecured.
            # 证明点 5：另一片（Legacy）在付款端仍未终态。
            payer_legacy = self._wait_until(
                lambda: self._tlc_in(payer, payer_bridge_id, payment_hash) or None,
                "the Legacy split to stay on the payer side",
                timeout=120,
            )
            assert not tlc_is_terminal(payer_legacy[0]), payer_legacy
            # 证明点 5：同一片在收款端也仍未终态 —— 单片的链上结算没有连带处理另一片。
            payee_legacy = self._wait_until(
                lambda: self._tlc_in(payee, bridge_payee_id, payment_hash) or None,
                "the Legacy split to stay on the payee side",
                timeout=120,
            )
            assert not tlc_is_terminal(payee_legacy[0]), payee_legacy
            # 证明点 5：那一片还没安全兑现，整笔付款就不得被标记成功。
            partial = payer.get_client().get_payment({"payment_hash": payment_hash})
            assert partial["status"] != "Success", partial
            # 证明点 6：已结算的 V1 片在付款端最终进入终态。链上消费先确认，付款端随后才由
            # 节点自身的链上对账把这个 offered TLC 收尾（本机实测约 4 分钟，触发节奏不完全由
            # 测试控制）。这是典型的上链触发查询，由 FIBER_ASSERT_ONCHAIN_TLC_QUERY 门控：关时
            # 不等这个终态；开启时才等到终态并留足余量。
            if onchain_tlc_query_enabled():
                self._wait_until(
                    lambda: all(
                        tlc_is_terminal(tlc)
                        for tlc in self._tlc_in(payer, direct_id, payment_hash)
                    )
                    and self._tlc_in(payer, direct_id, payment_hash),
                    "the settled V1 split to reach a terminal state on the payer side",
                    timeout=600,
                )
            # 证明点 6：单片链上结算不得把发票写成 Paid。hold 发票离开 Received 只有兑现
            # （→Paid）与显式 cancel_invoice（→Cancelled）两条路径；本场景里 V1 片所在通道
            # 已被强关、桥接片仍锁定且 bridge 已离线，两条路径都不会发生，所以发票只能停在
            # Received（见 reviews/review-feedback.md 中 H32V2-13 的确认口径）。
            partial_invoice = payee.get_client().get_invoice(
                {"payment_hash": payment_hash}
            )
            assert partial_invoice["status"] != "Paid", partial_invoice
        finally:
            # 证明点 7：恢复桥梁与连接，避免污染同类后续用例。
            self._restart(bridge, [payer, payee])

    # TEST-EVIDENCE-BEGIN H32V2-29
    # covered: same mixed-version topology as H32V2-28. Before settling, the payer
    #   payment was Inflight with payment_preimage null. The payee (the counterparty
    #   of the payer on the V1 channel) force-closed that channel; the force-close
    #   commitment had the 58-byte/0x01 V1 layout, and after settle_invoice the
    #   committed V1 settlement was parsed with the strict 97-byte entry width and
    #   the exact 32-byte hash and real preimage. The Legacy part stayed Committed
    #   on both ends (the bridge was taken offline so it could not be fulfilled),
    #   the payer payment was not Success while that part was unsecured, the settled
    #   V1 split reached a terminal state on the payer side (the on-chain claim
    #   confirms first and the payer node reconciles its offered TLC only minutes
    #   later, so the wait is bounded but generous), and the payee invoice was never
    #   marked Paid: a hold invoice leaves Received only by fulfillment (->Paid) or
    #   an explicit cancel_invoice (->Cancelled), and the force-closed V1 channel
    #   plus the still-locked Legacy part with the bridge offline can produce
    #   neither. The payer payment stayed Inflight and carried no terminal status
    #   even after that reconcile -- the whole MPP session has no settled outcome
    #   while one part is unsettled, which is recorded, not asserted as a contract.
    # partial: the V1 part is the one the payee can settle (it knows the preimage)
    #   and the Legacy part is kept locked by stopping the bridge -- that is a
    #   harness technique, not a protocol guarantee. 待确认: what the whole MPP
    #   payment must end as when one part is settled on chain and another is still
    #   locked; this row asserts only "not Success while a part is unsecured" and
    #   leaves the terminal status of the session open.
    # not covered: a Legacy part settled on chain while a V1 part stays locked (the
    #   force-closing side needs the preimage, i.e. it is always the payee side);
    #   UDT (xUDT); a second still-pending part.
    # TEST-EVIDENCE-END H32V2-29


@requires_full_hash_attack_fnn
class TestMixedVersionMppAdversarial(_MixedVersionMppSupport):
    """H32V2-30/31: long mixed-version paths and per-part version reconciliation.

    These two cases need the instrumented ``ATTACK_FULL_HASH_DEV`` counterparty
    started with ``LEGACY_COUNTERPARTY_ENV`` (a Legacy channel whose watchtower can
    emit a prefix-only claim). They live in a second class with fresh ports so the
    marker does not drag the adversary into H32V2-28/29.
    """

    ckb_rpc_port, ckb_p2p_port = 26114, 26115
    fiber1_rpc_port, fiber1_p2p_port = 26128, 26127
    fiber2_rpc_port, fiber2_p2p_port = 26129, 26130
    extra_fiber_rpc_port, extra_fiber_p2p_port = 26200, 26300
    start_fiber_config = {
        "fiber_watchtower_check_interval_seconds": WATCHTOWER_INTERVAL
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
            cls.extra_fiber_rpc_port + 2,
            cls.extra_fiber_p2p_port + 2,
            cls.extra_fiber_rpc_port + 3,
            cls.extra_fiber_p2p_port + 3,
        ):
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", port))
        assert "9a561b3" in subprocess.check_output(
            [ROOT / FiberConfigPath.V091_DEV.fiber_bin_path, "--version"], text=True
        )
        super().setup_class()
        cls.ckb = cls.node.getClient()

    # ------------------------------------------------------------------ H32V2-30

    # TEST-MAP: H32V2-30
    # H32V2-30 评审行（reviews/full-payment-hash-settlement-v2.md）：
    #   场景：至少三跳链路混用 V1/Legacy（上游 V1→下游 Legacy 及反向）；付款未到期且发起端
    #         无重试机会，下游 Legacy 对端以仅前缀有效原像完成该跳精确 TLC 的已确认消费。
    #   预期：中继按下游通道版本识别精确身份，在到期前跨版本向上游传播失败；各跳通道保持可用、
    #         余额不被错误扣减；发起端最终 Failed 且不获得成功原像。
    #   防止：跨版本长链路中继悬挂或把错误原像当成功传播。
    # 拓扑：payer --V1--> relay1 --V1--> relay2 --Legacy--> adversary(9a561b3 旧节点，
    #   以 LEGACY_COUNTERPARTY_ENV 启动，不宣告完整哈希)，即上游 V1、下游 Legacy。
    #   显式单路由（_send_explicit_route）= 无重试机会。
    # 证明点与断言的对应关系：
    #   证明点 1（混合版本三跳拓扑）：三条通道都建好且在图里可见；路由按 payer 命名的 hop 列表
    #     逐跳指定 outpoint，因此链路确实跨 V1 与 Legacy 两版。
    #   证明点 2（付款在途且失败前提成立）：发票 Received、付款 Inflight，六条 TLC 记录
    #     （三跳 × 两端）全部 Committed，并记下六者中最早的 expiry 作为“非超时”判据。
    #   证明点 3（下游证据是精确前缀消费）：对端强关下游通道，承诺 args 为 57 字节 Legacy；
    #     链上消费的 witness 只含该笔 TLC 且解锁它，同时断言原像摘要与 invoice hash 不等、
    #     仅 20 字节前缀相同 —— 失败必须来自这条精确证据而非超时或误判。
    #   证明点 4（跨版本失败向上游传播）：上游两跳各自的 Outbound/Inbound TLC 都到
    #     RemoveAckConfirmed，且下游 Legacy 片终态，付款最终 Failed。
    #   证明点 5（失败不来自超时）：付款失败时本地时间与链上 median time 都早于六条 TLC 的
    #     最早 expiry —— 是消费证据驱动，不是任何一跳的超时。
    #   证明点 6（不把错误原像当成功、不向中继转嫁损失）：付款没有 payment_preimage；传播过程中
    #     上游通道始终保持 ChannelReady 且无 shutdown_transaction_hash，四条上游视图的
    #     local/remote 余额与发起前逐字段相同（诚实中继不为下游链上损失扣上游的钱）。
    # 未覆盖/观测限制（与既有 TEST-EVIDENCE 一致）：“中继不缓存或转发错误原像”只能由
    #   Failed + payment_preimage 为空 + RemoveAckConfirmed 推断，中继持久化的原像库没有 RPC 可读；
    #   未覆盖 xUDT、超过三跳、启用重试的路由（本行前提已排除重试）。
    def test_mixed_version_long_path_fails_upstream(self):
        payer, relay1 = self.fiber1, self.fiber2
        relay2 = self._start_extra("relay2_30", FiberConfigPath.CURRENT_DEV)
        # 证明点 1：下游对端固定为旧版 9a561b3 且关闭完整哈希宣告，因此最后一跳必是 Legacy。
        adversary = self._start_extra(
            "adversary_30",
            FiberConfigPath.ATTACK_FULL_HASH_DEV,
            env=LEGACY_COUNTERPARTY_ENV,
        )

        # Route: payer --V1--> relay1 --V1--> relay2 --Legacy--> adversary.
        # 证明点 1：三跳链路，前两跳由新节点组成（V1），最后一跳通向旧节点（Legacy）。
        hop1 = self.open_channel(payer, relay1, LONG_PATH_CAPACITY, 0)
        hop2 = self.open_channel(relay1, relay2, LONG_PATH_CAPACITY, 0)
        hop3 = self.open_channel(relay2, adversary, LONG_PATH_CAPACITY, 0)
        outpoints = [
            self._channel(payer, hop1)["channel_outpoint"],
            self._channel(relay1, hop2)["channel_outpoint"],
            self._channel(relay2, hop3)["channel_outpoint"],
        ]
        # 证明点 1：四条通道的图都同步到这些 outpoint 后才发款，避免把“gossip 未同步”当成失败。
        for fiber in (payer, relay1, relay2, adversary):
            self._wait_graph_outpoints(fiber, outpoints)

        # 证明点 6 的基线：上游四条 (通道, 方向) 视图的 local/remote 余额。
        upstream_views = [
            (payer, hop1, "Outbound"),
            (relay1, hop1, "Inbound"),
            (relay1, hop2, "Outbound"),
            (relay2, hop2, "Inbound"),
        ]
        upstream_before = {
            index: (
                int(self._channel(fiber, channel_id)["local_balance"], 16),
                int(self._channel(fiber, channel_id)["remote_balance"], 16),
            )
            for index, (fiber, channel_id, _) in enumerate(upstream_views)
        }

        # 证明点 3 的素材：invoice hash 只与 preimage 摘要共享 20 字节前缀（完整 hash 不同）。
        preimage, bad_hash = self._craft_prefix_hash()
        invoice = self._hold_single_invoice(
            adversary,
            bad_hash,
            LONG_PATH_AMOUNT,
            "sha256",
            "H32V2-30 mixed-version long path prefix-only claim",
        )
        hops = [
            {
                "pubkey": relay1.get_pubkey(),
                "channel_outpoint": self._channel(payer, hop1)["channel_outpoint"],
            },
            {
                "pubkey": relay2.get_pubkey(),
                "channel_outpoint": self._channel(relay1, hop2)["channel_outpoint"],
            },
            {
                "pubkey": adversary.get_pubkey(),
                "channel_outpoint": self._channel(relay2, hop3)["channel_outpoint"],
            },
        ]
        # 证明点 1/2：显式单路由（无重试）发出付款，付款哈希即那个仅前缀匹配的 hash。
        payment = self._send_explicit_route(payer, hops, invoice, LONG_PATH_AMOUNT)
        assert payment["payment_hash"] == bad_hash, payment
        self.wait_invoice_state(adversary, bad_hash, "Received", timeout=120)
        self.wait_payment_state(payer, bad_hash, "Inflight", timeout=60)

        # 证明点 2：三跳 × 两端的六条 TLC 记录都已 Committed，付款确实在途。
        committed = [
            self._wait_tlc_committed(payer, hop1, bad_hash, timeout=180),
            self._wait_tlc_committed(relay1, hop1, bad_hash, timeout=180),
            self._wait_tlc_committed(relay1, hop2, bad_hash, timeout=180),
            self._wait_tlc_committed(relay2, hop2, bad_hash, timeout=180),
            self._wait_tlc_committed(relay2, hop3, bad_hash, timeout=180),
            self._wait_tlc_committed(adversary, hop3, bad_hash, timeout=180),
        ]
        # 证明点 5 的判据：六条 TLC 中最早的到期时间。
        earliest_expiry = min(int(tlc["expiry"], 16) for tlc in committed)

        # The adversary force-closes the downstream Legacy channel and claims the
        # exact TLC with a preimage that only matches its 20-byte prefix.
        # 证明点 3：下游强关是 Legacy 布局（57 字节），消费确认前发起端不得提前改变终态。
        _, commitment = self._force_close_commitment(adversary, hop3)
        self._assert_commitment_layout(commitment, "legacy")
        self.ckb.generate_epochs("0x1", wait_time=0)
        assert (
            payer.get_client().get_payment({"payment_hash": bad_hash})["status"]
            == "Inflight"
        ), "the payer must stay Inflight until the downstream claim confirms"
        self._inject_prefix_preimage(adversary, bad_hash, preimage)
        spend = self._wait_spend(
            {"tx_hash": commitment["hash"], "index": "0x0"}, "Legacy commitment cell"
        )
        # 证明点 3：链上证据精确指向该笔 TLC 且只解锁它；原像摘要 != invoice hash、仅前缀相同。
        self._assert_exact_tlc_claim(
            spend, bad_hash, preimage, [(bad_hash, LONG_PATH_AMOUNT)]
        )

        # The failure must travel back across both honest hops before the earliest
        # recorded expiry while the upstream channels stay live and uncharged.
        # 证明点 4：在有界窗口内等待失败跨两跳向上游传播（下游终态 + 上游全部 RemoveAckConfirmed
        # + 发起端 Failed），期间反复确认上游通道仍可用。
        deadline = time.monotonic() + 660
        payer_payment = None
        while True:
            downstream_terminal = all(
                tlc_is_terminal(tlc) for tlc in self._tlc_in(relay2, hop3, bad_hash)
            )
            payer_payment = payer.get_client().get_payment({"payment_hash": bad_hash})
            resolved = all(
                any(
                    tlc["status"] == {direction: "RemoveAckConfirmed"}
                    for tlc in self._tlc_in(fiber, channel_id, bad_hash)
                )
                for fiber, channel_id, direction in upstream_views
            )
            # 证明点 6：传播过程中上游通道必须保持 ChannelReady 且没有进入强关。
            for fiber, channel_id, _ in upstream_views:
                channel = self._channel(fiber, channel_id)
                assert channel["state"]["state_name"] == "ChannelReady", (
                    "an upstream channel left ChannelReady during failure "
                    f"propagation: {channel}; payment={payer_payment}"
                )
                assert channel.get("shutdown_transaction_hash") is None, channel
            if downstream_terminal and resolved and payer_payment["status"] == "Failed":
                break
            # 下游终态、上游 RemoveAckConfirmed 与发起端 Failed 都要等节点自己的链上扫描收尾，
            # 属上链触发查询；开关关时不做这个轮询，只保留下面一次的存活核对与"非成功"判据。
            if not onchain_tlc_query_enabled():
                break
            assert time.monotonic() < deadline, (
                "upstream failure propagation incomplete: "
                f"downstream_terminal={downstream_terminal}, resolved={resolved}, "
                f"payment={payer_payment}"
            )
            self._mine_watchtower_rounds(1)

        # 证明点 6：失败不携带成功原像 —— 错误原像没有被当作成功向上游传播。
        if onchain_tlc_query_enabled():
            assert payer_payment["status"] == "Failed", payer_payment
        else:
            assert payer_payment["status"] != "Success", payer_payment
        assert payer_payment.get("payment_preimage") is None, payer_payment
        # The failure came from the confirmed claim, not from the normal expiry
        # path of any hop.
        # 证明点 5：本地时间与链上 median time 都在最早 expiry 之前，排除超时路径。
        assert (
            int(time.time() * 1000) < earliest_expiry
        ), f"failure propagated after the earliest recorded TLC expiry {earliest_expiry}"
        assert (
            self._chain_median_time() < earliest_expiry
        ), f"chain median time is already past the earliest expiry {earliest_expiry}"
        # 证明点 6：上游四条视图的余额与发起前逐字段相同 —— 诚实中继不为下游链上损失扣上游通道。
        for index, (fiber, channel_id, _) in enumerate(upstream_views):
            channel = self._channel(fiber, channel_id)
            assert (
                int(channel["local_balance"], 16),
                int(channel["remote_balance"], 16),
            ) == upstream_before[index], (
                "the honest relay must not charge an upstream channel for a "
                f"downstream on-chain loss: {channel}"
            )

    # TEST-EVIDENCE-BEGIN H32V2-30
    # covered: explicit three-hop route payer --V1--> relay1 --V1--> relay2
    #   --Legacy--> adversary, so the upstream hop is V1 while the last hop is
    #   Legacy. The adversary (ATTACK_FULL_HASH_DEV + LEGACY_COUNTERPARTY_ENV)
    #   force-closed the downstream channel (57-byte Legacy commitment args) and
    #   its watchtower confirmed a spend whose witness carries the full preimage
    #   but whose on-chain 20-byte hash only matches the invoice hash prefix. Both
    #   upstream TLC pairs reached RemoveAckConfirmed, the payer payment ended
    #   Failed with no preimage, the upstream channels stayed ChannelReady with no
    #   shutdown_transaction_hash, and their recorded local/remote balances were
    #   unchanged. The failure timestamp and the chain median time are both before
    #   the earliest of the six recorded TLC expiries.
    # partial: "does not cache or forward the wrong preimage as success" is
    #   inferred from Failed + payment_preimage null + RemoveAckConfirmed; the
    #   relays' persisted preimage store is not readable over RPC.
    # not covered: UDT (xUDT); more than three hops; retry-enabled routes (the row
    #   excludes retries, so an explicit single route is used).
    # TEST-EVIDENCE-END H32V2-30

    # ------------------------------------------------------------------ H32V2-31

    # TEST-MAP: H32V2-31
    def test_mixed_version_parts_reconcile_by_own_version(self):
        payer, payee = self.fiber1, self.fiber2
        adversary = self._start_extra(
            "adversary_31",
            FiberConfigPath.ATTACK_FULL_HASH_DEV,
            env=LEGACY_COUNTERPARTY_ENV,
        )
        bridge = self._start_extra("legacy_bridge_31", FiberConfigPath.V091_DEV)

        # Three intended branches out of the payer: direct V1, adversary Legacy,
        # honest V091 Legacy. Each branch is smaller than the invoice.
        direct_id = self.open_channel(payer, payee, MIXED_DIRECT_CAPACITY, 0)
        adversary_up_id = self.open_channel(payer, adversary, MIXED_BRANCH_CAPACITY, 0)
        adversary_down_id = self.open_channel(
            adversary, payee, MIXED_BRANCH_CAPACITY, 0
        )
        bridge_up_id = self.open_channel(payer, bridge, MIXED_BRANCH_CAPACITY, 0)
        bridge_down_id = self.open_channel(bridge, payee, MIXED_BRANCH_CAPACITY, 0)
        # The payer's router needs every branch channel, including the two
        # downstream hops it does not own, before it can split the invoice.
        outpoints = [
            self._channel(payer, direct_id)["channel_outpoint"],
            self._channel(payer, adversary_up_id)["channel_outpoint"],
            self._channel(adversary, adversary_down_id)["channel_outpoint"],
            self._channel(payer, bridge_up_id)["channel_outpoint"],
            self._channel(bridge, bridge_down_id)["channel_outpoint"],
        ]
        for fiber in (payer, payee, adversary, bridge):
            self._wait_graph_outpoints(fiber, outpoints)

        preimage, bad_hash = self._craft_prefix_hash()
        invoice = self._hold_mpp_invoice(
            payee, bad_hash, MIXED_AMOUNT, "sha256", "H32V2-31 mixed-version per-part"
        )
        payment = payer.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "max_parts": hex(3),
                "max_fee_rate": hex(10**15),
            }
        )
        assert payment["payment_hash"] == bad_hash, payment
        self.wait_payment_state(payer, bad_hash, "Inflight", timeout=60)
        self.wait_invoice_state(payee, bad_hash, "Received", timeout=120)
        inbound = self._wait_inbound_parts(
            payee,
            (direct_id, adversary_down_id, bridge_down_id),
            bad_hash,
            timeout=300,
        )
        assert len(inbound) == 3, (
            "the router did not split the invoice over the three intended channels; "
            f"observed inbound parts={inbound}"
        )
        assert all(amount > 0 for amount in inbound.values()), inbound
        assert sum(inbound.values()) == MIXED_AMOUNT, inbound
        outbound = self._wait_outbound_parts(
            payer, (direct_id, adversary_up_id, bridge_up_id), bad_hash, timeout=300
        )

        # The malicious Legacy branch consumes its part with a prefix-only bad
        # preimage; that part must be judged by the Legacy 20-byte rule.
        _, adversary_commitment = self._force_close_commitment(
            adversary, adversary_up_id
        )
        self._assert_commitment_layout(adversary_commitment, "legacy")
        self.ckb.generate_epochs("0x1", wait_time=0)
        assert (
            payer.get_client().get_payment({"payment_hash": bad_hash})[
                "payment_preimage"
            ]
            is None
        ), "no usable preimage may exist before the on-chain claim"
        self._inject_prefix_preimage(adversary, bad_hash, preimage)
        legacy_spend = self._wait_spend(
            {"tx_hash": adversary_commitment["hash"], "index": "0x0"},
            "Legacy commitment cell",
        )
        self._assert_exact_tlc_claim(
            legacy_spend, bad_hash, preimage, [(bad_hash, outbound[adversary_up_id])]
        )

        # The Legacy part is terminal on the payer side and no usable preimage is
        # recorded; the bad prefix evidence must not touch the other parts.
        self._wait_until(
            lambda: (
                self._tlc_in(payer, adversary_up_id, bad_hash)
                and all(
                    tlc_is_terminal(tlc)
                    for tlc in self._tlc_in(payer, adversary_up_id, bad_hash)
                )
            ),
            "the Legacy part to become terminal on the payer",
            timeout=300,
        )
        observed = payer.get_client().get_payment({"payment_hash": bad_hash})
        assert observed["payment_preimage"] is None, observed
        for channel_id in (direct_id, bridge_up_id):
            tlcs = self._tlc_in(payer, channel_id, bad_hash)
            assert tlcs and not any(
                tlc_is_terminal(tlc) for tlc in tlcs
            ), f"channel {channel_id} was resolved by another part's evidence: {tlcs}"
        assert (
            payee.get_client().get_invoice({"payment_hash": bad_hash})["status"]
            == "Received"
        )

        # The V1 part is judged on the full 32 bytes: force-close it and show that
        # the same prefix-only preimage cannot claim it (the commitment cell stays
        # live across watchtower rounds).
        pending_before_v1_close = {
            channel_id: [
                tlc["status"] for tlc in self._tlc_in(payer, channel_id, bad_hash)
            ]
            for channel_id in (direct_id, bridge_up_id)
        }
        _, direct_commitment = self._force_close_commitment(payer, direct_id)
        self._assert_commitment_layout(direct_commitment, "v1")
        self._mine_watchtower_rounds(3)
        assert (
            self.ckb.get_live_cell("0x0", direct_commitment["hash"])["status"] == "live"
        ), (
            "the V1 commitment cell must stay live: the prefix-only preimage used on "
            f"the Legacy channel cannot satisfy the full 32-byte hash: {direct_commitment}"
        )
        direct_tlcs = self._tlc_in(payer, direct_id, bad_hash)
        assert direct_tlcs and not any(
            tlc_is_terminal(tlc) for tlc in direct_tlcs
        ), direct_tlcs
        # Neither part's evidence overwrote the other's state: the untouched honest
        # Legacy part keeps the exact status it had before the V1 close.
        assert [
            tlc["status"] for tlc in self._tlc_in(payer, bridge_up_id, bad_hash)
        ] == pending_before_v1_close[bridge_up_id], pending_before_v1_close

    # TEST-EVIDENCE-BEGIN H32V2-31
    # covered: one hold invoice on the payee with a single crafted payment hash
    #   (sha256(preimage) with its last 12 bytes flipped) routed as three MPP parts
    #   over a direct V1 channel, an adversary Legacy branch and an honest V091
    #   Legacy branch. The adversary force-closed its upstream channel (57-byte
    #   Legacy args) and confirmed a prefix-only claim with the real preimage on
    #   the Legacy branch; that branch became terminal on the payer with
    #   payment_preimage still null, while the direct V1 part and the untouched
    #   honest Legacy part stayed non-terminal and the payee invoice stayed
    #   Received. The V1 part was then force-closed (58-byte args ending 0x01) and
    #   its commitment cell stayed live across watchtower rounds, i.e. the same
    #   prefix-only preimage cannot resolve it; both pending parts' statuses were
    #   unchanged after those rounds, so neither part's evidence overwrote the
    #   other's state.
    # impossible as written: the row's literal pairing -- the SAME payment_hash
    #   accepted on a V1 channel by the full 32-byte preimage AND consumed on a
    #   Legacy channel by a prefix-only bad preimage -- is not constructible. A
    #   Legacy entry commits only the 20-byte prefix of the same hash, so the
    #   second preimage would have to collide with the first on 160 bits; and if
    #   the hash is crafted as a bad prefix (this suite), the full 32 bytes no
    #   longer match and the V1 contract cannot accept the real preimage. The "V1
    #   part Success with the correct preimage" half is therefore replaced by the
    #   achievable per-version separation asserted above (V1 layout + full-hash
    #   rejection + liveness). 待确认: the row's V1-success half needs a product
    #   decision or a collision-capable fixture.
    # partial: 待确认: whether MPP allows partial success at all -- the row's
    #   "the untouched pending part keeps waiting" is asserted only for the short
    #   window in which the Legacy claim is observed; the session's eventual
    #   terminal status is not asserted because the product contract is open.
    # not covered: UDT (xUDT); a V1 attacker emitting a full-hash-mismatch V1
    #   settlement (that is H32V2-07's contract-level negative case).
    # TEST-EVIDENCE-END H32V2-31

    # ------------------------------------------------------------------ H32V2-32

    # TEST-MAP: H32V2-32
    # TEST-EVIDENCE-BEGIN: H32V2-32
    # Evidence | covered | 与 H32V2-30 同一「上游 V1→下游 Legacy」三跳拓扑，但下游对端改用
    # 普通旧版节点 V091_DEV（不宣告完整哈希 → 协商 Legacy），不依赖对抗/故障注入夹具；
    # 判别输入换成完整 32 字节 hash 正确的真实原像：invoice hash == sha256(preimage)。
    # 下游链上结算由**提供该 TLC 的一侧（relay2）**强关触发：只有它的承诺带着这笔 offered TLC，
    # 对端才有可被消费的精确 TLC；若让收款方强关，链上承诺里没有该 TLC，无从结算。
    # 实际证明：链上消费 witness 按 Legacy 解析、清单恰为该笔 TLC、解锁原像等于真实 preimage、
    # 完整 hash 与原像摘要相等；发起端付款 Success 且记录的原像就是该原像；下游对端按该笔金额
    # 减去该笔结算交易的真实矿工费**链上到账**（强制 shutdown 后链上取回的资金不会写回 closed
    # channel 的 local_balance，故只以链上 capacity 增量核对）；两跳上游通道保持 ChannelReady、
    # 无 shutdown_transaction_hash、余额只按该笔付款与真实手续费变动；上游 TLC 全部离开进行中
    # 状态（终态集合枚举），没有悬挂。
    # partial: “中继向上游传播成功”只能由“上游两跳 TLC 全部为终态 + 发起端 Success + 余额不变”
    #   推断；中继持久化的原像库没有 RPC 可读。因此预期文字里“上游 TLC 按成功路径终态收尾”暂不
    #   写死状态名，测试接受成功路径与撤销路径两类终态名（见下）。对端到账用其账户链上 capacity
    #   增量判定（基线在强关前采样），该增量是账户级别的、不是某个通道的余额。
    # not covered: 反向长链路（上游 Legacy→下游 V1，需另立 ID）；xUDT；超过三跳；启用重试的路由。
    # TEST-EVIDENCE-END H32V2-32
    # H32V2-32 评审行（reviews/full-payment-hash-settlement-v2.md）：
    #   场景：至少三跳链路混用 V1/Legacy（上游 V1→下游 Legacy），付款未到期且发起端无重试机会；
    #         下游 Legacy 对端以完整 32 字节 hash 正确的有效原像完成该跳精确 TLC 的已确认消费。
    #   预期：中继按下游通道版本识别精确身份并证明原像完整匹配，随即向上游传播成功；发起端付款
    #         Success 且记录的原像就是该原像，对端到账；各跳通道保持可用、余额只按该笔付款与真实
    #         手续费变动；上游 TLC 按成功路径终态收尾，不悬挂、不按失败传播。
    #   防止：以坏原像失败收尾的实现把完整原像匹配的正常消费一并失败，或跨版本成功无法上传导致
    #         资金卡住。
    # 与 H32V2-30 的对照关系：30 用坏原像（仅前缀匹配）→ 必须向上游传播失败；32 用真实原像
    #   （完整 hash 相等）→ 必须向上游传播成功。两行合起来才能证明中继是按原像是否完整匹配
    #   分流，而不是一律失败或一律成功。
    # 证明点与断言的对应关系：
    #   证明点 1（混合版本三跳拓扑、无重试）：三条通道都建好且图同步可见，显式单路由发送。
    #   证明点 2（判别输入是完整匹配）：invoice hash 由 sha256(preimage) 算出，并同时断言它
    #     不等于 30 那种“仅改尾字节”的 hash，排除把前缀匹配当完整匹配。
    #   证明点 3（在途且证据未到）：发票 Received、付款 Inflight，三跳×两端共六条 TLC 记录
    #     全部 Committed；链上确认前付款不得提前改变终态。
    #   证明点 4（链上消费按下游自身版本且精确匹配）：对端强关的承诺 args 为 57 字节 Legacy；
    #     结算 witness 按 legacy 解析、清单恰为该笔金额、解锁原像等于真实 preimage，且
    #     sha256(preimage) == invoice hash（完整 32 字节相等 —— 本行的判别性断言）。
    #   证明点 5（成功上传、不悬挂）：上游四条 (通道, 方向) 视图的 TLC 全部离开进行中状态
    #     （COMMITTED_STATUSES 之外的终态），且付款 Success、记录原像等于真实原像 —— 说明这次
    #     完整匹配的消费被当作成功向上游传播，而不是失败。
    #   证明点 6（资金落在对端、余额只按该笔变动）：下游对端 local_balance 增加恰为该笔金额、
    #     其 received_tlc_balance 归零；上游四条视图的 local/remote 余额与发起前逐字段相同
    #     （中继不因这次链上消费被扣上游的钱，也不多收）。
    #   证明点 7（通道保持可用）：四条上游视图通道仍 ChannelReady 且没有进入强关，付款没有
    #     把长链路打散。
    #   证明点 8（payer 成功付款）：付款 Success、无 failed_error、返回原像就是本测试原像，
    #     且手续费非零。
    #   证明点 9（payer 侧资金变动）：两跳都从本地余额扣走，两跳支出之和 >= 该笔金额（收款方
    #     不被扣转发费）且 <= 该笔金额 + 1% relay 手续费额度。
    #   证明点 10（链路资金守恒）：relay 的两跳变动大小相等、方向相反（钱穿过去，只留下转发费）。

    def test_mixed_version_long_path_full_preimage_success(self):
        payer, relay1 = self.fiber1, self.fiber2
        relay2 = self._start_extra("relay2_32", FiberConfigPath.CURRENT_DEV)
        # 证明点 1：下游对端用普通旧版节点 V091_DEV（不宣告完整哈希 → 协商 Legacy），
        # 不是对抗/故障注入夹具：本行要的是“真实原像的正常消费”，不需要 p2p 注入能力。
        adversary = self._start_extra("adversary_32", FiberConfigPath.V091_DEV)

        # 证明点 1：同 H32V2-30 的三跳拓扑：payer --V1--> relay1 --V1--> relay2 --Legacy--> adversary。
        hop1 = self.open_channel(payer, relay1, LONG_PATH_CAPACITY, 0)
        hop2 = self.open_channel(relay1, relay2, LONG_PATH_CAPACITY, 0)
        hop3 = self.open_channel(relay2, adversary, LONG_PATH_CAPACITY, 0)
        outpoints = [
            self._channel(payer, hop1)["channel_outpoint"],
            self._channel(relay1, hop2)["channel_outpoint"],
            self._channel(relay2, hop3)["channel_outpoint"],
        ]
        for fiber in (payer, relay1, relay2, adversary):
            self._wait_graph_outpoints(fiber, outpoints)

        # 证明点 6 的基线：上游四条 (通道, 方向) 视图的 local/remote 余额。
        upstream_views = [
            (payer, hop1, "Outbound"),
            (relay1, hop1, "Inbound"),
            (relay1, hop2, "Outbound"),
            (relay2, hop2, "Inbound"),
        ]
        upstream_before = {
            index: (
                int(self._channel(fiber, channel_id)["local_balance"], 16),
                int(self._channel(fiber, channel_id)["remote_balance"], 16),
            )
            for index, (fiber, channel_id, _) in enumerate(upstream_views)
        }
        # 证明点 6 的基线：通道强关后资金会链上到账，所以要记对端账户的链上 CKB
        # （对端确实持有该笔 TLC 已由上面的 _wait_tlc_committed 断言）。
        adversary_chain_before = self._chain_ckb(adversary)
        # 证明点 2：真实原像；invoice hash 是它的完整 sha256（判别输入与 H32V2-30 相反）。
        preimage = self.generate_random_preimage()
        payment_hash = self._sha256_hex(preimage)
        invoice = self._hold_single_invoice(
            adversary,
            payment_hash,
            LONG_PATH_AMOUNT,
            "sha256",
            "H32V2-32 mixed-version long path full preimage",
        )
        hops = [
            {
                "pubkey": relay1.get_pubkey(),
                "channel_outpoint": self._channel(payer, hop1)["channel_outpoint"],
            },
            {
                "pubkey": relay2.get_pubkey(),
                "channel_outpoint": self._channel(relay1, hop2)["channel_outpoint"],
            },
            {
                "pubkey": adversary.get_pubkey(),
                "channel_outpoint": self._channel(relay2, hop3)["channel_outpoint"],
            },
        ]
        # 证明点 1：显式单路由（无重试）；mine_first 先把发款落定，便于随后停自动矿工。
        payment = self._send_explicit_route(
            payer, hops, invoice, LONG_PATH_AMOUNT, mine_first=True
        )
        assert payment["payment_hash"] == payment_hash, payment
        self.wait_invoice_state(adversary, payment_hash, "Received", timeout=120)
        self.wait_payment_state(payer, payment_hash, "Inflight", timeout=60)

        # 证明点 3：三跳 × 两端的六条 TLC 都 Committed；六者最早的 expiry 用作“未到期”判据。
        committed = [
            self._wait_tlc_committed(payer, hop1, payment_hash, timeout=180),
            self._wait_tlc_committed(relay1, hop1, payment_hash, timeout=180),
            self._wait_tlc_committed(relay1, hop2, payment_hash, timeout=180),
            self._wait_tlc_committed(relay2, hop2, payment_hash, timeout=180),
            self._wait_tlc_committed(relay2, hop3, payment_hash, timeout=180),
            self._wait_tlc_committed(adversary, hop3, payment_hash, timeout=180),
        ]
        earliest_expiry = min(int(tlc["expiry"], 16) for tlc in committed)

        # 证明点 4 的前置：让下游 Legacy 通道进入链上结算。
        # 必须由**提供该 TLC 的一侧**（relay2）强关：只有它的承诺里带着这笔 offered TLC，
        # 对端才有“可被链上消费的精确 TLC”。若反过来让收款方强关，链上承诺里根本没有这笔
        # TLC，对手方 watchtower 无从结算（这正是此前几轮“消费永不出现”的原因）。
        _, commitment = self._force_close_commitment(relay2, hop3)
        self._assert_commitment_layout(commitment, "legacy")
        # 关键：必须把那笔强关承诺真正挖到已确认，再等一个 epoch 满足承诺延迟。
        # 只调 generate_epochs 不保证确认该交易；承诺不上链时 watchtower 不会结算。
        self.Miner.miner_until_tx_committed(self.node, commitment["hash"])
        self.ckb.generate_epochs("0x1", wait_time=0)
        assert (
            self.ckb.get_transaction(commitment["hash"])["tx_status"]["status"]
            == "committed"
        ), commitment["hash"]
        assert (
            payer.get_client().get_payment({"payment_hash": payment_hash})["status"]
            == "Inflight"
        ), "the payer must stay Inflight until the on-chain consumption confirms"
        # 证明点 4：对端以真实原像完成该跳精确 TLC 的链上消费。
        # 走正常 settle_invoice（链上关闭时由收款方的内置 watchtower 广播结算），与 H32V2-29 同一
        # 做法；不用 create_preimage(force=True) —— 那是只给对抗对端伪造原像用的钩子。

        adversary.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )
        spend = self._wait_spend(
            {"tx_hash": commitment["hash"], "index": "0x0"},
            "Legacy commitment cell",
            timeout=420,
        )
        # 证明点 4：确认的消费精确指向该笔 TLC，解锁原像就是真实原像，且完整 32 字节相等。
        # expect_full_hash_match=True 已经断言解锁原像 == 本测试的原像、且 sha256(原像) == invoice hash。
        witness = self._assert_exact_tlc_claim(
            spend,
            payment_hash,
            preimage,
            [(payment_hash, LONG_PATH_AMOUNT)],
            expect_full_hash_match=True,
        )
        # 再直接比一次字节，失败时输出实际链上原像，便于区分“不是完整匹配”与“解析出错”。
        assert witness.unlocks[0].preimage == bytes.fromhex(
            preimage.removeprefix("0x")
        ), (
            "on-chain unlock must carry this test's preimage: "
            f"{witness.unlocks[0].preimage.hex() if witness.unlocks[0].preimage else None} "
            f"!= {preimage}"
        )
        assert (
            int(time.time() * 1000) < earliest_expiry
        ), f"consumption confirmed after the earliest recorded TLC expiry {earliest_expiry}"

        # 证明点 5：这次“完整匹配”的消费必须被当作成功上传：付款 Success 且原像正确。
        # 上链产出的付款查询终态由 FIBER_ASSERT_ONCHAIN_TLC_QUERY 门控；关时只核对没有被判成失败。
        if onchain_tlc_query_enabled():
            self.wait_payment_state(payer, payment_hash, "Success", timeout=660)
            settled = payer.get_client().get_payment({"payment_hash": payment_hash})
            assert settled["status"] == "Success", settled
            assert settled["payment_preimage"] == preimage, settled
        else:
            settled = payer.get_client().get_payment({"payment_hash": payment_hash})
            assert settled["status"] != "Failed", settled

        # 证明点 5：上游两跳的 TLC 都必须离开进行中状态并收敛，不悬挂。
        # 上游收尾同样依赖节点把链上成功传播回上游，属上链触发的查询，由同一开关门控。
        if onchain_tlc_query_enabled():

            def upstream_resolved():
                for fiber, channel_id, _ in upstream_views:
                    tlcs = self._tlc_in(fiber, channel_id, payment_hash)
                    if not tlcs:
                        continue
                    if any(not tlc_is_terminal(tlc) for tlc in tlcs):
                        return None
                return True

            self._wait_until(
                upstream_resolved,
                "the upstream TLCs to leave the in-flight states",
                timeout=660,
            )
            # 上游终态不得是“已宣告/已承诺”这些进行中状态；成功路径与撤销路径的终态名都接受，
            # 具体名字在实测后回填评审行的预期文字。
            for fiber, channel_id, _ in upstream_views:
                for tlc in self._tlc_in(fiber, channel_id, payment_hash):
                    assert (
                        tlc["status"] not in COMMITTED_STATUSES
                    ), f"upstream TLC stayed in flight after a full-hash success: {tlc}"

        # 证明点 6：下游对端按该笔金额**链上到账**。
        # 注意：强制 shutdown 之后，链上取回的资金不会写回 closed channel 的 local_balance，
        # 所以到账只能用链上 capacity（或结算交易的输出）核对，不能拿通道余额断言。
        # 收款方净得 = 该笔 TLC 金额 - 该笔结算交易的真实矿工费。
        settlement_fee = self.get_tx_message(spend["hash"])["fee"]
        delta = self._wait_until(
            lambda: (
                self._chain_ckb(adversary) - adversary_chain_before
                if self._chain_ckb(adversary) - adversary_chain_before
                >= LONG_PATH_AMOUNT - settlement_fee
                else None
            ),
            "the adversary to receive the full-hash amount on chain",
            timeout=180,
        )
        assert (
            delta >= LONG_PATH_AMOUNT - settlement_fee
        ), f"adversary on-chain delta {delta} < {LONG_PATH_AMOUNT} - fee {settlement_fee}"

        # 证明点 8：payer 成功付款 —— 付款 Success、返回原像就是本测试的原像，且确实付了手续费。
        # 上链产出的付款查询终态由 FIBER_ASSERT_ONCHAIN_TLC_QUERY 门控；关时保留"未被判失败"与
        # 手续费已产生这两条与资金相关的核对。
        if onchain_tlc_query_enabled():
            self.wait_payment_state(payer, payment_hash, "Success", timeout=660)
            settled = payer.get_client().get_payment({"payment_hash": payment_hash})
            assert settled["status"] == "Success", settled
            assert settled["payment_preimage"] == preimage, settled
            assert settled["failed_error"] is None, settled
            assert int(settled["fee"], 16) > 0, settled
        else:
            settled = payer.get_client().get_payment({"payment_hash": payment_hash})
            assert settled["status"] != "Failed", settled
        # 证明点 9：成功后在途金额归零；实际路由金额取自付款 Committed 时的目标 TLC。
        # 付款前 offered_tlc_balance 为零，不能用作本次付款金额。
        self._wait_until(
            lambda: all(
                int(self._channel(fiber, channel_id)["offered_tlc_balance"], 16) == 0
                for index, (fiber, channel_id, direction) in enumerate(upstream_views)
                if direction == "Outbound"
            ),
            "the payer offered-TLC balances to clear",
            timeout=180,
        )
        payer_out = int(committed[0]["amount"], 16)
        relay_in = int(committed[1]["amount"], 16)
        relay_out = int(committed[2]["amount"], 16)
        assert payer_out == LONG_PATH_AMOUNT + int(settled["fee"], 16), (
            payer_out,
            settled,
        )
        assert (
            payer_out <= LONG_PATH_AMOUNT + LONG_PATH_AMOUNT // 100
        ), f"payer outbound(TLC) {payer_out} exceeds amount + relay fee allowance: {settled}"
        # 证明点 10：同一跳两端金额一致；中继收入 = 转出金额 + 非负转发费。
        assert relay_in == payer_out, (relay_in, payer_out)
        assert relay_in >= relay_out >= LONG_PATH_AMOUNT, (relay_in, relay_out, settled)

        # 证明点 6/7：付款前后余额变化方向正确，上游通道仍可用。
        # 上面的 Committed TLC 金额核对路由金额和手续费；这里独立检查余额变化。
        for index, (fiber, channel_id, direction) in enumerate(upstream_views):
            channel = self._channel(fiber, channel_id)
            before_local, before_remote = upstream_before[index]
            after_local = int(channel["local_balance"], 16)
            after_remote = int(channel["remote_balance"], 16)
            if direction == "Outbound":
                assert after_local < before_local, (index, before_local, after_local)
                assert after_remote > before_remote, (
                    index,
                    before_remote,
                    after_remote,
                )
            else:
                assert after_local > before_local, (index, before_local, after_local)
                assert after_remote < before_remote, (
                    index,
                    before_remote,
                    after_remote,
                )
            assert channel["state"]["state_name"] == "ChannelReady", channel
            assert channel.get("shutdown_transaction_hash") is None, channel

        # 证明点 5/7：付款终态与长链路可用性同时成立；对端发票对已收到的金额达到 Paid。
        assert (
            payer.get_client().get_payment({"payment_hash": payment_hash})["status"]
            == "Success"
        ), settled
        adversary_invoice = adversary.get_client().get_invoice(
            {"payment_hash": payment_hash}
        )
        assert adversary_invoice["status"] == "Paid", adversary_invoice

    # ------------------------------------------------------------- H32V2-30 helpers

    def _sha256_hex(self, preimage):
        """完整 32 字节 hash：与 _craft_prefix_hash 的坏 hash 相对照，用于 H32V2-32。"""
        return (
            "0x"
            + hashlib.sha256(bytes.fromhex(preimage.removeprefix("0x"))).hexdigest()
        )

    def _chain_ckb(self, fiber):
        """某节点账户在链上的 CKB 总量。

        通道强关并链上结算后，钱是**链上到账**到收款人锁脚本，而不是留在通道的
        local_balance 里；因此核对“对端按该笔金额到账”必须看链上 capacity。
        """
        return int(
            self.ckb.get_cells_capacity(
                {
                    "script": self.get_account_script(fiber.account_private),
                    "script_type": "lock",
                    "script_search_mode": "exact",
                }
            )["capacity"],
            16,
        )

    def _send_explicit_route(self, payer, hops, invoice, amount, mine_first=False):
        """Explicit route disables retries; an on-chain loss is not a refund.

        ``mine_first=True`` 在发款前先挖一个块：H32V2-32 随后要停掉自动矿工并注入真实原像，
        先挖块保证“发款已提交”与后续操作之间没有待确认交易竞争。H32V2-30 沿用默认行为。
        """
        if mine_first:
            self.Miner.miner_with_version(self.node, "0x0")
        route = None
        last_error = None
        for _ in range(90):
            try:
                route = payer.get_client().build_router(
                    {
                        "amount": hex(amount),
                        "hops_info": hops,
                        "final_tlc_expiry_delta": hex(FINAL_EXPIRY_DELTA),
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

    def _wait_tlc_committed(self, fiber, channel_id, payment_hash, timeout=150):
        def committed():
            tlcs = self._tlc_in(fiber, channel_id, payment_hash)
            if len(tlcs) == 1 and "Committed" in tlcs[0]["status"].values():
                return tlcs[0]
            return None

        return self._wait_until(
            committed,
            f"TLC {payment_hash} to be Committed in {channel_id} on {fiber.tmp_path}",
            timeout=timeout,
            interval=0.5,
        )
