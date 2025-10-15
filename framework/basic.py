from abc import ABC

import unittest
import framework.helper.miner
import framework.helper.ckb_cli
import framework.helper.contract
import framework.helper.node
import framework.helper.contract_util
import framework.helper.tx
import framework.test_node
import framework.test_cluster
import framework.config
import shutil
from framework.util import get_project_root


class CkbTest(ABC, unittest.TestCase):
    Miner: framework.helper.miner = framework.helper.miner
    Ckb_cli: framework.helper.ckb_cli = framework.helper.ckb_cli
    Contract: framework.helper.contract = framework.helper.contract
    Contract_util: framework.helper.contract_util = framework.helper.contract_util
    Node: framework.helper.node = framework.helper.node
    Cluster: framework.test_cluster.Cluster = framework.test_cluster.Cluster
    CkbNode: framework.test_node.CkbNode = framework.test_node.CkbNode
    CkbNodeConfigPath: framework.test_node.CkbNodeConfigPath = (
        framework.test_node.CkbNodeConfigPath
    )
    Config = framework.config
    Tx: framework.helper.tx = framework.helper.tx

    @classmethod
    def setup_class(cls):
        print("\nSetup TestClass2")

    @classmethod
    def teardown_class(cls):
        print("\nTeardown TestClass2")

    def setup_method(self, method):
        self.did_pass = None
        print("\nSetting up method", method.__name__)

    def teardown_method(self, method):
        print("\nTearing down method", method.__name__)
        print("\nself.did_pass =", self.did_pass)
        if not self.did_pass:
            print("back log data")
            try:
                shutil.copytree(
                    f"{get_project_root()}/tmp",
                    f"{get_project_root()}/report/{method.__name__}",
                )
            except OSError as e:
                print("Error: %s - %s." % (e.filename, e.strerror))
