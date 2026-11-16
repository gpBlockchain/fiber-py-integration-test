from framework.basic import CkbTest
from framework.basic_fiber import FiberTest, check_port, tar_file, XUDT_TX_HASH


from framework.helper.udt_contract import UdtContract, issue_udt_tx
from framework.test_fiber import Fiber
import time


class SharedFiberTest(FiberTest):
    """
    共享 Fiber 环境的测试基类
    同一个 class 的所有用例共用一套 fiber 环境，只在 teardown_class 时清理

    子类可选在类体中设置 ``shared_fiber1_extra_config`` / ``shared_fiber2_extra_config``
    （dict），会在 ``prepare`` 时与公共 ``update_config`` 合并，用于覆盖 fiber1/2 的
    模板变量（例如 ``fiber_announced_addrs``、``fiber_reuse_port_for_websocket``）。

    与 FiberTest 的区别：
    - setup_class: 不仅初始化 CKB 节点，还初始化 fiber 环境
    - setup_method: 不清理环境，只做必要的初始化
    - teardown_method: 不清理 fiber 环境
    - teardown_class: 清理所有 fiber 环境和 CKB 节点
    """

    @classmethod
    def setup_class(self):
        """
        部署一个ckb 节点
        启动 ckb 节点
        启动2个fiber
        给 fiber1 充值udt 金额
        连接2个fiber
        """
        # 初始化 fiber 环境（从 setup_method 移到这里）
        super(SharedFiberTest, self).setup_class()
        self.beginNum = hex(self.node.getClient().get_tip_block_number())
        self.fibers = []
        self.new_fibers = []
        self.fiber1 = Fiber.init_by_port(
            self.fiber_version,
            self.account1_private_key,
            "fiber/node1",
            str(self.fiber1_rpc_port),
            str(self.fiber1_p2p_port),
        )
        self.fiber2 = Fiber.init_by_port(
            self.fiber_version,
            self.account2_private_key,
            "fiber/node2",
            str(self.fiber2_rpc_port),
            str(self.fiber2_p2p_port),
        )
        self.fibers.append(self.fiber1)
        self.fibers.append(self.fiber2)
        #
        self.udtContract = UdtContract(XUDT_TX_HASH, 0)
        #
        deploy_hash, deploy_index = self.udtContract.get_deploy_hash_and_index()

        if self.debug:
            return
            # # issue
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
        # deploy fiber
        # start 2 fiber with xudt
        update_config = {
            "ckb_rpc_url": self.node.rpcUrl,
            "ckb_udt_whitelist": True,
            "xudt_script_code_hash": self.Contract.get_ckb_contract_codehash(
                deploy_hash, deploy_index, True, self.node.rpcUrl
            ),
            "xudt_cell_deps_tx_hash": deploy_hash,
            "xudt_cell_deps_index": deploy_index,
        }
        update_config.update(self.start_fiber_config)

        fiber1_config = dict(update_config)
        fiber1_extra = getattr(self, "shared_fiber1_extra_config", None)
        if fiber1_extra:
            fiber1_config.update(fiber1_extra)
        self.fiber1.prepare(update_config=fiber1_config)
        self.fiber1.start(fnn_log_level=self.fnn_log_level)

        fiber2_config = dict(update_config)
        fiber2_extra = getattr(self, "shared_fiber2_extra_config", None)
        if fiber2_extra:
            fiber2_config.update(fiber2_extra)
        self.fiber2.prepare(update_config=fiber2_config)
        self.fiber2.start(fnn_log_level=self.fnn_log_level)
        before_balance1 = self.Ckb_cli.wallet_get_capacity(
            self.account1["address"]["testnet"], api_url=self.node.getClient().url
        )
        self.logger.debug(f"before_balance1:{before_balance1}")
        self.fiber1.connect_peer(self.fiber2)
        time.sleep(1)
        self.logger.debug("SharedFiberTest setup_class completed")

    def setup_method(self, method):
        """
        每个测试方法执行前的初始化
        不清理环境，只做必要的初始化（调用 CkbTest 的 setup_method）
        """
        # 直接调用 CkbTest 的 setup_method，避免调用 FiberTest 的（会初始化 fiber 环境）
        CkbTest.setup_method(self, method)
        self.logger.debug(f"\nSetting up method:{method.__name__}")

    def teardown_method(self, method):
        """
        每个测试方法执行后的清理
        不清理 fiber 环境，只做必要的清理（调用 CkbTest 的 teardown_method）
        """
        if self.debug:
            return
        if self.first_debug:
            return
        # 直接调用 CkbTest 的 teardown_method，避免调用 FiberTest 的（会清理 fiber）
        CkbTest.teardown_method(self, method)

    @classmethod
    def teardown_class(self):
        """
        整个测试类执行后的清理
        清理所有 fiber 环境和 ckb 节点
        """
        if self.debug:
            return
        if self.first_debug:
            return
        # 清理所有 fiber
        for fiber in self.fibers:
            fiber.stop()
            fiber.clean()
        # 清理 ckb 节点
        super(SharedFiberTest, self).teardown_class()
