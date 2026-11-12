"""Trampoline 下游全部链上超时退款后，上游开放通道释放本金。

正式构建按真实 TLC expiry 等待，含随机附加期限可超过 19 小时；不修改系统时间。
"""

import time

from framework.basic_fiber import COMMIT_LOCK_CODE_HASH, FiberTest
from framework.config import DEFAULT_MIN_DEPOSIT_CKB
from framework.helper.settlement_witness import SettlementWitness
from framework.util import ckb_hash
from framework.helper.channel_settlement import ChannelSettlement
from framework.helper.fiber_ops import (
    assert_upstream_payment,
    get_channel,
    wait_for_result,
)

CKB = 100_000_000
RESULT_WAIT = 420


class TestTrampolineTimeoutRefund(FiberTest):
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 3}

    def setup_method(self, method):
        super().setup_method(method)
        self.settlement = ChannelSettlement(
            self.node.getClient(), COMMIT_LOCK_CODE_HASH
        )

    # TEST-MAP: MPP1705-07
    def test_upstream_refund_after_all_downstream_timeouts(self):
        """
        1. 建立 P–T–R，两条下游通道各不足以支付全额；核实 hold 付款的两片已提交。
        2. 停止 R、强关全部下游；退款结果未知时，上游保持开放并锁住原付款。
        3. 按真实到期时间等待，逐条核对链上消费、无原像退款和 T 实际收回的本金。
        4. 全部退款确认后 420 秒内，上游失败且本金释放；原上游通道可再次付款。
        """
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
        wait_for_result(
            "P 已获知 T 的 trampoline 能力",
            lambda: any(
                n["pubkey"] == trampoline.get_pubkey()
                and "TRAMPOLINE_ROUTING_REQUIRED" in n["features"]
                for n in payer.get_client().graph_nodes({})["nodes"]
            ),
        )
        upstream_before = [get_channel(f, upstream) for f in (payer, trampoline)]
        downstream_before = [get_channel(trampoline, c) for c in downstream]
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
                    for t in get_channel(f, c)["pending_tlcs"]
                    if t["payment_hash"] == payment_hash
                ]
                for f, c, _ in ends
            ]
            if all(
                len(ts) == 1 and ts[0]["status"] == {end[2]: "Committed"}
                for ts, end in zip(rows, ends)
            ):
                return [ts[0] for ts in rows]

        tlcs = wait_for_result("两跳、两片的两端均已提交", committed)
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
            funding = self.settlement.get_output(outpoint)
            principal = int(before["local_balance"], 16) + DEFAULT_MIN_DEPOSIT_CKB
            assert int(funding["capacity"], 16) == (
                principal + int(before["remote_balance"], 16) + DEFAULT_MIN_DEPOSIT_CKB
            )
            trampoline.get_client().shutdown_channel(
                {"channel_id": channel_id, "force": True}
            )
            commitment = wait_for_result(
                "下游强关交易确认",
                lambda: self.settlement.find_spending_transaction(outpoint),
            )
            locked = [
                i
                for i, o in enumerate(commitment["outputs"])
                if o["lock"]["code_hash"] == COMMIT_LOCK_CODE_HASH
            ]
            assert len(locked) == 1, commitment
            net, fee = self.settlement.net_amounts(commitment, outpoint, locks)
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
        unknown_until = time.monotonic() + RESULT_WAIT
        while time.monotonic() < unknown_until:
            assert_upstream_payment(*self.upstream, held=True)
            for chain in chains:
                assert (
                    self.settlement.find_spending_transaction(chain["outpoint"]) is None
                ), "到期前下游结果应仍未知"
            time.sleep(5)

        # 3. 沿准确消费链累计净到账，扣除额外手续费输入，不把零钱误认成退款。
        deadline = time.monotonic() + max(0, latest_expiry - time.time()) + 1800
        while not all(c["refunded"] for c in chains):
            assert time.monotonic() < deadline, f"下游退款超时: {chains}"
            for chain in chains:
                if chain["refunded"]:
                    continue
                tx = self.settlement.find_spending_transaction(chain["outpoint"])
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
                net, fee = self.settlement.net_amounts(tx, chain["outpoint"], locks)
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
                if locked:
                    chain["outpoint"] = {
                        "tx_hash": tx["hash"],
                        "index": hex(locked[0][0]),
                    }
                    self.node.getClient().generate_epochs("0x1", wait_time=0)
                else:
                    assert chain["refunded"], chain
            # 到期前检查持单；到期后允许节点先于 indexer 看到已确认退款。
            assert_upstream_payment(
                *self.upstream,
                held=time.time()
                < min(int(c["tlc"]["expiry"], 16) / 1000 for c in chains),
            )
            if not all(c["refunded"] for c in chains):
                time.sleep(5)

        # 4. 退款传播后两端本金恢复，再在同一上游通道发新付款验证可用性。
        def upstream_refunded():
            rows, payment = assert_upstream_payment(*self.upstream, held=False)
            return payment["status"] == "Failed" and all(
                int(c[field], 16) == 0
                for c in rows
                for field in ("offered_tlc_balance", "received_tlc_balance")
            )

        wait_for_result(
            "链上退款后上游失败并释放本金", upstream_refunded, timeout=RESULT_WAIT
        )
        for fiber, before in zip((payer, trampoline), upstream_before):
            after = get_channel(fiber, upstream)
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
        wait_for_result(
            "健康付款结清",
            lambda: all(
                int(get_channel(f, upstream)[field], 16) == 0
                for f in (payer, trampoline)
                for field in ("offered_tlc_balance", "received_tlc_balance")
            ),
        )
        for fiber, before, delta in zip(
            (payer, trampoline), upstream_before, (-CKB, CKB)
        ):
            channel = get_channel(fiber, upstream)
            assert channel["state"]["state_name"] == "ChannelReady"
            assert (
                int(channel["local_balance"], 16)
                == int(before["local_balance"], 16) + delta
            )
        assert (
            payer.get_client().get_payment({"payment_hash": payment_hash})["status"]
            == "Failed"
        )
