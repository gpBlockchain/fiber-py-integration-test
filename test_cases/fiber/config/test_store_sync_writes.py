"""
PR-1703: store_sync_writes 配置开关（每次写库等待落盘）。

黑盒可验证的部分：开关被节点接受并在启动日志中声明
（`Store sync writes: true/false`），且开启后开通道、付款、重启恢复等
既有功能无回归。断电耐久本身（kill -9 不丢 OS 页缓存）单机上无法从外部
区分开/关，不在本文件覆盖。
"""

import time

from framework.basic_fiber import FiberTest

CKB = 100000000


class TestStoreSyncWrites(FiberTest):
    start_fiber_config = {"fiber_store_sync_writes": True}

    def _read_fiber_log(self, fiber) -> str:
        try:
            with open(f"{fiber.tmp_path}/node.log", "r") as f:
                return f.read()
        except FileNotFoundError:
            return ""

    # TEST-MAP: PR1703-01
    def test_sync_writes_on_functional_regression(self):
        """
        1. 以 yml 配置 fiber_store_sync_writes: true 启动 fiber1/fiber2，
           断言两节点启动日志声明 Store sync writes: true。
        2. 开通道并完成一笔付款。
        3. 重启 fiber1，通道保持 ChannelReady 且双方余额与重启前一致。
        """
        # 1. 断言两个节点的启动日志都声明开关已生效。
        for fiber in (self.fiber1, self.fiber2):
            log = self._read_fiber_log(fiber)
            assert (
                "Store sync writes: true" in log
            ), f"{fiber.tmp_path} 启动日志未声明 Store sync writes: true"

        # 2. 开通道并完成一笔付款。
        channel_id = self.open_channel(self.fiber1, self.fiber2, 500 * CKB, 100 * CKB)
        self.send_payment(self.fiber1, self.fiber2, 1 * CKB)

        # 3. 记录双方余额，重启 fiber1 后核对通道与余额。
        # Payment Success 可能早于通道账本更新，先等待双方 TLC 结清。
        for fiber in (self.fiber1, self.fiber2):
            self.wait_fibers_pending_tlc_eq0(fiber)

        def balances():
            channels1 = self.fiber1.get_client().list_channels({})["channels"]
            channels2 = self.fiber2.get_client().list_channels({})["channels"]
            return (
                next(c for c in channels1 if c["channel_id"] == channel_id)[
                    "local_balance"
                ],
                next(c for c in channels2 if c["channel_id"] == channel_id)[
                    "local_balance"
                ],
            )

        balance1_before, balance2_before = balances()
        self.fiber1.stop()
        self.fiber1.start(fnn_log_level=self.fnn_log_level)
        time.sleep(3)
        self.fiber1.connect_peer(self.fiber2)
        self.wait_for_channel_state(
            self.fiber1.get_client(),
            self.fiber2.get_pubkey(),
            "ChannelReady",
            channel_id=channel_id,
        )
        balance1_after, balance2_after = balances()
        assert (
            balance1_after == balance1_before
        ), f"fiber1 重启后余额变化：{balance1_before} -> {balance1_after}"
        assert (
            balance2_after == balance2_before
        ), f"fiber2 对端余额变化：{balance2_before} -> {balance2_after}"


class TestStoreSyncWritesDefault(FiberTest):
    """默认配置与环境变量入口（PR-1703）。"""

    def _read_fiber_log(self, fiber) -> str:
        try:
            with open(f"{fiber.tmp_path}/node.log", "r") as f:
                return f.read()
        except FileNotFoundError:
            return ""

    # TEST-MAP: PR1703-02
    def test_sync_writes_off_by_default(self):
        """
        1. 不配置 store_sync_writes（默认）启动 fiber1/fiber2，断言启动日志
           声明 Store sync writes: false。
        2. 开通道并完成一笔付款，验证默认配置下既有功能正常。
        """
        # 1. 断言两个节点的启动日志都声明开关默认关闭。
        for fiber in (self.fiber1, self.fiber2):
            log = self._read_fiber_log(fiber)
            assert (
                "Store sync writes: false" in log
            ), f"{fiber.tmp_path} 启动日志未声明 Store sync writes: false"

        # 2. 开通道并完成一笔付款。
        self.open_channel(self.fiber1, self.fiber2, 500 * CKB, 100 * CKB)
        self.send_payment(self.fiber1, self.fiber2, 1 * CKB)

    # TEST-MAP: PR1703-03
    def test_sync_writes_on_via_env(self):
        """
        1. 以环境变量 FIBER_STORE_SYNC_WRITES=true 启动新节点 fiber3，断言其
           启动日志声明 Store sync writes: true。
        2. fiber3 与 fiber2 开通道并完成一笔付款，验证该入口下既有功能正常。
        """
        # 1. 环境变量入口启动 fiber3 并断言日志声明开启。
        fiber3 = self.start_new_fiber(
            self.generate_account(10000),
            env={"FIBER_STORE_SYNC_WRITES": "true"},
        )
        log = self._read_fiber_log(fiber3)
        assert (
            "Store sync writes: true" in log
        ), f"{fiber3.tmp_path} 启动日志未声明 Store sync writes: true"

        # 2. fiber3 开通道并完成一笔付款。
        self.open_channel(fiber3, self.fiber2, 500 * CKB, 100 * CKB)
        self.send_payment(fiber3, self.fiber2, 1 * CKB)
