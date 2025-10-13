import time

import pytest

from framework.basic_fiber import FiberTest


class TestCkbRemoveTx(FiberTest):

    debug = True
    # @pytest.mark.skip("https://github.com/nervosnetwork/fiber/issues/515")
    def test_remove_open_tx_stuck_node1(self):
        """
        导致节点1 node_info 卡住

        Returns:

        """
        self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(2000 * 100000000),
                "public": True,
            }
        )
        tx_hash = self.wait_and_check_tx_pool_fee(1000, False, 120)
        tx = self.node.getClient().get_transaction(tx_hash)
        self.node.client.clear_tx_pool()
        time.sleep(5)
        del tx['transaction']['hash']
        tx_hash = self.node.getClient().send_transaction(tx['transaction'])
        self.Miner.miner_until_tx_committed(self.node,tx_hash)
        time.sleep(10)

        # self.faucet(self.fiber1.account_private, 10000)
        # time.sleep(3)
        # self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1)

    def test_0001(self):
        self.fiber1.get_client().list_channels({"include_closed": True})
        self.fiber2.get_client().list_channels({"include_closed": True})
        # self.node.getClient().get_transaction("0x53b95ba82246842694ebcfb557916e6f671bfc938099652a6cd804f5cb82108c")
        # self.node.getClient().get_transaction("0xe8e7ba92565ae03fc4e83d26ab08a5a8eefaa09d951535631ff98a381d1ca33d")

    def test_bbabb(self):
        self.fiber1.get_client().list_peers()
        self.fiber2.get_client().list_peers()


    def test_0002(self):
        for i in range(100):
            self.fiber1.get_client().open_channel(
                {
                    "peer_id": self.fiber2.get_peer_id(),
                    "funding_amount": hex(1000 * 100000000+i),
                    "public": True,
                }
            )
            time.sleep(0.1)
        # self.fiber1.get_client().open_channel(
        #     {
        #         "peer_id": self.fiber2.get_peer_id(),
        #         "funding_amount": hex(2000 * 100000000),
        #         "public": True,
        #     }
        # )

    def test_balala(self):
        self.fiber1.get_client().list_channels({})
        self.fiber2.get_client().list_channels({})

    # @pytest.mark.skip("https://github.com/nervosnetwork/fiber/issues/515")
    def test_remove_open_tx_stuck_node2(self):
        """
        导致节点2 node_info 卡住

        Returns:

        """
        self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
            }
        )
        self.wait_tx_pool(pending_size=1, try_size=100)
        self.node.client.clear_tx_pool()
        # self.node.restart()
        # self.node.start_miner()
        time.sleep(3)
        self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
            }
        )
        time.sleep(5)
        self.fiber2.get_client().node_info()
        fiber3 = self.start_new_fiber(self.generate_account(10000))
        self.open_channel(fiber3, self.fiber2, 1000 * 100000000, 1)
