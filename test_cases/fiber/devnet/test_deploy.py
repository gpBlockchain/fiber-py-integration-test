import pytest

from framework.basic import CkbTest
from test_cases.soft_fork.test_sync_again_with_other_node_when_sync_failed_tx import (
    tar_file,
)


class TestDeploy(CkbTest):
    @pytest.mark.skip("deploy")
    def test_deploy(self):
        self.node = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.CURRENT_TEST, f"cluster/hardfork/node0", 8114, 8225
        )
        self.node.prepare()
        self.node.prepare()
        # tar_file(DATA_ERROR_TAT, node1.ckb_dir)

        self.node.start()

        self.Miner.make_tip_height_number(self.node, 1100)

        # deploy contract  xudt
        xudt_tx_hash = self.Contract.deploy_ckb_contract(
            self.Config.MINER_PRIVATE_1,
            "/Users/guopenglin/PycharmProjects/ckb-py-integration-test/source/contract/fiber/xudt",
            2000,
            True,
            self.node.rpcUrl,
        )
        xudt_code_hash = self.Contract.get_ckb_contract_codehash(
            xudt_tx_hash, 0, True, self.node.rpcUrl
        )
        self.Miner.miner_until_tx_committed(self.node, xudt_tx_hash)

        auth_tx_hash = self.Contract.deploy_ckb_contract(
            self.Config.MINER_PRIVATE_1,
            "/Users/guopenglin/PycharmProjects/ckb-py-integration-test/source/contract/fiber/auth",
            2000,
            True,
            self.node.rpcUrl,
        )
        auth_code_hash = self.Contract.get_ckb_contract_codehash(
            auth_tx_hash, 0, True, self.node.rpcUrl
        )
        self.Miner.miner_until_tx_committed(self.node, auth_tx_hash)

        commitment_lock_tx_hash = self.Contract.deploy_ckb_contract(
            self.Config.MINER_PRIVATE_1,
            "/Users/guopenglin/PycharmProjects/ckb-py-integration-test/source/contract/fiber/commitment-lock",
            2000,
            True,
            self.node.rpcUrl,
        )
        commitment_lock__code_hash = self.Contract.get_ckb_contract_codehash(
            commitment_lock_tx_hash, 0, True, self.node.rpcUrl
        )
        self.Miner.miner_until_tx_committed(self.node, commitment_lock_tx_hash)

        # funding-lock
        funding_lock_tx_hash = self.Contract.deploy_ckb_contract(
            self.Config.MINER_PRIVATE_1,
            "/Users/guopenglin/PycharmProjects/ckb-py-integration-test/source/contract/fiber/funding-lock",
            2000,
            True,
            self.node.rpcUrl,
        )
        funding_lock_code_hash = self.Contract.get_ckb_contract_codehash(
            funding_lock_tx_hash, 0, True, self.node.rpcUrl
        )
        self.Miner.miner_until_tx_committed(self.node, funding_lock_tx_hash)

        print("xudt_code_hash:", xudt_code_hash)
        print("xudt_tx_hash:", xudt_tx_hash)
        print("auth_code_hash:", auth_code_hash)
        print("auth_tx_hash:", auth_tx_hash)
        print("commitment_lock_code_hash:", commitment_lock__code_hash)
        print("commitment_lock_tx_hash:", commitment_lock_tx_hash)
        print("funding_lock_code_hash:", funding_lock_code_hash)
        print("funding_lock_tx_hash:", funding_lock_tx_hash)

    # xudt_code_hash: 0x102583443ba6cfe5a3ac268bbb4475fb63eb497dce077f126ad3b148d4f4f8f8
    # xudt_tx_hash: 0x03c4475655a46dc4984c49fce03316f80bf666236bd95118112731082758d686
    # auth_code_hash: 0x97959f53d36b73e86acb5e8b925d9f58ef255fce05b78625a308349f2df01c8a
    # auth_tx_hash: 0xecb1c1e3df6cd1e1ca16ca9bd392a3c030ece59cb5123bf156c51034e311a3ec
    # commitment_lock_code_hash: 0x2d7d93e3347ddf9f10f6690af75f1e24debaa6c4363f3b2c068f61c757253d38
    # commitment_lock_tx_hash: 0x79c3e55d7010755918f3d9b464425692eee8aa2e9ce89e4355cac0caa51d95bf
    # funding_lock_code_hash: 0xd7302abe337c459b84c9da6d739d7736d6e8dbfd2326a509981c35943cfe0f56
    # funding_lock_tx_hash: 0xa4b5c0c402797226ba4dadce21117811549de8b62f8acb3065dc49c23965f2a8

    def test_00000(self):
        self.node = self.CkbNode.init_dev_by_port(
            self.CkbNodeConfigPath.CURRENT_TEST, f"cluster/hardfork/node0", 8114, 8225
        )
        # self.node.prepare()
        # self.node.prepare()
        # tar_file("/Users/guopenglin/PycharmProjects/ckb-py-integration-test/source/fiber/data.fiber.tar.gz", self.node.ckb_dir)
        # self.node.start()
        # self.node.getClient().get_tip_block_number()
        # xudt_arg = self.node.getClient().get_live_cell(hex(0),"0x79c3e55d7010755918f3d9b464425692eee8aa2e9ce89e4355cac0caa51d95bf")
        self.node.getClient().get_tip_block_number()
