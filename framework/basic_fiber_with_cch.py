from framework.basic_fiber import FiberTest
from framework.test_btc import BtcNode
from framework.test_lnd import LndNode


class FiberCchTest(FiberTest):
    LNDs: list[LndNode] = []
    btcNode: BtcNode

    @classmethod
    def setup_class(cls):
        super().setup_class()
        cls.btcNode = BtcNode()
        cls.LNDs = [
            LndNode("tmp/lnd/node0", 9735, 10009, 8180),
            LndNode("tmp/lnd/node1", 9736, 11010, 8181),
        ]
        if cls.debug == True:
            return
            # 启动btc
        cls.btcNode.prepare()
        cls.btcNode.start()
        # 启动lnd
        for lnd in cls.LNDs:
            lnd.prepare()
            lnd.start()

        # 建立2个lnd的连接
        ingrid_p2tr_address = cls.LNDs[0].ln_cli_with_cmd("newaddress p2tr")["address"]
        cls.btcNode.sendtoaddress(ingrid_p2tr_address, 5, 25)
        cls.btcNode.miner(1)
        cls.LNDs[0].open_channel(cls.LNDs[1], 1000000, 1, 0)
        cls.btcNode.miner(6)
        ingrid_p2tr_address = cls.LNDs[1].ln_cli_with_cmd("newaddress p2tr")["address"]
        cls.btcNode.sendtoaddress(ingrid_p2tr_address, 5, 25)
        cls.btcNode.miner(1)
        cls.LNDs[1].open_channel(cls.LNDs[0], 1000000, 1, 0)
        cls.btcNode.miner(6)

    def faucetBtc(self, lnd, amount):
        address = lnd.ln_cli_with_cmd("newaddress p2tr")["address"]
        self.btcNode.sendtoaddress(address, amount, 25)
        self.btcNode.miner(1)

    def setup_method(cls, method):
        super().setup_method(method)
        if cls.debug == True:
            return
        cls.fiber1.stop()
        # lnd_cert_path: {{ cch_lnd_cert_path | default("../../lnd/node1/tls.cert") }}
        # lnd_rpc_url: {{ cch_lnd_rpc_url | default("https://localhost:10009") }}
        cls.fiber1.prepare(
            update_config={
                "cch": True,
                "cch_lnd_cert_path": f"{cls.LNDs[0].tmp_path}/tls.cert",
                "cch_lnd_rpc_url": f"https://localhost:{cls.LNDs[0].rpc_port}",
            }
        )
        cls.fiber1.start()

    def start_new_lnd(self):
        if self.debug:
            self.logger.debug("=================start  mock lnd ==================")
            return self.start_new_mock_lnd()

        i = len(self.LNDs)
        # start lnd
        lnd = LndNode(f"tmp/lnd/node{i}", 9735 + i, 10009 + i, 8180 + i)
        self.LNDs.append(lnd)
        lnd.prepare()
        lnd.start()
        return lnd

    def start_new_mock_lnd(self):
        i = len(self.LNDs)
        lnd = LndNode(f"tmp/lnd/node{i}", 9735 + i, 10009 + i, 8180 + i)
        self.LNDs.append(lnd)
        return lnd

    def teardown_method(self, method):
        if self.debug:
            return
        if self.first_debug:
            return
        super().teardown_method(method)
        for fiber in self.fibers:
            fiber.stop()
            fiber.clean()

    @classmethod
    def teardown_class(cls):
        if cls.debug:
            return
        if cls.first_debug:
            return
        cls.node.stop()
        cls.node.clean()
        for lnd in cls.LNDs:
            lnd.stop()
            lnd.clean()
        cls.btcNode.stop()
        cls.btcNode.clean()
