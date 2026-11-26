"""付款方退款不等于通道结清：重启后等对端领完余额（Fiber #1753）。"""

from framework.basic_clock_fiber import BasicClockFiber
from framework.basic_fiber import COMMIT_LOCK_CODE_HASH
from framework.helper.channel_settlement import ChannelSettlement
from framework.helper.fiber_ops import (
    calculate_payment_hash,
    get_channel,
    restart_fiber,
    start_fiber,
    wait_for_result,
)
from framework.helper.settlement_witness import CKB


class TestDirectTimeoutRefundAfterRestart(BasicClockFiber):
    # 不复用同哈希重付用例的节点、时钟或持久化数据。
    tmp_path_name = "tmp/clock-fiber-direct-refund"
    ckb_rpc_port, ckb_p2p_port = 38114, 38125
    fiber1_rpc_port, fiber1_p2p_port = 38228, 38227
    fiber2_rpc_port, fiber2_p2p_port = 38229, 38230
    extra_fiber_rpc_port, extra_fiber_p2p_port = 38251, 38402
    fnn_log_level = "info"
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 3}

    # TEST-MAP: MPP1705-09
    # TEST-MAP: PSET-01
    def test_direct_timeout_refund_then_sender_restart_and_new_payment(self):
        """
        1. A 分别与 B、C 建立公开 CKB 通道；先付清 B 200 CKB，记下原通道账本。
        2. A 向 B 支付 100 CKB 持单；两端承诺后停 B，由 A 强关并确认原 funding 已消费。
        3. 推进集群时钟并等链上达到该 TLC 的到期点，核实无原像退款和旧付款 Failed。
        4. B 仍离线，用原目录重启 A；重启前后 B 的同一笔余额都在链上，原通道仍等待。
        5. 恢复 B，核对最后一笔承诺输出和双方到账；A 仍能查到原通道，而且已结束等待。
        6. A 通过健康通道向 C 另付 1 CKB；新款成功、余额只变一次，旧款仍 Failed 且无原像。
        """
        # 1. B ← A → C：两条通道均公开；A 各出资 1,000 CKB，对端业务出资 0，零路由费。
        settlement = ChannelSettlement(self.node.getClient(), COMMIT_LOCK_CODE_HASH)
        sender, receiver = self.fiber1, self.fiber2
        healthy = self.start_new_fiber(self.generate_account(10000))
        delay = 0x10000000001  # 1 epoch
        public = {"public": True, "commitment_delay_epoch": hex(delay)}
        old_id = self.open_channel(
            sender, receiver, 1000 * CKB, 0, 0, 0, other_config=public
        )
        healthy_id = self.open_channel(
            sender, healthy, 1000 * CKB, 0, 0, 0, other_config=public
        )
        assert healthy_id != old_id
        for node in (sender, receiver, healthy):
            node.get_client().try_count, node.get_client().timeout = 1, 10
        wait_for_result(
            "付款方同步两条公开通道",
            lambda: len(sender.get_client().graph_channels({})["channels"]) >= 2,
        )
        old_peers = (sender, receiver)
        opening = [
            int(get_channel(node, old_id)["local_balance"], 16) for node in old_peers
        ]
        prepaid = sender.get_client().send_payment(
            {
                "target_pubkey": receiver.get_pubkey(),
                "amount": hex(200 * CKB),
                "keysend": True,
                "max_fee_amount": "0x0",
            }
        )
        self.wait_payment_state(sender, prepaid["payment_hash"], "Success", timeout=120)
        for node, balance, delta in zip(old_peers, opening, (-200 * CKB, 200 * CKB)):
            self.wait_channel_tlcs_settled(
                node, old_id, timeout=120, expected_local_balance=balance + delta
            )
        old = settlement.snapshot_channel(
            sender, receiver, old_id, commitment_delay_epoch=delay
        )

        # 2. 另开持单；原像只在测试端。记录原 TLC 两端的承诺，再停 B、强关并跟踪实际 funding。
        preimage = self.generate_random_preimage()
        payment_hash = calculate_payment_hash(preimage, "ckb_hash")
        assert payment_hash != prepaid["payment_hash"]
        invoice = receiver.get_client().new_invoice(
            {
                "amount": hex(100 * CKB),
                "currency": "Fibd",
                "payment_hash": payment_hash,
                "hash_algorithm": "ckb_hash",
                "final_expiry_delta": hex(10_800_000),
                "expiry": hex(86_400),
            }
        )
        held = sender.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "tlc_expiry_limit": hex(172_800_000),
            }
        )
        assert held["payment_hash"] == payment_hash, held
        self.wait_invoice_state(receiver, payment_hash, "Received", timeout=120)
        settlement.record_committed_tlcs(old, payment_hash)
        assert len(old["tlcs"]) == 1 and old["sent"] == 100 * CKB

        def old_payment_status():
            row = sender.get_client().get_payment({"payment_hash": payment_hash})
            assert row["payment_hash"] == payment_hash, row
            assert (
                row["status"] != "Success" and row.get("payment_preimage") is None
            ), row
            return row["status"]

        old_payment_status()
        receiver.stop()
        sender.get_client().shutdown_channel({"channel_id": old_id, "force": True})
        owner_locks = [
            self.get_account_script(node.account_private) for node in old_peers
        ]
        chain = settlement.track_commitment(old["funding"], owner_locks)

        # 3. 时间跳跃不是退款证据：等链上中位时间到期后，仍须看到原 TLC 的确认消费和 A 的净到账。
        expiry_ms = int(old["tlcs"][0]["expiry"], 16)
        assert expiry_ms > self.cluster_clock.now_ms(), "原强关交易确认前 TLC 已到期"
        self.advance_time_to(expiry_ms + 1000, mine_epochs=1)
        self.wait_chain_median_time(expiry_ms)
        receiver_cell, receiver_capacity = settlement.wait_direct_timeout_refund(
            old, chain, payment_hash, preimage, now_s=self.cluster_clock.now_s
        )
        wait_for_result(
            "退款后旧付款 Failed 且无原像",
            lambda: old_payment_status() == "Failed",
            timeout=420,
        )

        # 4. B 仍离线。用重启前记录的同一 outpoint 和通道 ID，在重启前后各查一次。
        settlement.assert_receiver_balance_pending(
            old, receiver_cell, receiver_capacity
        )
        restart_fiber(sender, peers=(healthy,))
        settlement.assert_receiver_balance_pending(
            old, receiver_cell, receiver_capacity
        )
        assert old_payment_status() == "Failed"

        # 5. B 领回原有份额：按实际费用对账，再查原通道 Closed 且已清除等待标志。
        start_fiber(receiver)
        settlement.wait_refunded_channel_close(
            old, chain, payment_hash, preimage, receiver_capacity
        )

        # 6. 不重付旧款；用不同哈希向 C 发送一笔 1 CKB keysend，检查两端余额和锁定。
        healthy_peers = (sender, healthy)
        before = [
            self.wait_channel_tlcs_settled(node, healthy_id, timeout=120)
            for node in healthy_peers
        ]
        probe = sender.get_client().send_payment(
            {
                "target_pubkey": healthy.get_pubkey(),
                "amount": hex(CKB),
                "keysend": True,
                "max_fee_amount": "0x0",
            }
        )
        assert probe["payment_hash"] not in (payment_hash, prepaid["payment_hash"])
        self.wait_payment_state(sender, probe["payment_hash"], "Success", timeout=120)
        for node, row, delta in zip(healthy_peers, before, (-CKB, CKB)):
            after = self.wait_channel_tlcs_settled(
                node,
                healthy_id,
                timeout=120,
                expected_local_balance=int(row["local_balance"], 16) + delta,
            )
            assert (
                int(after["remote_balance"], 16)
                == int(row["remote_balance"], 16) - delta
            )
        assert old_payment_status() == "Failed"
