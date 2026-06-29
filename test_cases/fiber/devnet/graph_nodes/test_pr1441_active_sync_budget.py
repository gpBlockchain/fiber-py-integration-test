# PR-1441: a disconnected finished active-sync peer must not keep consuming
# the active gossip sync budget.

import time

from framework.basic_fiber import FiberTest


class TestPR1441ActiveSyncBudget(FiberTest):
    start_fiber_config = {
        "fiber_gossip_network_num_targeted_active_syncing_peers": 1,
        "fiber_gossip_network_num_targeted_outbound_passive_syncing_peers": 0,
    }

    def test_disconnected_finished_peer_releases_active_sync_budget(self):
        fiber2_pubkey = self.fiber2.get_pubkey()
        time.sleep(2)

        try:
            self.fiber1.get_client().disconnect_peer({"pubkey": fiber2_pubkey})
        except Exception as err:
            if "is not connected" not in err.args[0]:
                raise

        for _ in range(10):
            peers = self.fiber1.get_client().list_peers().get("peers", [])
            if all(peer["pubkey"] != fiber2_pubkey for peer in peers):
                break
            time.sleep(1)
        else:
            assert False, f"peer {fiber2_pubkey} did not disconnect"

        fiber3 = self.start_new_fiber(self.generate_account(1000))
        fiber3_pubkey = fiber3.get_pubkey()
        self.fiber1.connect_peer(fiber3)

        for _ in range(30):
            nodes = self.fiber1.get_client().graph_nodes({}).get("nodes", [])
            if any(node["pubkey"] == fiber3_pubkey for node in nodes):
                return
            time.sleep(1)
        assert False, f"graph_nodes did not contain {fiber3_pubkey}"
