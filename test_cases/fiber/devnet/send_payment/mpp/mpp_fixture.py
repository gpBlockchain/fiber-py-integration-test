"""MPP 公开接口与链上资金检查共用 fixture。"""

import time

from framework.basic_fiber import COMMIT_LOCK_CODE_HASH, FiberTest
from framework.config import DEFAULT_MIN_DEPOSIT_CKB, DEFAULT_MIN_LEDGER_DEPOSIT_CKB
from framework.helper.channel_settlement import ChannelSettlement
from framework.helper.fiber_ops import (
    get_channel,
    calculate_payment_hash,
    wait_for_result,
)

CKB = 100_000_000
RESULT_WAIT = 420
AMOUNT = 1500 * CKB


class MppFixture(FiberTest):
    # 使用项目默认端口；与其他使用公共 CLI 临时文件的节点测试串行执行。
    tmp_path_name = "tmp-mpp-regression"
    fnn_log_level = "info"
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 3}

    @classmethod
    def setup_class(cls):
        super().setup_class()
        cls._mpp_method_started = False

    def setup_method(self, method):
        cls = type(self)
        if cls._mpp_method_started:
            cls.teardown_class()
            cls.setup_class()
        cls._mpp_method_started = True
        super().setup_method(method)
        self.settlement = ChannelSettlement(
            self.node.getClient(), COMMIT_LOCK_CODE_HASH
        )

    def start_new_fiber(
        self, account_private_key, config=None, fiber_version=None, env=None
    ):
        return super().start_new_fiber(
            account_private_key,
            config=config,
            fiber_version=fiber_version or self.fiber_version,
            env=env,
        )

    def open_payment_channel(self, left, right, capacity, private=False, udt=None):
        # 私有接收端不调用仅适用于 public 通道的 update_channel。
        # 实际正向发送者费率仍为 0；私有反向费率不参与本组付款。
        cid = self.open_channel(
            left,
            right,
            capacity,
            0,
            0,
            1000 if private else 0,
            udt=udt,
            other_config={
                "public": not private,
                "commitment_delay_epoch": "0x10000000001",
            },
        )
        before = [get_channel(f, cid) for f in (left, right)]
        raw = bytes.fromhex(before[0]["channel_outpoint"][2:])
        assert len(raw) == 36
        return dict(
            left=left,
            right=right,
            channel_id=cid,
            before=before,
            commitment_delay_epoch=0x10000000001,
            funding={
                "tx_hash": "0x" + raw[:32].hex(),
                "index": hex(int.from_bytes(raw[32:], "little")),
            },
        )

    def prepare_payment(self, topology="direct", hash_algorithm="ckb_hash"):
        self.sender, self.relay = self.fiber1, self.fiber2
        self.receiver = self.start_new_fiber(
            self.generate_account(20000), fiber_version=self.fiber_version
        )
        self.topology, self.hash_algorithm = topology, hash_algorithm
        if topology == "direct":
            self.split_channels = [
                self.open_payment_channel(self.sender, self.relay, 1000 * CKB)
                for _ in range(2)
            ]
            self.forward_channel = self.open_payment_channel(
                self.relay, self.receiver, 3000 * CKB
            )
            self.channels = self.split_channels + [self.forward_channel]
        else:
            self.upstream_channel = self.open_payment_channel(
                self.sender, self.relay, 3000 * CKB
            )
            if topology == "trampoline_with_bridge":
                self.bridge = self.start_new_fiber(
                    self.generate_account(20000), fiber_version=self.fiber_version
                )
                self.split_channels = [
                    self.open_payment_channel(self.relay, self.receiver, 1000 * CKB),
                    self.open_payment_channel(self.relay, self.bridge, 1000 * CKB),
                ]
                self.forward_channel = self.open_payment_channel(
                    self.bridge, self.receiver, 2000 * CKB
                )
                self.channels = (
                    [self.upstream_channel]
                    + self.split_channels
                    + [self.forward_channel]
                )
            else:
                self.split_channels = [
                    self.open_payment_channel(
                        self.relay, self.receiver, 1000 * CKB, private=True
                    )
                    for _ in range(2)
                ]
                self.channels = [self.upstream_channel] + self.split_channels
            wait_for_result(
                "trampoline 能力传播",
                lambda: any(
                    n["pubkey"] == self.relay.get_pubkey()
                    and "TRAMPOLINE_ROUTING_REQUIRED" in n["features"]
                    for n in self.sender.get_client().graph_nodes({})["nodes"]
                ),
            )
        for f in self.fibers:
            f.get_client().try_count, f.get_client().timeout = 1, 10
        expected_graph = len(self.channels) if topology != "trampoline" else 1
        wait_for_result(
            "所需通道已同步",
            lambda: len(self.sender.get_client().graph_channels({})["channels"])
            >= expected_graph,
        )
        assert all(
            int(e["before"][0]["local_balance"], 16) < AMOUNT
            for e in self.split_channels
        )
        assert (
            sum(int(e["before"][0]["local_balance"], 16) for e in self.split_channels)
            >= AMOUNT
        )
        self.preimage = self.generate_random_preimage()
        self.payment_hash = calculate_payment_hash(self.preimage, hash_algorithm)
        invoice = self.receiver.get_client().new_invoice(
            {
                "amount": hex(AMOUNT),
                "currency": "Fibd",
                "payment_hash": self.payment_hash,
                "hash_algorithm": hash_algorithm,
                "allow_mpp": True,
                "allow_trampoline_routing": topology != "direct",
                "expiry": hex(86400),
                "final_expiry_delta": hex(43_200_000),
            }
        )
        request = dict(
            invoice=invoice["invoice_address"],
            max_parts=hex(2),
            timeout=hex(1800),
            max_fee_amount=hex(AMOUNT // 100 if topology != "direct" else 0),
            tlc_expiry_limit=hex(172_800_000),
        )
        if topology != "direct":
            request["trampoline_hops"] = [self.relay.get_pubkey()]
        response = self.sender.get_client().send_payment(request)
        assert response["payment_hash"] == self.payment_hash
        self.wait_invoice_state(
            self.receiver, self.payment_hash, "Received", timeout=120
        )
        for edge in self.channels:
            self.settlement.record_committed_tlcs(edge, self.payment_hash)
        assert sum(e["sent"] for e in self.split_channels) == AMOUNT
        if topology != "direct":
            assert 0 < self.upstream_channel["sent"] - AMOUNT <= AMOUNT // 100
        self.assert_no_preimage(self.sender)
        if topology != "direct":
            self.assert_no_preimage(self.relay)
        self.assert_expiry_margin(1800)

    def assert_expiry_margin(self, seconds):
        # 正式版 epoch 保护窗口按公开 commitment delay 和 4 小时/epoch 换算。
        for edge in self.channels:
            delay = edge["commitment_delay_epoch"]  # 本 fixture 显式提交的公开开通参数
            number, index, length = (
                delay & 0xFFFFFF,
                (delay >> 24) & 0xFFFF,
                (delay >> 40) & 0xFFFF,
            )
            assert length > 0
            protection = (number + index / length) * 14400 * 2 / 3
            assert min(int(t["expiry"], 16) / 1000 for t in edge["tlcs"]) > (
                time.time() + seconds + protection + 120
            ), "TLC 到期保护裕量不足"

    def assert_no_preimage(self, fiber):
        row = fiber.get_client().get_payment({"payment_hash": self.payment_hash})
        assert row["status"] != "Success" and row.get("payment_preimage") is None, row
        return row

    def assert_payment_success(self, fiber, isolated=False, timeout=RESULT_WAIT):
        def read():
            if isolated:
                assert (
                    fiber.get_client().list_peers()["peers"] == []
                ), "原像来源被 Fiber 重连污染"
            row = fiber.get_client().get_payment({"payment_hash": self.payment_hash})
            if row["status"] != "Success":
                return None
            assert row.get("payment_preimage") == self.preimage, row
            assert not row.get("failed_error"), row
            assert (
                calculate_payment_hash(row["payment_preimage"], self.hash_algorithm)
                == self.payment_hash
            )
            return row

        return wait_for_result("正确原像及成功状态", read, timeout=timeout)

    def settle_payment(self, wait_paid=True):
        self.receiver.get_client().settle_invoice(
            {"payment_hash": self.payment_hash, "payment_preimage": self.preimage}
        )
        if wait_paid:
            self.wait_invoice_state(
                self.receiver, self.payment_hash, "Paid", timeout=120
            )

    def force_close_channel(self, channel, broadcaster):
        """强关已记录的原通道，从其 funding outpoint 跟踪后续链上消费。"""
        assert len(channel["tlcs"]) == 1
        broadcaster.get_client().shutdown_channel(
            {"channel_id": channel["channel_id"], "force": True}
        )
        locks = [
            self.get_account_script(f.account_private)
            for f in (channel["left"], channel["right"])
        ]
        settlement = self.settlement.track_commitment(channel["funding"], locks)
        settlement["channel"] = channel
        return settlement

    def wait_for_chain_settlement(
        self, settlement, *, claimed_only=False, timeout=1800
    ):
        self.settlement.wait(
            settlement,
            self.payment_hash,
            settlement["channel"]["sent"],
            self.preimage,
            until_claim=claimed_only,
            timeout=timeout,
        )

    def assert_payment_fully_settled(self, chain):
        self.wait_for_chain_settlement(chain)
        assert chain["claimed"], "未见属于原付款的原像领取"
        edge = chain["channel"]
        # 保证金按原通道的公开 commitment 编码确定，不按升级后的 FNN 版本。
        deposit = (
            DEFAULT_MIN_LEDGER_DEPOSIT_CKB
            if chain["version"] == "legacy"
            else DEFAULT_MIN_DEPOSIT_CKB
        )
        expected = [
            int(row["local_balance"], 16) + deposit + sign * edge["sent"]
            for row, sign in zip(edge["before"], (-1, 1))
        ]
        # 从各所有者的实际净到账分别归属费用；合计必须等于独立 inputs-outputs 手续费。
        costs = [expected[i] - chain["net"][i] for i in range(2)]
        assert all(0 <= cost <= chain["fee"] for cost in costs), (expected, chain)
        assert sum(costs) == chain["fee"] < CKB // 100
        for other in self.channels:
            if other is not edge:
                wait_for_result(
                    "开放原通道只交付一次并解锁",
                    lambda e=other: self.settlement.payment_is_settled(
                        e, self.payment_hash
                    ),
                    RESULT_WAIT,
                )
        self.assert_payment_success(self.sender)
        self.wait_invoice_state(self.receiver, self.payment_hash, "Paid", timeout=120)

    def assert_healthy_payment(self, sender, receiver, edge):
        before = [get_channel(f, edge["channel_id"]) for f in (sender, receiver)]
        result = sender.get_client().send_payment(
            {
                "target_pubkey": receiver.get_pubkey(),
                "amount": hex(CKB),
                "keysend": True,
                "max_fee_amount": "0x0",
            }
        )
        self.wait_payment_state(sender, result["payment_hash"], "Success", timeout=120)
        wait_for_result(
            "健康付款解锁且不释放原在途锁定",
            lambda: all(
                get_channel(f, edge["channel_id"])[k] == row[k]
                for f, row in zip((sender, receiver), before)
                for k in ("offered_tlc_balance", "received_tlc_balance")
            ),
        )
        for f, row, sign in zip((sender, receiver), before, (-1, 1)):
            assert (
                int(get_channel(f, edge["channel_id"])["local_balance"], 16)
                == int(row["local_balance"], 16) + sign * CKB
            )
