from framework.basic_fiber import FiberTest
from framework.test_btc import BtcNode
from framework.test_lnd import LndNode

from framework.test_fiber import FiberConfigPath
import time

BTC_BLOCK_TIME_SECONDS = 10 * 60


class FiberCchTest(FiberTest):
    LNDs: list[LndNode] = []
    btcNode: BtcNode
    fiber_version = FiberConfigPath.CURRENT_CCH
    start_lnd_config = {}

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
            lnd.prepare(cls.start_lnd_config)
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
        lnd.prepare(self.start_lnd_config)
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

    def wait_cch_order_state(
        self, client, payment_hash, status="Success", timeout=360, interval=1
    ):
        """
        Enum with values of
            Pending - Order is created and has not send out payments yet.
            IncomingAccepted - HTLC in the incoming payment is accepted.
            OutgoingInFlight - There's an outgoing payment in flight.
            OutgoingSuccess - The outgoing payment is settled.
            Success - Both payments are settled and the order succeeds.
            Failed - Order is failed.
        Args:
            client:
            payment_hash:
            status:
            timeout:
            interval:
        Returns:

        """
        for i in range(timeout):
            result = client.get_client().get_cch_order({"payment_hash": payment_hash})
            if result["status"] == status:
                return
            if result["status"] == "Failed" or result["status"] == "Success":
                raise Exception(f"payment failed, reason:{result['status']}")
            time.sleep(interval)
        raise TimeoutError(
            f"payment:{payment_hash} status did not reach state: {result['status']}, expected:{status} , within timeout period."
        )

    def get_all_nodes_payment_tlc_expiry(self, payment_hash):
        """
        获取所有节点关于该payment——hash 的所有过期时间
        """
        expiry_times = []
        for lnd in self.LNDs:
            expiry_times.extend(self.get_lnd_payment_tlc_expiry(lnd, payment_hash))
        expiry_times.reverse()
        for fiber in self.fibers:
            expiry_times.extend(self.get_fiber_payment_tlc_expiry(fiber, payment_hash))
        return expiry_times

    def get_fiber_payment_tlc_expiry(self, fiber, payment_hash):
        pending_tlcs = self.get_pending_tlc(fiber, payment_hash)
        expiry_times = []
        for direction in ("Inbound", "Outbound"):
            for tlc in pending_tlcs.get(direction, []):
                expiry_times.append(tlc["expiry_seconds"])
        return expiry_times

    def get_lnd_payment_tlc_expiry(self, lnd, payment_hash):
        """
        1. get btc height
        2. get tlc expiration_height
        3. 换算还需要多少s 过期
        """
        btc_tip_height = int(str(self.btcNode.rpc("getblockcount")).strip(), 0)
        target_hash = str(payment_hash).lower().replace("0x", "")
        expiry_times = []
        channels = lnd.ln_cli_with_cmd("listchannels").get("channels", [])
        for channel in channels:
            for htlc in channel.get("pending_htlcs", []):
                hash_lock = str(htlc.get("hash_lock", "")).lower().replace("0x", "")
                if hash_lock != target_hash:
                    continue
                expiration_height = int(str(htlc["expiration_height"]), 0)
                expiry_times.append(
                    (expiration_height - btc_tip_height) * BTC_BLOCK_TIME_SECONDS
                )
        return expiry_times
