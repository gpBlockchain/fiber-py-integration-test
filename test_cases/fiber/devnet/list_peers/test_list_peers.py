import pytest

from framework.basic_fiber import FiberTest


class TestListPeers(FiberTest):

    # @pytest.mark.skip("https://github.com/nervosnetwork/fiber/issues/718")
    def test_01(self):
        peer = self.fiber1.get_client().list_peers()
        assert (
            peer["peers"][0]["pubkey"]
            == self.fiber2.get_client().node_info()["node_id"]
        )
        assert (
            peer["peers"][0]["address"]
            in self.fiber2.get_client().node_info()["addresses"]
        )
        assert (
            peer["peers"][0]["peer_id"]
            == self.fiber2.get_client().node_info()["addresses"][0].split("/")[-1]
        )
        # https://github.com/nervosnetwork/fiber/issues/718
        peers = self.fiber2.get_client().list_peers()
        assert len(peers["peers"][0]["address"]) != None
