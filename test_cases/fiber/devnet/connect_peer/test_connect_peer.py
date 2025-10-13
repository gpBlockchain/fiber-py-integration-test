import time

import pytest

from framework.basic_fiber import FiberTest


class TestConnectPeer(FiberTest):

    def test_connect_peer(self):
        """
        Test connecting to a peer.

        Steps:
        1. Connect to an existing node.
        2. Attempt to connect to a non-existing node.
        3. Wait for 2 seconds to ensure the connection is established.
        4. Retrieve node information.
        5. Assert that the peer count is 1.
        """
        # Step 1: Connect to an existing node
        self.fiber1.connect_peer(self.fiber2)

        # Step 2: Attempt to connect to a non-existing node
        self.fiber1.get_client().connect_peer(
            {
                "address": "/ip4/127.0.0.1/tcp/8231/p2p/QmNoDjLNbJujKpBorKHWPHPKoLrzND1fYtmmEVxkq35Hgp"
            }
        )

        # Step 3: Wait for 2 seconds to ensure the connection is established
        time.sleep(2)

        # Step 4: Retrieve node information
        node_info = self.fiber1.get_client().node_info()

        # Step 5: Assert that the peer count is 1
        assert node_info["peers_count"] == "0x1"

    @pytest.mark.skip("restart ,list_peer will empty ,not stable")
    def test_restart(self):
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1000 * 100000000)
        for i in range(5):
            self.fiber1.stop()
            self.fiber1.start()
            peers = self.fiber1.get_client().list_peers()
        time.sleep(2)
        self.fiber1.get_client().list_channels({})
        peers = self.fiber1.get_client().list_peers()
        assert len(peers["peers"]) == 1
