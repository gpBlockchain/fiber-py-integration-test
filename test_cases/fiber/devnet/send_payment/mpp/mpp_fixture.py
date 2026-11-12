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

    def wait_payment_routes(self, public_channels, *, trampoline=None):
        """等待已显式建立的拓扑传播；不建通道、不提交付款。"""
        if trampoline is not None:
            wait_for_result(
                "trampoline 能力传播",
                lambda: any(
                    node["pubkey"] == trampoline.get_pubkey()
                    and "TRAMPOLINE_ROUTING_REQUIRED" in node["features"]
                    for node in self.sender.get_client().graph_nodes({})["nodes"]
                ),
            )
        for fiber in self.fibers:
            fiber.get_client().try_count, fiber.get_client().timeout = 1, 10
        wait_for_result(
            "所需公开通道已同步",
            lambda: len(self.sender.get_client().graph_channels({})["channels"])
            >= public_channels,
        )

    def wait_mpp_committed(self, amount, *, upstream_channel=None):
        """仅观察显式发起的付款：发票 Received、逐跳两端 Committed 与金额。"""
        assert all(
            int(edge["before"][0]["local_balance"], 16) < amount
            for edge in self.split_channels
        )
        assert (
            sum(
                int(edge["before"][0]["local_balance"], 16)
                for edge in self.split_channels
            )
            >= amount
        )
        self.wait_invoice_state(
            self.receiver, self.payment_hash, "Received", timeout=120
        )
        for edge in self.channels:
            self.settlement.record_committed_tlcs(edge, self.payment_hash)
        assert sum(edge["sent"] for edge in self.split_channels) == amount
        if upstream_channel is not None:
            assert 0 < upstream_channel["sent"] - amount <= amount // 100
        self.assert_no_preimage(self.sender)
        if upstream_channel is not None:
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

    def track_closed_channel(self, channel):
        """只观察用例已显式强关的原通道，从 funding 跟踪链上消费。"""
        assert len(channel["tlcs"]) == 1
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

    def assert_healthy_payment_settled(
        self, sender, receiver, edge, before, payment_hash, amount
    ):
        """只核对用例已发起的新付款；保留原付款的锁定，不隐藏 keysend。"""
        self.wait_payment_state(sender, payment_hash, "Success", timeout=120)
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
                == int(row["local_balance"], 16) + sign * amount
            )
