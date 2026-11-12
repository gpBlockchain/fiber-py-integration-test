"""MPP：成功证明先于最终资金交付，重启/升级和纯链上恢复。"""

from pathlib import Path
import shutil
import tempfile
import time

from framework.test_fiber import FiberConfigPath
from framework.helper.fiber_ops import start_with_confirm
from test_cases.fiber.devnet.send_payment.mpp.mpp_fixture import (
    MppFixture,
    CKB,
    RESULT_WAIT,
)
from framework.helper.fiber_ops import (
    get_channel,
    calculate_payment_hash,
    wait_for_result,
    start_fiber,
    restart_fiber,
)


class TestMppSuccessRecovery(MppFixture):
    # TEST-MAP: MPP1705-01
    def test_early_success_sha256(self):
        """
        1. 两条容量不足的通道构成 sha256 MPP，逐跳两端提交后强关一条。
        2. 暂停出块，结算发票；链上片仍未消费时发送方已成功且原像正确。
        3. 恢复出块，核对接收本金、每端净额与独立链费，不重复交付。
        """
        # 1. 两片 Committed 之后才强关。
        # 拓扑：sender ==(1,000 CKB × 2, public)==> relay --(3,000 CKB, public)--> receiver
        # 各通道左端出资、右端 0；付款 sender → receiver 为 1,500 CKB，必须拆成两片。
        self.sender, self.relay = self.fiber1, self.fiber2
        self.receiver = self.start_new_fiber(
            self.generate_account(20000), fiber_version=self.fiber_version
        )
        delay = 0x10000000001  # 1 epoch；用于公开通道参数和到期保护核对。
        public = {"public": True, "commitment_delay_epoch": hex(delay)}
        sender_relay_1 = self.open_channel(
            self.sender, self.relay, 1000 * CKB, 0, 0, 0, other_config=public
        )
        sender_relay_2 = self.open_channel(
            self.sender, self.relay, 1000 * CKB, 0, 0, 0, other_config=public
        )
        relay_receiver = self.open_channel(
            self.relay, self.receiver, 3000 * CKB, 0, 0, 0, other_config=public
        )
        # 快照只查询余额和 funding，不开通道、不发付款。
        sender_relay_1_before = self.settlement.snapshot_channel(
            self.sender, self.relay, sender_relay_1, commitment_delay_epoch=delay
        )
        sender_relay_2_before = self.settlement.snapshot_channel(
            self.sender, self.relay, sender_relay_2, commitment_delay_epoch=delay
        )
        relay_receiver_before = self.settlement.snapshot_channel(
            self.relay, self.receiver, relay_receiver, commitment_delay_epoch=delay
        )
        self.split_channels = [sender_relay_1_before, sender_relay_2_before]
        self.forward_channel = relay_receiver_before
        self.channels = [*self.split_channels, self.forward_channel]
        self.wait_payment_routes(public_channels=3)
        amount = 1500 * CKB
        self.hash_algorithm = "sha256"
        self.preimage = self.generate_random_preimage()
        self.payment_hash = calculate_payment_hash(self.preimage, self.hash_algorithm)
        invoice = self.receiver.get_client().new_invoice(
            {
                "amount": hex(amount),
                "currency": "Fibd",
                "payment_hash": self.payment_hash,
                "hash_algorithm": self.hash_algorithm,
                "allow_mpp": True,
                "allow_trampoline_routing": False,
                "expiry": hex(86400),
                "final_expiry_delta": hex(43_200_000),
            }
        )
        # 付款：sender → relay → receiver；两片，费用上限 0。
        response = self.sender.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "max_parts": hex(2),
                "timeout": hex(1800),
                "max_fee_amount": "0x0",
                "tlc_expiry_limit": hex(172_800_000),
            }
        )
        assert response["payment_hash"] == self.payment_hash
        self.wait_mpp_committed(amount)
        self.relay.get_client().shutdown_channel(
            {"channel_id": self.split_channels[0]["channel_id"], "force": True}
        )
        chain = self.track_closed_channel(self.split_channels[0])

        # 2. 保留稳定的链上未完成窗口，不以最终成功代替提前成功。
        self.node.stop_miner()
        try:
            self.receiver.get_client().settle_invoice(
                {"payment_hash": self.payment_hash, "payment_preimage": self.preimage}
            )
            self.wait_invoice_state(
                self.receiver, self.payment_hash, "Paid", timeout=120
            )
            self.assert_payment_success(self.sender)
            assert self.settlement.find_spending_transaction(chain["outpoint"]) is None
        finally:
            self.node.start_miner()

        # 3. 原付款精确消费链和开放通道都须只交付应有金额。
        self.assert_payment_fully_settled(chain)

    # TEST-MAP: MPP1705-01
    def test_early_success_ckb_hash(self):
        """
        1. 以独立 CKB hash 构建两片 MPP 并强关其中一条。
        2. 暂停出块，交出原像，断言 P 提前成功且链上片尚未完成。
        3. 恢复出块核对原付款全路径资金及手续费。
        """
        # 1. 输入只改变 hash 算法，资金与分片前提保持一致。
        # 拓扑：sender ==(1,000 CKB × 2, public)==> relay --(3,000 CKB, public)--> receiver
        # 各通道左端出资、右端 0；付款 sender → receiver 为 1,500 CKB，必须拆成两片。
        self.sender, self.relay = self.fiber1, self.fiber2
        self.receiver = self.start_new_fiber(
            self.generate_account(20000), fiber_version=self.fiber_version
        )
        delay = 0x10000000001  # 1 epoch；用于公开通道参数和到期保护核对。
        public = {"public": True, "commitment_delay_epoch": hex(delay)}
        sender_relay_1 = self.open_channel(
            self.sender, self.relay, 1000 * CKB, 0, 0, 0, other_config=public
        )
        sender_relay_2 = self.open_channel(
            self.sender, self.relay, 1000 * CKB, 0, 0, 0, other_config=public
        )
        relay_receiver = self.open_channel(
            self.relay, self.receiver, 3000 * CKB, 0, 0, 0, other_config=public
        )
        # 快照只查询余额和 funding，不开通道、不发付款。
        sender_relay_1_before = self.settlement.snapshot_channel(
            self.sender, self.relay, sender_relay_1, commitment_delay_epoch=delay
        )
        sender_relay_2_before = self.settlement.snapshot_channel(
            self.sender, self.relay, sender_relay_2, commitment_delay_epoch=delay
        )
        relay_receiver_before = self.settlement.snapshot_channel(
            self.relay, self.receiver, relay_receiver, commitment_delay_epoch=delay
        )
        self.split_channels = [sender_relay_1_before, sender_relay_2_before]
        self.forward_channel = relay_receiver_before
        self.channels = [*self.split_channels, self.forward_channel]
        self.wait_payment_routes(public_channels=3)
        amount = 1500 * CKB
        self.hash_algorithm = "ckb_hash"
        self.preimage = self.generate_random_preimage()
        self.payment_hash = calculate_payment_hash(self.preimage, self.hash_algorithm)
        invoice = self.receiver.get_client().new_invoice(
            {
                "amount": hex(amount),
                "currency": "Fibd",
                "payment_hash": self.payment_hash,
                "hash_algorithm": self.hash_algorithm,
                "allow_mpp": True,
                "allow_trampoline_routing": False,
                "expiry": hex(86400),
                "final_expiry_delta": hex(43_200_000),
            }
        )
        # 付款：sender → relay → receiver；两片，费用上限 0。
        response = self.sender.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "max_parts": hex(2),
                "timeout": hex(1800),
                "max_fee_amount": "0x0",
                "tlc_expiry_limit": hex(172_800_000),
            }
        )
        assert response["payment_hash"] == self.payment_hash
        self.wait_mpp_committed(amount)
        self.relay.get_client().shutdown_channel(
            {"channel_id": self.split_channels[0]["channel_id"], "force": True}
        )
        chain = self.track_closed_channel(self.split_channels[0])

        # 2. 成功查询与链上未消费证据必须同时成立。
        self.node.stop_miner()
        try:
            self.receiver.get_client().settle_invoice(
                {"payment_hash": self.payment_hash, "payment_preimage": self.preimage}
            )
            self.wait_invoice_state(
                self.receiver, self.payment_hash, "Paid", timeout=120
            )
            self.assert_payment_success(self.sender)
            assert self.settlement.find_spending_transaction(chain["outpoint"]) is None
        finally:
            self.node.start_miner()

        # 3. 不以 RPC Success 替代实际交付。
        self.assert_payment_fully_settled(chain)

    # TEST-MAP: MPP1705-02
    def test_trampoline_settles_upstream_before_chain_finishes(self):
        """
        1. P–T 两片转付给 R，冻结含正费用预算的上游金额并强关下游一条。
        2. 暂停链，R 结算后 T/P 均成功且开放上游只扣一次、锁定释放。
        3. 恢复链，核对 R 本金和 T 扣除实际链费后的全路径资金。
        """
        # 1. 明确走 trampoline，禁止旁路与单片付款冒充。
        # 拓扑：sender --(3,000 CKB, public)--> relay ==(1,000 CKB × 2, private)==> receiver
        # 各通道左端出资、右端 0；付款 sender → receiver 为 1,500 CKB，必须拆成两片。
        self.sender, self.relay = self.fiber1, self.fiber2
        self.receiver = self.start_new_fiber(
            self.generate_account(20000), fiber_version=self.fiber_version
        )
        delay = 0x10000000001  # 1 epoch；用于公开通道参数和到期保护核对。
        public = {"public": True, "commitment_delay_epoch": hex(delay)}
        private = {"public": False, "commitment_delay_epoch": hex(delay)}
        sender_relay = self.open_channel(
            self.sender, self.relay, 3000 * CKB, 0, 0, 0, other_config=public
        )
        relay_receiver_1 = self.open_channel(
            self.relay, self.receiver, 1000 * CKB, 0, 0, 1000, other_config=private
        )
        relay_receiver_2 = self.open_channel(
            self.relay, self.receiver, 1000 * CKB, 0, 0, 1000, other_config=private
        )
        # 快照只查询余额和 funding，不开通道、不发付款。
        sender_relay_before = self.settlement.snapshot_channel(
            self.sender, self.relay, sender_relay, commitment_delay_epoch=delay
        )
        relay_receiver_1_before = self.settlement.snapshot_channel(
            self.relay, self.receiver, relay_receiver_1, commitment_delay_epoch=delay
        )
        relay_receiver_2_before = self.settlement.snapshot_channel(
            self.relay, self.receiver, relay_receiver_2, commitment_delay_epoch=delay
        )
        self.upstream_channel = sender_relay_before
        self.split_channels = [relay_receiver_1_before, relay_receiver_2_before]
        self.channels = [self.upstream_channel, *self.split_channels]
        self.wait_payment_routes(public_channels=1, trampoline=self.relay)
        amount = 1500 * CKB
        self.hash_algorithm = "ckb_hash"
        self.preimage = self.generate_random_preimage()
        self.payment_hash = calculate_payment_hash(self.preimage, self.hash_algorithm)
        invoice = self.receiver.get_client().new_invoice(
            {
                "amount": hex(amount),
                "currency": "Fibd",
                "payment_hash": self.payment_hash,
                "hash_algorithm": self.hash_algorithm,
                "allow_mpp": True,
                "allow_trampoline_routing": True,
                "expiry": hex(86400),
                "final_expiry_delta": hex(43_200_000),
            }
        )
        # 付款：sender → relay（trampoline）→ receiver；两片，费用上限 15 CKB。
        response = self.sender.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "max_parts": hex(2),
                "timeout": hex(1800),
                "max_fee_amount": hex(amount // 100),
                "tlc_expiry_limit": hex(172_800_000),
                "trampoline_hops": [self.relay.get_pubkey()],
            }
        )
        assert response["payment_hash"] == self.payment_hash
        self.wait_mpp_committed(amount, upstream_channel=self.upstream_channel)
        self.receiver.get_client().shutdown_channel(
            {"channel_id": self.split_channels[0]["channel_id"], "force": True}
        )
        chain = self.track_closed_channel(self.split_channels[0])

        # 2. 下游仍待链上交付时，上游已经结清。
        self.node.stop_miner()
        try:
            self.receiver.get_client().settle_invoice(
                {"payment_hash": self.payment_hash, "payment_preimage": self.preimage}
            )
            self.wait_invoice_state(
                self.receiver, self.payment_hash, "Paid", timeout=120
            )
            self.assert_payment_success(self.relay)
            self.assert_payment_success(self.sender)
            wait_for_result(
                "上游只结算一次",
                lambda: self.settlement.payment_is_settled(
                    self.upstream_channel, self.payment_hash
                ),
                RESULT_WAIT,
            )
            assert self.settlement.find_spending_transaction(chain["outpoint"]) is None
        finally:
            self.node.start_miner()

        # 3. 各通道守恒和分所有者链费共同证明中继无本金损失。
        self.assert_payment_fully_settled(chain)

    # TEST-MAP: MPP1705-03
    def test_sender_restart_preserves_success_and_remaining_work(self):
        """
        1. 建立 P 已成功且一个分片尚在链上的状态。
        2. 保留数据目录重启 P，原 hash/原像仍成功且链上片仍未完成。
        3. 恢复链并核对只交付一次，再通过原健康通道付款。
        """
        # 1. 强关并暂停出块，交出原像后观察早期成功。
        # 拓扑：sender ==(1,000 CKB × 2, public)==> relay --(3,000 CKB, public)--> receiver
        # 各通道左端出资、右端 0；付款 sender → receiver 为 1,500 CKB，必须拆成两片。
        self.sender, self.relay = self.fiber1, self.fiber2
        self.receiver = self.start_new_fiber(
            self.generate_account(20000), fiber_version=self.fiber_version
        )
        delay = 0x10000000001  # 1 epoch；用于公开通道参数和到期保护核对。
        public = {"public": True, "commitment_delay_epoch": hex(delay)}
        sender_relay_1 = self.open_channel(
            self.sender, self.relay, 1000 * CKB, 0, 0, 0, other_config=public
        )
        sender_relay_2 = self.open_channel(
            self.sender, self.relay, 1000 * CKB, 0, 0, 0, other_config=public
        )
        relay_receiver = self.open_channel(
            self.relay, self.receiver, 3000 * CKB, 0, 0, 0, other_config=public
        )
        # 快照只查询余额和 funding，不开通道、不发付款。
        sender_relay_1_before = self.settlement.snapshot_channel(
            self.sender, self.relay, sender_relay_1, commitment_delay_epoch=delay
        )
        sender_relay_2_before = self.settlement.snapshot_channel(
            self.sender, self.relay, sender_relay_2, commitment_delay_epoch=delay
        )
        relay_receiver_before = self.settlement.snapshot_channel(
            self.relay, self.receiver, relay_receiver, commitment_delay_epoch=delay
        )
        self.split_channels = [sender_relay_1_before, sender_relay_2_before]
        self.forward_channel = relay_receiver_before
        self.channels = [*self.split_channels, self.forward_channel]
        self.wait_payment_routes(public_channels=3)
        amount = 1500 * CKB
        self.hash_algorithm = "ckb_hash"
        self.preimage = self.generate_random_preimage()
        self.payment_hash = calculate_payment_hash(self.preimage, self.hash_algorithm)
        invoice = self.receiver.get_client().new_invoice(
            {
                "amount": hex(amount),
                "currency": "Fibd",
                "payment_hash": self.payment_hash,
                "hash_algorithm": self.hash_algorithm,
                "allow_mpp": True,
                "allow_trampoline_routing": False,
                "expiry": hex(86400),
                "final_expiry_delta": hex(43_200_000),
            }
        )
        # 付款：sender → relay → receiver；两片，费用上限 0。
        response = self.sender.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "max_parts": hex(2),
                "timeout": hex(1800),
                "max_fee_amount": "0x0",
                "tlc_expiry_limit": hex(172_800_000),
            }
        )
        assert response["payment_hash"] == self.payment_hash
        self.wait_mpp_committed(amount)
        closed = max(self.split_channels, key=lambda edge: edge["sent"])
        healthy = next(edge for edge in self.split_channels if edge is not closed)
        self.relay.get_client().shutdown_channel(
            {"channel_id": closed["channel_id"], "force": True}
        )
        chain = self.track_closed_channel(closed)
        self.node.stop_miner()
        try:
            self.receiver.get_client().settle_invoice(
                {"payment_hash": self.payment_hash, "payment_preimage": self.preimage}
            )
            self.wait_invoice_state(
                self.receiver, self.payment_hash, "Paid", timeout=120
            )
            self.assert_payment_success(self.sender)
            assert self.settlement.find_spending_transaction(chain["outpoint"]) is None

            # 2. 重启不重新 send_payment，也不替换数据。
            restart_fiber(self.sender, peers=(self.relay,))
            self.assert_payment_success(self.sender)
            assert self.settlement.find_spending_transaction(chain["outpoint"]) is None
        finally:
            self.node.start_miner()

        # 3. 原款完整交付后健康通道再付一笔，原款仍保持成功。
        self.assert_payment_fully_settled(chain)
        # 新付款：sender → relay，1 CKB keysend，费用上限 0。
        probe_before = [
            get_channel(f, healthy["channel_id"]) for f in (self.sender, self.relay)
        ]
        probe_payment = self.sender.get_client().send_payment(
            {
                "target_pubkey": self.relay.get_pubkey(),
                "amount": hex(CKB),
                "keysend": True,
                "max_fee_amount": "0x0",
            }
        )
        self.assert_healthy_payment_settled(
            self.sender,
            self.relay,
            healthy,
            probe_before,
            probe_payment["payment_hash"],
            CKB,
        )
        self.assert_payment_success(self.sender)

    # TEST-MAP: MPP1705-04
    def test_offline_upstream_and_trampoline_restart_settle_once(self):
        """
        1. 两片提交后停 P，R 强关一条并结算，T 在链未完成时取得成功证明。
        2. 重启 T，再恢复 P；开放上游用原付款只结算一次。
        3. 完成下游交付，再次重启 T/P 并核对余额和锁定不变化。
        """
        # 1. P 在原像交给 R 前停机。
        # 拓扑：sender --(3,000 CKB, public)--> relay ==(1,000 CKB × 2, private)==> receiver
        # 各通道左端出资、右端 0；付款 sender → receiver 为 1,500 CKB，必须拆成两片。
        self.sender, self.relay = self.fiber1, self.fiber2
        self.receiver = self.start_new_fiber(
            self.generate_account(20000), fiber_version=self.fiber_version
        )
        delay = 0x10000000001  # 1 epoch；用于公开通道参数和到期保护核对。
        public = {"public": True, "commitment_delay_epoch": hex(delay)}
        private = {"public": False, "commitment_delay_epoch": hex(delay)}
        sender_relay = self.open_channel(
            self.sender, self.relay, 3000 * CKB, 0, 0, 0, other_config=public
        )
        relay_receiver_1 = self.open_channel(
            self.relay, self.receiver, 1000 * CKB, 0, 0, 1000, other_config=private
        )
        relay_receiver_2 = self.open_channel(
            self.relay, self.receiver, 1000 * CKB, 0, 0, 1000, other_config=private
        )
        # 快照只查询余额和 funding，不开通道、不发付款。
        sender_relay_before = self.settlement.snapshot_channel(
            self.sender, self.relay, sender_relay, commitment_delay_epoch=delay
        )
        relay_receiver_1_before = self.settlement.snapshot_channel(
            self.relay, self.receiver, relay_receiver_1, commitment_delay_epoch=delay
        )
        relay_receiver_2_before = self.settlement.snapshot_channel(
            self.relay, self.receiver, relay_receiver_2, commitment_delay_epoch=delay
        )
        self.upstream_channel = sender_relay_before
        self.split_channels = [relay_receiver_1_before, relay_receiver_2_before]
        self.channels = [self.upstream_channel, *self.split_channels]
        self.wait_payment_routes(public_channels=1, trampoline=self.relay)
        amount = 1500 * CKB
        self.hash_algorithm = "ckb_hash"
        self.preimage = self.generate_random_preimage()
        self.payment_hash = calculate_payment_hash(self.preimage, self.hash_algorithm)
        invoice = self.receiver.get_client().new_invoice(
            {
                "amount": hex(amount),
                "currency": "Fibd",
                "payment_hash": self.payment_hash,
                "hash_algorithm": self.hash_algorithm,
                "allow_mpp": True,
                "allow_trampoline_routing": True,
                "expiry": hex(86400),
                "final_expiry_delta": hex(43_200_000),
            }
        )
        # 付款：sender → relay（trampoline）→ receiver；两片，费用上限 15 CKB。
        response = self.sender.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "max_parts": hex(2),
                "timeout": hex(1800),
                "max_fee_amount": hex(amount // 100),
                "tlc_expiry_limit": hex(172_800_000),
                "trampoline_hops": [self.relay.get_pubkey()],
            }
        )
        assert response["payment_hash"] == self.payment_hash
        self.wait_mpp_committed(amount, upstream_channel=self.upstream_channel)
        self.sender.stop()
        self.receiver.get_client().shutdown_channel(
            {"channel_id": self.split_channels[0]["channel_id"], "force": True}
        )
        chain = self.track_closed_channel(self.split_channels[0])
        self.node.stop_miner()
        try:
            self.receiver.get_client().settle_invoice(
                {"payment_hash": self.payment_hash, "payment_preimage": self.preimage}
            )
            self.wait_invoice_state(
                self.receiver, self.payment_hash, "Paid", timeout=120
            )
            self.assert_payment_success(self.relay)
            assert self.settlement.find_spending_transaction(chain["outpoint"]) is None

            # 2. T 的原数据重启后才恢复上游。
            restart_fiber(self.relay, peers=(self.receiver,))
            self.assert_payment_success(self.relay)
            start_fiber(self.sender, peers=(self.relay,))
            self.assert_payment_success(self.sender)
            wait_for_result(
                "离线上游恢复后一次结算",
                lambda: self.settlement.payment_is_settled(
                    self.upstream_channel, self.payment_hash
                ),
                RESULT_WAIT,
            )
        finally:
            self.node.start_miner()

        # 3. 第二次重启后仍按原始余额验证，而不是把错误新余额当基准。
        self.assert_payment_fully_settled(chain)
        restart_fiber(self.relay, peers=(self.receiver, self.sender))
        restart_fiber(self.sender, peers=(self.relay,))
        self.assert_payment_success(self.sender)
        for edge in (self.upstream_channel, self.split_channels[1]):
            wait_for_result(
                "再次重启无重放扣款",
                lambda e=edge: self.settlement.payment_is_settled(e, self.payment_hash),
                RESULT_WAIT,
            )

    # TEST-MAP: MPP1705-05
    def test_sender_recovers_preimage_only_from_chain(self):
        """
        1. P 无原像时停机，M 强关一条 P–M 后由 R 结算。
        2. 确认原像领取的链上消费，再停 M；P 无 Fiber 对端启动并恢复成功。
        3. 恢复 M，原付款继续成功且全路径金额正确。
        """
        # 1. 原像只在 P 停机后交给 R。
        # 拓扑：sender ==(1,000 CKB × 2, public)==> relay --(3,000 CKB, public)--> receiver
        # 各通道左端出资、右端 0；付款 sender → receiver 为 1,500 CKB，必须拆成两片。
        self.sender, self.relay = self.fiber1, self.fiber2
        self.receiver = self.start_new_fiber(
            self.generate_account(20000), fiber_version=self.fiber_version
        )
        delay = 0x10000000001  # 1 epoch；用于公开通道参数和到期保护核对。
        public = {"public": True, "commitment_delay_epoch": hex(delay)}
        sender_relay_1 = self.open_channel(
            self.sender, self.relay, 1000 * CKB, 0, 0, 0, other_config=public
        )
        sender_relay_2 = self.open_channel(
            self.sender, self.relay, 1000 * CKB, 0, 0, 0, other_config=public
        )
        relay_receiver = self.open_channel(
            self.relay, self.receiver, 3000 * CKB, 0, 0, 0, other_config=public
        )
        # 快照只查询余额和 funding，不开通道、不发付款。
        sender_relay_1_before = self.settlement.snapshot_channel(
            self.sender, self.relay, sender_relay_1, commitment_delay_epoch=delay
        )
        sender_relay_2_before = self.settlement.snapshot_channel(
            self.sender, self.relay, sender_relay_2, commitment_delay_epoch=delay
        )
        relay_receiver_before = self.settlement.snapshot_channel(
            self.relay, self.receiver, relay_receiver, commitment_delay_epoch=delay
        )
        self.split_channels = [sender_relay_1_before, sender_relay_2_before]
        self.forward_channel = relay_receiver_before
        self.channels = [*self.split_channels, self.forward_channel]
        self.wait_payment_routes(public_channels=3)
        amount = 1500 * CKB
        self.hash_algorithm = "ckb_hash"
        self.preimage = self.generate_random_preimage()
        self.payment_hash = calculate_payment_hash(self.preimage, self.hash_algorithm)
        invoice = self.receiver.get_client().new_invoice(
            {
                "amount": hex(amount),
                "currency": "Fibd",
                "payment_hash": self.payment_hash,
                "hash_algorithm": self.hash_algorithm,
                "allow_mpp": True,
                "allow_trampoline_routing": False,
                "expiry": hex(86400),
                "final_expiry_delta": hex(43_200_000),
            }
        )
        # 付款：sender → relay → receiver；两片，费用上限 0。
        response = self.sender.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "max_parts": hex(2),
                "timeout": hex(1800),
                "max_fee_amount": "0x0",
                "tlc_expiry_limit": hex(172_800_000),
            }
        )
        assert response["payment_hash"] == self.payment_hash
        self.wait_mpp_committed(amount)
        self.assert_no_preimage(self.sender)
        self.sender.stop()
        self.relay.get_client().shutdown_channel(
            {"channel_id": self.split_channels[0]["channel_id"], "force": True}
        )
        chain = self.track_closed_channel(self.split_channels[0])
        self.receiver.get_client().settle_invoice(
            {"payment_hash": self.payment_hash, "payment_preimage": self.preimage}
        )
        self.wait_invoice_state(self.receiver, self.payment_hash, "Paid", timeout=120)

        # 2. 先证明原像已实际用于原 TLC 领取，再隔离 P 的唯一对端。
        self.wait_for_chain_settlement(chain, claimed_only=True)
        assert chain["claimed"]
        self.relay.stop()
        # R 也暂时离线，避免已持久化的 gossip 地址自动接入 P。
        self.receiver.stop()
        start_fiber(self.sender)
        self.assert_payment_success(self.sender, isolated=True)
        assert self.sender.get_client().list_peers()["peers"] == []

        # 3. 重连仅负责剩余结算，不作为原像来源证明。
        start_fiber(self.receiver)
        start_fiber(self.relay, peers=(self.sender, self.receiver))
        self.assert_payment_fully_settled(chain)

    # TEST-MAP: MPP1705-13
    def test_trampoline_chain_preimage_then_offline_upstream(self):
        """
        1. P/T 都无原像时停机；R 强关一片并结算，证明实际链上原像领取。
        2. 停 R，T 无 Fiber 对端启动，从首次 node_info 后 420 秒内恢复正确成功。
        3. R 仍停机时只恢复 P，上游开放通道只结算一次，再恢复 R 核对全部资金。
        """
        # 1. 冻结 U=A+F，原像未交给任意节点前停止 P/T。
        # 拓扑：sender --(3,000 CKB, public)--> relay ==(1,000 CKB × 2, private)==> receiver
        # 各通道左端出资、右端 0；付款 sender → receiver 为 1,500 CKB，必须拆成两片。
        self.sender, self.relay = self.fiber1, self.fiber2
        self.receiver = self.start_new_fiber(
            self.generate_account(20000), fiber_version=self.fiber_version
        )
        delay = 0x10000000001  # 1 epoch；用于公开通道参数和到期保护核对。
        public = {"public": True, "commitment_delay_epoch": hex(delay)}
        private = {"public": False, "commitment_delay_epoch": hex(delay)}
        sender_relay = self.open_channel(
            self.sender, self.relay, 3000 * CKB, 0, 0, 0, other_config=public
        )
        relay_receiver_1 = self.open_channel(
            self.relay, self.receiver, 1000 * CKB, 0, 0, 1000, other_config=private
        )
        relay_receiver_2 = self.open_channel(
            self.relay, self.receiver, 1000 * CKB, 0, 0, 1000, other_config=private
        )
        # 快照只查询余额和 funding，不开通道、不发付款。
        sender_relay_before = self.settlement.snapshot_channel(
            self.sender, self.relay, sender_relay, commitment_delay_epoch=delay
        )
        relay_receiver_1_before = self.settlement.snapshot_channel(
            self.relay, self.receiver, relay_receiver_1, commitment_delay_epoch=delay
        )
        relay_receiver_2_before = self.settlement.snapshot_channel(
            self.relay, self.receiver, relay_receiver_2, commitment_delay_epoch=delay
        )
        self.upstream_channel = sender_relay_before
        self.split_channels = [relay_receiver_1_before, relay_receiver_2_before]
        self.channels = [self.upstream_channel, *self.split_channels]
        self.wait_payment_routes(public_channels=1, trampoline=self.relay)
        amount = 1500 * CKB
        self.hash_algorithm = "ckb_hash"
        self.preimage = self.generate_random_preimage()
        self.payment_hash = calculate_payment_hash(self.preimage, self.hash_algorithm)
        invoice = self.receiver.get_client().new_invoice(
            {
                "amount": hex(amount),
                "currency": "Fibd",
                "payment_hash": self.payment_hash,
                "hash_algorithm": self.hash_algorithm,
                "allow_mpp": True,
                "allow_trampoline_routing": True,
                "expiry": hex(86400),
                "final_expiry_delta": hex(43_200_000),
            }
        )
        # 付款：sender → relay（trampoline）→ receiver；两片，费用上限 15 CKB。
        response = self.sender.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "max_parts": hex(2),
                "timeout": hex(1800),
                "max_fee_amount": hex(amount // 100),
                "tlc_expiry_limit": hex(172_800_000),
                "trampoline_hops": [self.relay.get_pubkey()],
            }
        )
        assert response["payment_hash"] == self.payment_hash
        self.wait_mpp_committed(amount, upstream_channel=self.upstream_channel)
        self.assert_no_preimage(self.sender)
        self.assert_no_preimage(self.relay)
        self.sender.stop()
        self.relay.stop()
        self.receiver.get_client().shutdown_channel(
            {"channel_id": self.split_channels[0]["channel_id"], "force": True}
        )
        chain = self.track_closed_channel(self.split_channels[0])
        # 对端全部离线时 Paid 可能要等实际领取；先登记原像并推进链。
        self.receiver.get_client().settle_invoice(
            {"payment_hash": self.payment_hash, "payment_preimage": self.preimage}
        )
        self.wait_for_chain_settlement(chain, claimed_only=True)
        assert chain["claimed"]

        # 2. 隔离来源阶段禁止 Fiber 重连，也不向 T 调用提供原像的 RPC。
        self.receiver.stop()
        started = start_fiber(self.relay)
        self.assert_payment_success(
            self.relay,
            isolated=True,
            timeout=RESULT_WAIT - (time.monotonic() - started),
        )
        rows = self.settlement.assert_channel_open(
            self.upstream_channel, offline=(self.sender,)
        )
        assert int(rows[0]["received_tlc_balance"], 16) == self.upstream_channel["sent"]
        assert {
            (t["id"], t["amount"])
            for t in rows[0]["pending_tlcs"]
            if t["payment_hash"] == self.payment_hash
        } == {(t["id"], t["amount"]) for t in self.upstream_channel["tlcs"]}
        self.assert_expiry_margin(2 * RESULT_WAIT)

        # 3. R 离线时上游已领取，随后才恢复剩余下游。
        start_fiber(self.sender, peers=(self.relay,))
        self.assert_payment_success(self.sender)
        wait_for_result(
            "R 离线时上游结清",
            lambda: self.settlement.payment_is_settled(
                self.upstream_channel, self.payment_hash
            ),
            RESULT_WAIT,
        )
        assert {p["pubkey"] for p in self.relay.get_client().list_peers()["peers"]} == {
            self.sender.get_pubkey()
        }
        start_fiber(self.receiver, peers=(self.relay,))
        self.assert_payment_fully_settled(chain)


class TestMppUpgrade(MppFixture):
    fiber_version = FiberConfigPath.V091_DEV

    # TEST-MAP: MPP1705-10
    def test_upgrade_partial_success_and_paginated_queries(self):
        """
        1. 0.9.1 经正常操作生成成功/失败对照和已有原像、链上片未完成的旧付款。
        2. 停 P 并备份原数据，升级同一副本到 CURRENT_DEV，查询须显示原像及 Success。
        3. 按 limit=1 遍历无过滤/各状态列表，核对集合无漏重，再核对原款交付。
        """
        # 1. 独立控制通道不通往目标 R，既不改变两片路由也不改写数据库。
        control = self.start_new_fiber(
            self.generate_account(10000), fiber_version=self.fiber_version
        )
        # 独立对照：sender --200 CKB--> control；后续分别发送成功和取消发票付款。
        delay = 0x10000000001  # 1 epoch；用于公开通道参数和到期保护核对。
        public = {"public": True, "commitment_delay_epoch": hex(delay)}
        control_channel = self.open_channel(
            self.fiber1, control, 200 * CKB, 0, 0, 0, other_config=public
        )
        ok = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": control.get_pubkey(),
                "amount": hex(CKB),
                "keysend": True,
            }
        )
        self.wait_payment_state(self.fiber1, ok["payment_hash"], "Success", timeout=120)
        bad_hash = calculate_payment_hash(self.generate_random_preimage(), "ckb_hash")
        invoice = control.get_client().new_invoice(
            {
                "amount": hex(CKB),
                "currency": "Fibd",
                "payment_hash": bad_hash,
                "hash_algorithm": "ckb_hash",
            }
        )
        self.fiber1.get_client().send_payment({"invoice": invoice["invoice_address"]})
        self.wait_invoice_state(control, bad_hash, "Received", timeout=120)
        control.get_client().cancel_invoice({"payment_hash": bad_hash})
        self.wait_payment_state(self.fiber1, bad_hash, "Failed", timeout=RESULT_WAIT)
        # 拓扑：sender ==(1,000 CKB × 2, public)==> relay --(3,000 CKB, public)--> receiver
        # 各通道左端出资、右端 0；付款 sender → receiver 为 1,500 CKB，必须拆成两片。
        self.sender, self.relay = self.fiber1, self.fiber2
        self.receiver = self.start_new_fiber(
            self.generate_account(20000), fiber_version=self.fiber_version
        )
        sender_relay_1 = self.open_channel(
            self.sender, self.relay, 1000 * CKB, 0, 0, 0, other_config=public
        )
        sender_relay_2 = self.open_channel(
            self.sender, self.relay, 1000 * CKB, 0, 0, 0, other_config=public
        )
        relay_receiver = self.open_channel(
            self.relay, self.receiver, 3000 * CKB, 0, 0, 0, other_config=public
        )
        # 快照只查询余额和 funding，不开通道、不发付款。
        sender_relay_1_before = self.settlement.snapshot_channel(
            self.sender, self.relay, sender_relay_1, commitment_delay_epoch=delay
        )
        sender_relay_2_before = self.settlement.snapshot_channel(
            self.sender, self.relay, sender_relay_2, commitment_delay_epoch=delay
        )
        relay_receiver_before = self.settlement.snapshot_channel(
            self.relay, self.receiver, relay_receiver, commitment_delay_epoch=delay
        )
        self.split_channels = [sender_relay_1_before, sender_relay_2_before]
        self.forward_channel = relay_receiver_before
        self.channels = [*self.split_channels, self.forward_channel]
        self.wait_payment_routes(public_channels=3)
        amount = 1500 * CKB
        self.hash_algorithm = "ckb_hash"
        self.preimage = self.generate_random_preimage()
        self.payment_hash = calculate_payment_hash(self.preimage, self.hash_algorithm)
        invoice = self.receiver.get_client().new_invoice(
            {
                "amount": hex(amount),
                "currency": "Fibd",
                "payment_hash": self.payment_hash,
                "hash_algorithm": self.hash_algorithm,
                "allow_mpp": True,
                "allow_trampoline_routing": False,
                "expiry": hex(86400),
                "final_expiry_delta": hex(43_200_000),
            }
        )
        # 付款：sender → relay → receiver；两片，费用上限 0。
        response = self.sender.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "max_parts": hex(2),
                "timeout": hex(1800),
                "max_fee_amount": "0x0",
                "tlc_expiry_limit": hex(172_800_000),
            }
        )
        assert response["payment_hash"] == self.payment_hash
        self.wait_mpp_committed(amount)
        self.relay.get_client().shutdown_channel(
            {"channel_id": self.split_channels[0]["channel_id"], "force": True}
        )
        chain = self.track_closed_channel(self.split_channels[0])
        self.node.stop_miner()
        try:
            self.receiver.get_client().settle_invoice(
                {"payment_hash": self.payment_hash, "payment_preimage": self.preimage}
            )
            self.wait_invoice_state(
                self.receiver, self.payment_hash, "Paid", timeout=120
            )
            wait_for_result("旧版已获原像但尚未成功", self._old_partial)
            assert self.settlement.find_spending_transaction(chain["outpoint"]) is None

            # 2. 停机复制完整目录；临时备份由测试清理，不阻塞后续重复运行。
            self.sender.stop()
            backup = tempfile.TemporaryDirectory(
                prefix="mpp-upgrade-", dir=Path(self.sender.tmp_path).parent
            )
            self.addCleanup(backup.cleanup)
            shutil.copytree(self.sender.tmp_path, Path(backup.name) / "payer")
            self.sender.fiber_config_enum = FiberConfigPath.CURRENT_DEV
            start_with_confirm(
                self.sender, fnn_log_level=self.fnn_log_level, timeout=120
            )
            self.sender.connect_peer(self.relay)
            self.assert_payment_success(self.sender)

            # 3. 游标必须前进，逐页全量集合与独立创建的 hash 对照。
            expected = {self.payment_hash, ok["payment_hash"], bad_hash}
            all_rows = self._pages()
            assert set(all_rows) == expected, all_rows
            succeeded = self._pages("Success")
            assert set(succeeded) == {self.payment_hash, ok["payment_hash"]}
            assert set(self._pages("Failed")) == {bad_hash}
            for status in ("Created", "Inflight"):
                assert self.payment_hash not in self._pages(status)
            assert all_rows[self.payment_hash]["payment_preimage"] == self.preimage
            assert succeeded[self.payment_hash]["payment_preimage"] == self.preimage
            assert self.settlement.find_spending_transaction(chain["outpoint"]) is None
        finally:
            self.node.start_miner()
        self.assert_payment_fully_settled(chain)

    def _old_partial(self):
        row = self.sender.get_client().get_payment({"payment_hash": self.payment_hash})
        if row.get("payment_preimage") != self.preimage:
            return None
        assert (
            row["status"] != "Success"
        ), "旧版付款未处于已有原像、尚未完成的升级前状态"
        return row

    def _pages(self, status=None):
        cursor, seen_cursors, rows = None, set(), {}
        for _ in range(20):
            request = {"limit": "0x1"}
            if status is not None:
                request["status"] = status
            if cursor is not None:
                request["after"] = cursor
            page = self.sender.get_client().list_payments(request)
            entries = page["payments"]
            if not entries:
                return rows
            assert len(entries) == 1
            row = entries[0]
            assert row["payment_hash"] not in rows, page
            if status:
                assert row["status"] == status
            rows[row["payment_hash"]] = row
            cursor = page["last_cursor"]
            assert cursor not in seen_cursors
            seen_cursors.add(cursor)
        raise AssertionError("分页未在固定上限内结束")
