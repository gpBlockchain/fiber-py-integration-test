"""旧通道退款结清后，同一哈希在另一通道的付款仍应成功（Fiber #1753）。"""

import time

from framework.basic_clock_fiber import BasicClockFiber
from framework.basic_fiber import COMMIT_LOCK_CODE_HASH
from framework.cluster_clock import scheduling_now
from framework.helper.channel_settlement import ChannelSettlement
from framework.helper.fiber_ops import (
    calculate_payment_hash,
    get_channel,
    restart_fiber,
    start_fiber,
    wait_for_result,
)
from framework.helper.settlement_witness import CKB


class TestSameHashPaymentAfterChannelClose(BasicClockFiber):
    # 这个用例要重启 A、B 并推进整条链；不复用普通退款用例的目录或端口。
    tmp_path_name = "tmp/clock-fiber-same-hash"
    ckb_rpc_port, ckb_p2p_port = 37114, 37125
    fiber1_rpc_port, fiber1_p2p_port = 37228, 37227
    fiber2_rpc_port, fiber2_p2p_port = 37229, 37230
    extra_fiber_rpc_port, extra_fiber_p2p_port = 37251, 37402
    fnn_log_level = "info"
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 3}

    # TEST-MAP: PSET-02
    def test_same_hash_payment_stays_success_after_refunded_channel_closes(self):
        """
        1. A 分别直连 B、C；先付清 B 200 CKB，记录 A–B 的持单前账本。
        2. B 开 100 CKB 持单；确认承诺后停 B、由 A 强关，推进集群时钟到到期点，再核实退款和失败。
        3. A 原目录重启，只连 C；B 的余额仍待领。C 用原哈希开新票，A 只提交一次。
        4. 确认 A–C 上单笔 100 CKB 已承诺，按真实调度时间保持持单 130 秒，期间付款须一直在途。
        5. 只给 C 原像；先确认新款成功、C 实收一笔 100 CKB，再恢复 B 领完原有余额。
        6. 原通道确已结清后重新按同一哈希查询：新款仍成功、原像正确，C 没有多收或少收。
        """
        # 1. 拓扑 B ← A → C：A 各出资 1,000 CKB，对端业务出资 0；两条公开通道，零路由费。
        settlement = ChannelSettlement(self.node.getClient(), COMMIT_LOCK_CODE_HASH)
        sender, old_receiver = self.fiber1, self.fiber2
        new_receiver = self.start_new_fiber(self.generate_account(10000))
        delay = 0x10000000001  # 1 epoch
        public = {"public": True, "commitment_delay_epoch": hex(delay)}
        old_id = self.open_channel(
            sender, old_receiver, 1000 * CKB, 0, 0, 0, other_config=public
        )
        new_id = self.open_channel(
            sender, new_receiver, 1000 * CKB, 0, 0, 0, other_config=public
        )
        for node in (sender, old_receiver, new_receiver):
            node.get_client().try_count, node.get_client().timeout = 1, 10
        wait_for_result(
            "付款方同步两条公开通道",
            lambda: len(sender.get_client().graph_channels({})["channels"]) >= 2,
        )
        peers = (sender, old_receiver)
        opening = [int(get_channel(f, old_id)["local_balance"], 16) for f in peers]
        prepaid = sender.get_client().send_payment(
            {
                "target_pubkey": old_receiver.get_pubkey(),
                "amount": hex(200 * CKB),
                "keysend": True,
                "max_fee_amount": "0x0",
            }
        )
        self.wait_payment_state(sender, prepaid["payment_hash"], "Success", timeout=120)
        for fiber, balance, delta in zip(peers, opening, (-200 * CKB, 200 * CKB)):
            self.wait_channel_tlcs_settled(
                fiber, old_id, timeout=120, expected_local_balance=balance + delta
            )
        old = settlement.snapshot_channel(
            sender, old_receiver, old_id, commitment_delay_epoch=delay
        )

        # 2. A → B 的原持单：原像留在测试端，只有看到原 TLC 的链上退款才接受 Failed。
        amount, algorithm = 100 * CKB, "ckb_hash"
        preimage = self.generate_random_preimage()
        payment_hash = calculate_payment_hash(preimage, algorithm)
        assert payment_hash != prepaid["payment_hash"]
        query = {"payment_hash": payment_hash}
        hold = {
            "amount": hex(amount),
            "currency": "Fibd",
            "payment_hash": payment_hash,
            "hash_algorithm": algorithm,
            "expiry": hex(86_400),
        }
        old_invoice = old_receiver.get_client().new_invoice(
            {**hold, "final_expiry_delta": hex(10_800_000)}
        )
        original = sender.get_client().send_payment(
            {
                "invoice": old_invoice["invoice_address"],
                "max_fee_amount": "0x0",
                "tlc_expiry_limit": hex(172_800_000),
            }
        )
        assert original["payment_hash"] == payment_hash, original
        self.wait_invoice_state(old_receiver, payment_hash, "Received", timeout=120)
        settlement.record_committed_tlcs(old, payment_hash)
        assert len(old["tlcs"]) == 1 and old["sent"] == amount
        old_receiver.stop()
        sender.get_client().shutdown_channel({"channel_id": old_id, "force": True})
        owner_locks = [self.get_account_script(node.account_private) for node in peers]
        chain = settlement.track_commitment(old["funding"], owner_locks)
        # 原 funding 已被确认消费后，才把节点的业务时间推进到这笔 TLC 的实际到期点。
        expiry_ms = int(old["tlcs"][0]["expiry"], 16)
        assert expiry_ms > self.cluster_clock.now_ms(), "强关确认前旧 TLC 已到期"
        self.advance_time_to(expiry_ms + 1000, mine_epochs=1)
        self.wait_chain_median_time(expiry_ms)
        pending_cell, pending_capacity = settlement.wait_direct_timeout_refund(
            old, chain, payment_hash, preimage, now_s=self.cluster_clock.now_s
        )

        def old_payment_failed():
            payment = sender.get_client().get_payment(query)
            assert payment["payment_hash"] == payment_hash, payment
            assert (
                payment["status"] != "Success"
                and payment.get("payment_preimage") is None
            ), payment
            return payment["status"] == "Failed"

        wait_for_result(
            "旧款已退款且付款方报告 Failed", old_payment_failed, timeout=420
        )
        settlement.assert_receiver_balance_pending(old, pending_cell, pending_capacity)

        # 3. 重启后仍是旧失败；不同首跳、同一哈希和金额，全程仅向 C 的新发票付款一次。
        restart_fiber(sender, peers=(new_receiver,))
        assert old_payment_failed(), "重启后旧款应仍为 Failed"
        settlement.assert_receiver_balance_pending(old, pending_cell, pending_capacity)
        for fiber in (sender, new_receiver):
            self.wait_channel_tlcs_settled(fiber, new_id, timeout=120)
        new = settlement.snapshot_channel(
            sender, new_receiver, new_id, commitment_delay_epoch=delay
        )
        assert old_id != new_id and old["funding"] != new["funding"]
        before_invoice_ms = self.cluster_clock.now_ms()
        new_invoice = new_receiver.get_client().new_invoice(
            {**hold, "final_expiry_delta": hex(43_200_000)}
        )
        issued_ms = int(new_invoice["invoice"]["data"]["timestamp"], 16)
        assert (
            before_invoice_ms - 1000 <= issued_ms <= self.cluster_clock.now_ms() + 1000
        ), "C 的发票未使用同一集群时钟"
        address = new_invoice["invoice_address"]
        assert address != old_invoice["invoice_address"]
        new_payment = sender.get_client().send_payment(
            {
                "invoice": address,
                "timeout": hex(3600),
                "max_parts": "0x1",
                "max_fee_amount": "0x0",
                "tlc_expiry_limit": hex(172_800_000),
            }
        )
        assert new_payment["payment_hash"] == payment_hash, new_payment

        # 4. C 实际收到持单，A–C 的两端都看到同一笔付款。跨过 130 秒，不提交原像。
        self.wait_invoice_state(new_receiver, payment_hash, "Received", timeout=120)
        settlement.record_committed_tlcs(new, payment_hash)
        assert len(new["tlcs"]) == 1 and new["sent"] == amount
        # 新 TLC 要覆盖持单、结算、恢复 B、链上收款及通道巡检的总预算。
        assert (
            int(new["tlcs"][0]["expiry"], 16)
            > self.cluster_clock.now_ms() + (130 + 120 + 120 + 1800 + 660) * 1000
        )
        hold_until = scheduling_now() + 130
        while True:
            payment = sender.get_client().get_payment(query)
            assert (
                payment["payment_hash"] == payment_hash
                and payment["status"] == "Inflight"
            ), payment
            assert payment.get("payment_preimage") is None, payment
            invoice = new_receiver.get_client().get_invoice(query)
            assert (
                invoice["invoice_address"] == address
                and invoice["status"] == "Received"
            ), invoice
            settlement.assert_payment_locked(new, payment_hash)
            remaining = hold_until - scheduling_now()
            if remaining <= 0:
                break
            time.sleep(min(5, remaining))

        # 5. 明确在 B 仍离线时结算 C；成功、Paid 和 A–C 双边余额齐备后才让 B 启动。
        new_receiver.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )

        def new_payment_is_paid():
            return settlement.invoice_payment_is_settled(
                new, address, payment_hash, preimage, algorithm
            )

        wait_for_result(
            "A 为 Success，C 的新发票 Paid 且只收 100 CKB",
            new_payment_is_paid,
            timeout=120,
        )
        settlement.assert_receiver_balance_pending(old, pending_cell, pending_capacity)
        start_fiber(old_receiver)
        settlement.wait_refunded_channel_close(
            old, chain, payment_hash, preimage, pending_capacity
        )

        # 6. 旧 funding 的最后一笔消费已确认，原 ID 的 Closed 等待标志已清。重新读 RPC，不重试。
        assert (
            new_payment_is_paid()
        ), f"{old_id} 已结清，但 {payment_hash} 的新付款、发票或余额不再匹配"
