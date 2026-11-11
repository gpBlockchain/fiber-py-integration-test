"""
PR-1708: funding 交易广播遭交易池冲突（PoolRejectedRBF，-1111，即池中另一笔
交易已花费本交易的输入）时应快速失败，而不是被当成「已在池中」反复重发、
干等到 funding 自身超时（PR 在 testnet 实测约 5.5 分钟无报错）。

构造方式：复用 PR-1213/PR-1444 的 CKB RPC 代理模式，两个 Fiber 节点都经
代理连接 CKB，代理向 send_transaction 注入真实节点会返回的 -1111 错误。
必须双节点都走代理：否则对端会直接广播同一笔 funding 交易并成功确认，
掩盖本侧的 -1111 失败路径。rc2 的选币器已感知交易池（PR-1707），从外部
无法稳定构造真实的池内输入竞争，代理注入是确定性触发该错误路径的方式。
"""

import time

from framework.basic_fiber import FiberTest
from framework.ckb_rpc_proxy import CkbRpcProxy
from framework.config import DEFAULT_MIN_DEPOSIT_CKB

CKB = 100000000


class TestFundingPoolConflictFailsFast(FiberTest):
    proxy: CkbRpcProxy

    def setup_method(self, method):
        """Override: 与 TestFundingRetry 相同，两个 Fiber 节点都经代理连接 CKB。"""
        self.did_pass = None
        self.beginNum = hex(self.node.getClient().get_tip_block_number())
        self.fibers = []
        self.new_fibers = []

        self.proxy = CkbRpcProxy(self.node.rpcUrl)
        self.proxy.start()

        from framework.test_fiber import Fiber

        self.fiber1 = Fiber.init_by_port(
            self.fiber_version,
            self.account1_private_key,
            "fiber/node1",
            "8228",
            "8227",
        )
        self.fiber2 = Fiber.init_by_port(
            self.fiber_version,
            self.account2_private_key,
            "fiber/node2",
            "8229",
            "8230",
        )
        self.fibers.append(self.fiber1)
        self.fibers.append(self.fiber2)

        from framework.helper.udt_contract import UdtContract, issue_udt_tx
        from framework.basic_fiber import XUDT_TX_HASH

        self.udtContract = UdtContract(XUDT_TX_HASH, 0)
        deploy_hash, deploy_index = self.udtContract.get_deploy_hash_and_index()

        self.node.getClient().clear_tx_pool()
        for i in range(5):
            self.Miner.miner_with_version(self.node, "0x0")
        tx_hash = issue_udt_tx(
            self.udtContract,
            self.node.rpcUrl,
            self.fiber1.account_private,
            self.fiber1.account_private,
            1000 * 100000000,
        )
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        self.node.start_miner()

        update_config = {
            "ckb_rpc_url": self.proxy.url,
            "ckb_udt_whitelist": True,
            "xudt_script_code_hash": self.Contract.get_ckb_contract_codehash(
                deploy_hash, deploy_index, True, self.node.rpcUrl
            ),
            "xudt_cell_deps_tx_hash": deploy_hash,
            "xudt_cell_deps_index": deploy_index,
        }
        update_config.update(self.start_fiber_config)

        self.fiber1.prepare(update_config=update_config)
        self.fiber1.start(fnn_log_level=self.fnn_log_level)
        self.fiber2.prepare(update_config=update_config)
        self.fiber2.start(fnn_log_level=self.fnn_log_level)

        self.fiber1.connect_peer(self.fiber2)
        time.sleep(1)
        self.logger.debug(f"\nSetting up method: {method.__name__}")

    def teardown_method(self, method):
        proxy = getattr(self, "proxy", None)
        if proxy:
            proxy.stop()
        super().teardown_method(method)

    def _wait_funding_aborted(self, fiber, peer, timeout=60):
        """等待 fiber 上向 peer 开出的通道记录变为 Closed/FUNDING_ABORTED。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            pending = fiber.get_client().list_channels(
                {"pubkey": peer.get_pubkey(), "only_pending": True}
            )
            failed = next(
                (
                    channel
                    for channel in pending["channels"]
                    if channel["state"]["state_name"] == "Closed"
                    and channel["state"]["state_flags"] == "FUNDING_ABORTED"
                ),
                None,
            )
            if failed:
                return failed
            time.sleep(1)
        return None

    def _assert_funding_aborted_within(self, seconds=60):
        """注入 -1111 并从 fiber1 开通道，断言 pending 通道在 seconds 内失败。"""
        self.proxy.block_method_with_error(
            "send_transaction",
            code=-1111,
            message="PoolRejectedRBF: another transaction in the pool spends an input",
        )
        open_begin = time.monotonic()
        self.fiber1.get_client().open_channel(
            {
                "pubkey": self.fiber2.get_pubkey(),
                "funding_amount": hex(500 * CKB + DEFAULT_MIN_DEPOSIT_CKB),
                "public": True,
            }
        )
        failed = self._wait_funding_aborted(self.fiber1, self.fiber2, seconds)
        elapsed = time.monotonic() - open_begin
        assert failed is not None, (
            "fiber1 的开通道在 60 秒内未失败（旧实现会把 -1111 当成"
            "「已在池中」干等约 5.5 分钟）；当前 pending："
            f"{self.fiber1.get_client().list_channels({'pubkey': self.fiber2.get_pubkey(), 'only_pending': True})['channels']}"
        )
        print(f"funding aborted after {elapsed:.1f}s (threshold {seconds}s)")
        return failed

    # TEST-MAP: PR1708-01
    def test_funding_pool_conflict_fails_fast(self):
        """
        1. 代理向两个节点的 send_transaction 注入 -1111（PoolRejectedRBF，
           池中另一笔交易已花费本交易输入）错误。
        2. fiber1 向 fiber2 开通道，双方对 funding 交易的广播都被池冲突拒绝。
        3. fiber1 的 pending 通道应在 60 秒内变为 Closed/FUNDING_ABORTED，
           而不是干等 funding 超时（旧实现约 5.5 分钟）。
        """
        # 1-3. 注入 -1111、开通道、断言 60 秒内失败。
        self._assert_funding_aborted_within(60)

    # TEST-MAP: PR1708-02
    def test_funding_succeeds_after_pool_conflict_failure(self):
        """
        1. 代理注入 -1111，fiber1 的第一次开通道快速失败（同 PR1708-01）。
        2. 等待 fiber2 侧也记录通道中止（选币预留已释放），代理恢复转发
           send_transaction。
        3. fiber1 重新向 fiber2 开通道，新通道正常进入 ChannelReady，
           旧的失败记录不影响新通道。
        """
        # 1. 第一次开通道因注入的 -1111 快速失败。
        self._assert_funding_aborted_within(60)

        # 2. 等待 fiber2 侧也完成中止处理（释放预留的选币），再恢复代理转发。
        #    fiber1 的 pending 记录先于 fiber2 的清理完成，不同步会撞上
        #    fiber2 选币预留尚未释放的窗口。
        peer_failed = self._wait_funding_aborted(self.fiber2, self.fiber1, 30)
        assert peer_failed is not None, "fiber2 侧未在 30 秒内记录通道中止"
        self.proxy.unblock_methods()

        # 3. 重新开通道，等待新通道（排除旧的失败记录）进入 ChannelReady。
        existing_channel_ids = {
            channel["channel_id"]
            for channel in self.fiber1.get_client().list_channels(
                {"pubkey": self.fiber2.get_pubkey(), "include_closed": True}
            )["channels"]
        }
        self.fiber1.get_client().open_channel(
            {
                "pubkey": self.fiber2.get_pubkey(),
                "funding_amount": hex(500 * CKB + DEFAULT_MIN_DEPOSIT_CKB),
                "public": True,
            }
        )
        channel_id = self.wait_for_new_channel_state(
            self.fiber1.get_client(),
            self.fiber2.get_pubkey(),
            "ChannelReady",
            existing_channel_ids,
            timeout=120,
        )
        assert channel_id not in existing_channel_ids
