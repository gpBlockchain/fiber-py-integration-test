"""Trampoline 中继在链上退款前后重启，开放的上游通道仍可收回本金。"""

import time

from framework.basic_fiber import COMMIT_LOCK_CODE_HASH
from framework.config import DEFAULT_MIN_DEPOSIT_CKB
from framework.helper.settlement_witness import SettlementWitness
from framework.util import ckb_hash
from test_cases.fiber.devnet.trampoline_routing import (
    test_trampoline_timeout_refund as refund,
)

CKB = refund.CKB
RESULT_WAIT = refund.RESULT_WAIT


class TestTrampolineTimeoutRefundRestart(refund.TestTrampolineTimeoutRefund):
    # 继承链上消费与资金核对方法；无重启用例仅由原类收集。
    test_upstream_refund_after_all_downstream_timeouts = None

    # TEST-MAP: MPP1705-08
    def test_restart_before_downstream_refunds(self):
        """
        1. 独立建立两片持单并强关下游，核实链上尚未退款、上游仍锁住本金。
        2. 保留 T 的原数据目录正常停启，P 保持在线；重连后未知结果仍不提前退款。
        3. 等真实到期并确认两片退款，420 秒内上游失败、原本金恢复且可再付款。
        """
        # 1. 强关后精确消费尚未发生；原付款在开放上游通道中保持锁定。
        before, chains, locks, expiry = self._prepare_closed_payment()
        self._assert_unknown(chains)

        # 2. 正常重启 T，核对同一通道/付款恢复，并观察一个完整结果窗。
        self._restart_trampoline(held=True)
        self._observe_unknown(chains)
        print("重启变体：下游未退款时重启已完成，上游仍锁定", flush=True)

        # 3. 复用首例的退款净额、释放本金与健康付款断言。
        deadline = self._wait_downstream_refunds(chains, locks, expiry)
        self._assert_refund_and_probe(before, deadline)

    # TEST-MAP: MPP1705-08
    def test_restart_after_downstream_refunds(self):
        """
        1. 独立建立两片持单并强关下游，未知结果期间上游不提前退款。
        2. 等真实到期，核对两条准确消费链均已无原像退款给 T。
        3. 退款确认后正常停启 T，P 保持在线；原结果窗内本金释放且不重复扣款。
        """
        # 1. 本变体独立创建通道和付款；不复用前一变体的链上退款。
        before, chains, locks, expiry = self._prepare_closed_payment()
        self._observe_unknown(chains)

        # 2. 先确认每片的退款目的、净额和无原像消费，再触发重启。
        deadline = self._wait_downstream_refunds(chains, locks, expiry)
        _, payment = self._assert_upstream(held=False)
        print(f"退款已确认，重启前上游状态: {payment['status']}", flush=True)

        # 3. 重启不重置 420 秒结果窗；已传播或尚待传播的退款都须保持正确。
        self._restart_trampoline(held=False)
        self._assert_refund_and_probe(before, deadline)

    def _prepare_closed_payment(self):
        """与无重启首例相同的公开拓扑、两片身份核对和强关前提。"""
        # 1. 由公开 RPC 建立两片付款，原像只留在测试进程。
        payer, trampoline = self.fiber1, self.fiber2
        receiver = self.start_new_fiber(self.generate_account(10000))
        for fiber in self.fibers:
            fiber.get_client().try_count = 1
            fiber.get_client().timeout = 10
        upstream = self.open_channel(payer, trampoline, 3000 * CKB, 0, 0, 0)
        downstream = [
            self.open_channel(
                trampoline, receiver, 1000 * CKB, 0, 0, other_config={"public": False}
            )
            for _ in range(2)
        ]
        self.wait_graph_channels_sync(payer, 1, timeout=120)
        self._wait(
            "P 已获知 T 的 trampoline 能力",
            lambda: any(
                n["pubkey"] == trampoline.get_pubkey()
                and "TRAMPOLINE_ROUTING_REQUIRED" in n["features"]
                for n in payer.get_client().graph_nodes({})["nodes"]
            ),
        )
        upstream_before = [self._channel(f, upstream) for f in (payer, trampoline)]
        downstream_before = [self._channel(trampoline, c) for c in downstream]
        amount = 1500 * CKB
        assert all(int(c["local_balance"], 16) < amount for c in downstream_before)
        assert sum(int(c["local_balance"], 16) for c in downstream_before) >= amount
        payment_hash = ckb_hash(self.generate_random_preimage())
        invoice = receiver.get_client().new_invoice(
            {
                "amount": hex(amount),
                "currency": "Fibd",
                "payment_hash": payment_hash,
                "hash_algorithm": "ckb_hash",
                "allow_mpp": True,
                "allow_trampoline_routing": True,
                "expiry": hex(86400),
                "final_expiry_delta": hex(10_800_000),
            }
        )
        payment = payer.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "trampoline_hops": [trampoline.get_pubkey()],
                "max_parts": hex(2),
                "max_fee_amount": hex(amount // 100),
                "tlc_expiry_limit": hex(172_800_000),
                "timeout": hex(300),
            }
        )
        assert payment["payment_hash"] == payment_hash
        self.wait_invoice_state(receiver, payment_hash, "Received", timeout=120)
        ends = [(payer, upstream, "Outbound"), (trampoline, upstream, "Inbound")]
        for channel_id in downstream:
            ends.extend(
                [
                    (trampoline, channel_id, "Outbound"),
                    (receiver, channel_id, "Inbound"),
                ]
            )

        def committed():
            rows = [
                [
                    t
                    for t in self._channel(f, c)["pending_tlcs"]
                    if t["payment_hash"] == payment_hash
                ]
                for f, c, _ in ends
            ]
            if all(
                len(ts) == 1 and ts[0]["status"] == {end[2]: "Committed"}
                for ts, end in zip(rows, ends)
            ):
                return [ts[0] for ts in rows]

        tlcs = self._wait("两跳、两片的两端均已提交", committed)
        for left, right in zip(tlcs[::2], tlcs[1::2]):
            assert (left["id"], left["amount"], left["expiry"]) == (
                right["id"],
                right["amount"],
                right["expiry"],
            )
        assert sum(int(t["amount"], 16) for t in (tlcs[2], tlcs[4])) == amount
        incoming = int(tlcs[0]["amount"], 16)
        assert amount <= incoming <= amount + amount // 100
        latest_expiry = max(int(t["expiry"], 16) for t in (tlcs[2], tlcs[4])) / 1000
        assert 600 < latest_expiry - time.time() <= 48 * 3600, tlcs
        print(
            f"两片已提交，下游最晚到期: "
            f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(latest_expiry))}",
            flush=True,
        )
        self.upstream = (payer, trampoline, upstream, payment_hash, incoming)
        locks = [
            self.get_account_script(f.account_private) for f in (trampoline, receiver)
        ]

        # 2. 强关时保留准确 funding outpoint；上游任何强关都应直接失败。
        receiver.stop()
        chains = []
        for channel_id, before, tlc in zip(
            downstream, downstream_before, (tlcs[2], tlcs[4])
        ):
            packed = bytes.fromhex(before["channel_outpoint"][2:])
            assert len(packed) == 36
            outpoint = {
                "tx_hash": "0x" + packed[:32].hex(),
                "index": hex(int.from_bytes(packed[32:], "little")),
            }
            funding = self._output(outpoint)
            principal = int(before["local_balance"], 16) + DEFAULT_MIN_DEPOSIT_CKB
            assert int(funding["capacity"], 16) == (
                principal + int(before["remote_balance"], 16) + DEFAULT_MIN_DEPOSIT_CKB
            )
            trampoline.get_client().shutdown_channel(
                {"channel_id": channel_id, "force": True}
            )
            commitment = self._wait("下游强关交易确认", lambda: self._spend(outpoint))
            locked = [
                i
                for i, o in enumerate(commitment["outputs"])
                if o["lock"]["code_hash"] == COMMIT_LOCK_CODE_HASH
            ]
            assert len(locked) == 1, commitment
            net, fee = self._net_refund(commitment, outpoint, locks)
            chains.append(
                {
                    "outpoint": {
                        "tx_hash": commitment["hash"],
                        "index": hex(locked[0]),
                    },
                    "net": net,
                    "fee": fee,
                    "principal": principal,
                    "funding": int(funding["capacity"], 16),
                    "tlc": tlc,
                    "seen_tlc": False,
                    "refunded": False,
                }
            )
        self.node.getClient().generate_epochs("0x1", wait_time=0)
        return upstream_before, chains, locks, latest_expiry

    def _assert_unknown(self, chains):
        self._assert_upstream(held=True)
        for chain in chains:
            assert self._spend(chain["outpoint"]) is None, "重启前后下游结果应仍未知"

    def _observe_unknown(self, chains):
        deadline = time.monotonic() + RESULT_WAIT
        while time.monotonic() < deadline:
            self._assert_unknown(chains)
            time.sleep(5)

    def _restart_trampoline(self, *, held):
        payer, trampoline, channel_id, payment_hash, _ = self.upstream
        rows, _ = self._assert_upstream(held=held)
        identities = [
            {t["id"] for t in c["pending_tlcs"] if t["payment_hash"] == payment_hash}
            for c in rows
        ]
        trampoline_pubkey = trampoline.get_pubkey()
        trampoline.stop()
        # T 离线期间仍通过真实 RPC 访问 P；只重启 T，不更换数据目录或二进制。
        assert payer.get_client().node_info()["pubkey"] == payer.get_pubkey()
        trampoline.start(fnn_log_level=self.fnn_log_level, timeout=120)
        assert trampoline.get_pubkey() == trampoline_pubkey
        trampoline.connect_peer(payer)
        self._wait(
            "重启后恢复同一上游通道",
            lambda: all(
                self._channel(f, channel_id)["state"]["state_name"] == "ChannelReady"
                for f in (payer, trampoline)
            ),
        )
        restored, _ = self._assert_upstream(held=held)
        for before, channel in zip(identities, restored):
            actual = {
                t["id"]
                for t in channel["pending_tlcs"]
                if t["payment_hash"] == payment_hash
            }
            assert actual <= before, (before, actual)
            if held:
                assert actual == before and len(actual) == 1

    def _wait_downstream_refunds(self, chains, locks, latest_expiry):
        payment_hash = self.upstream[3]
        # 3. 沿准确消费链累计净到账，扣除额外手续费输入，不把零钱误认成退款。
        print(
            f"等待真实 TLC 到期: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(latest_expiry))}",
            flush=True,
        )
        deadline = time.monotonic() + max(0, latest_expiry - time.time()) + 1800
        while not all(c["refunded"] for c in chains):
            assert time.monotonic() < deadline, f"下游退款超时: {chains}"
            for chain in chains:
                if chain["refunded"]:
                    continue
                tx = self._spend(chain["outpoint"])
                if tx is None:
                    continue
                input_index = next(
                    i
                    for i, entry in enumerate(tx["inputs"])
                    if entry["previous_output"] == chain["outpoint"]
                )
                witness = SettlementWitness.from_hex(
                    tx["witnesses"][input_index], version="v1"
                )
                assert witness.unlocks and all(
                    u.preimage is None for u in witness.unlocks
                )
                expected = (payment_hash, int(chain["tlc"]["amount"], 16))
                if not chain["seen_tlc"]:
                    witness.assert_pending_tlcs([expected])
                    chain["seen_tlc"] = True
                else:
                    assert all(
                        t.payment_hash == bytes.fromhex(payment_hash[2:])
                        and t.amount == expected[1]
                        for t in witness.tlcs
                    )
                    assert len(witness.tlcs) <= 1
                net, fee = self._net_refund(tx, chain["outpoint"], locks)
                chain["net"] = [a + b for a, b in zip(chain["net"], net)]
                chain["fee"] += fee
                locked = [
                    (i, o)
                    for i, o in enumerate(tx["outputs"])
                    if o["lock"]["code_hash"] == COMMIT_LOCK_CODE_HASH
                ]
                assert len(locked) <= 1
                remaining = sum(int(o["capacity"], 16) for _, o in locked)
                assert sum(chain["net"]) + remaining == chain["funding"] - chain["fee"]
                assert 0 <= chain["fee"] < CKB // 100
                assert chain["net"][0] <= chain["principal"]
                # 索引解锁应把该片金额交给 T；余额 sweep 则须交回 T 的全部本金。
                # R 离线时其保证金可仍在派生 cell，不把它误当成未退款的 TLC。
                indexed_refund = any(
                    u.unlock_type < len(witness.tlcs)
                    and witness.tlcs[u.unlock_type].payment_hash
                    == bytes.fromhex(payment_hash[2:])
                    and witness.tlcs[u.unlock_type].amount == expected[1]
                    for u in witness.unlocks
                )
                if indexed_refund:
                    assert expected[1] - fee <= net[0] <= expected[1], (tx, net, fee)
                chain["refunded"] = indexed_refund or (
                    chain["principal"] - chain["fee"] <= chain["net"][0]
                )
                print(
                    f"退款消费 {tx['hash']}: net={chain['net']} fee={chain['fee']} "
                    f"refunded={chain['refunded']}",
                    flush=True,
                )
                if locked:
                    chain["outpoint"] = {
                        "tx_hash": tx["hash"],
                        "index": hex(locked[0][0]),
                    }
                    self.node.getClient().generate_epochs("0x1", wait_time=0)
                else:
                    assert chain["refunded"], chain
            # 到期前检查持单；到期后允许节点先于 indexer 看到已确认退款。
            self._assert_upstream(
                held=time.time()
                < min(int(c["tlc"]["expiry"], 16) / 1000 for c in chains)
            )
            if not all(c["refunded"] for c in chains):
                time.sleep(5)

        return time.monotonic() + RESULT_WAIT

    def _assert_refund_and_probe(self, upstream_before, deadline):
        payer, trampoline, upstream, payment_hash, _ = self.upstream

        # 4. 退款传播后两端本金恢复，再在同一上游通道发新付款验证可用性。
        def upstream_refunded():
            rows, payment = self._assert_upstream(held=False)
            return payment["status"] == "Failed" and all(
                int(c[field], 16) == 0
                for c in rows
                for field in ("offered_tlc_balance", "received_tlc_balance")
            )

        remaining = deadline - time.monotonic()
        assert remaining > 0, "重启及恢复已耗尽退款结果观察窗"
        self._wait("链上退款后上游失败并释放本金", upstream_refunded, timeout=remaining)
        for fiber, before in zip((payer, trampoline), upstream_before):
            after = self._channel(fiber, upstream)
            for field in ("local_balance", "remote_balance"):
                assert after[field] == before[field], (before, after)
        probe = payer.get_client().send_payment(
            {
                "target_pubkey": trampoline.get_pubkey(),
                "amount": hex(CKB),
                "keysend": True,
            }
        )
        self.wait_payment_state(payer, probe["payment_hash"], "Success", timeout=120)
        self._wait(
            "健康付款结清",
            lambda: all(
                int(self._channel(f, upstream)[field], 16) == 0
                for f in (payer, trampoline)
                for field in ("offered_tlc_balance", "received_tlc_balance")
            ),
        )
        for fiber, before, delta in zip(
            (payer, trampoline), upstream_before, (-CKB, CKB)
        ):
            channel = self._channel(fiber, upstream)
            assert channel["state"]["state_name"] == "ChannelReady"
            assert (
                int(channel["local_balance"], 16)
                == int(before["local_balance"], 16) + delta
            )
        assert (
            payer.get_client().get_payment({"payment_hash": payment_hash})["status"]
            == "Failed"
        )
