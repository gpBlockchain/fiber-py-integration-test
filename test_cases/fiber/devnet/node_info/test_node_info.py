import time

import pytest

from framework.basic_fiber import FiberTest


class TestNodeInfo(FiberTest):

    # @pytest.mark.skip("https://github.com/nervosnetwork/fiber/issues/631")
    def test_commit_hash(self):
        """

        Returns:

        """
        node_info = self.fiber1.get_client().node_info()

        # node_info
        assert node_info["commit_hash"] is not None

        # public key self pay
        with pytest.raises(Exception) as exc_info:
            self.fiber1.get_client().send_payment(
                {
                    "target_pubkey": node_info["node_id"],
                    "amount": hex(10 * 100000000),
                    "keysend": True,
                    "dry_run": True,
                }
            )
        expected_error_message = "allow_self_payment is not enable"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

        # peer id -> use peer id open_channel
        # self.fiber2.get_client().open_channel({
        #     "peer_id": node_info["peer_id"],
        #     "funding_amount": hex(1000 * 100000000),
        #     "public": True,
        # })
        # self.wait_for_channel_state(self.fiber2.get_client(), node_info["peer_id"], "CHANNEL_READY")
        # addresses
        nodes = self.fiber1.get_client().graph_nodes({})
        assert (
            nodes["nodes"][0]["addresses"] == node_info["addresses"]
            or nodes["nodes"][1]["addresses"] == node_info["addresses"]
        )

        # chain hash
        block_hash = self.node.getClient().get_block_hash("0x0")
        assert block_hash == node_info["chain_hash"]

        # open_channel_auto_accept_min_ckb_funding_amount
        assert node_info["open_channel_auto_accept_min_ckb_funding_amount"] == hex(
            100 * 100000000
        )

        # auto_accept_channel_ckb_funding_amount
        assert node_info["auto_accept_channel_ckb_funding_amount"] == hex(
            62 * 100000000
        )

        # tlc_expiry_delta
        assert node_info["tlc_expiry_delta"] == hex(24 * 60 * 60 * 1000)

        # tlc_min_value
        assert node_info["tlc_min_value"] == hex(0)

        # tlc_max_value
        # https://github.com/nervosnetwork/fiber/issues/631
        # assert node_info["tlc_max_value"] == hex(0)

        # tlc_fee_proportional_millionths
        assert node_info["tlc_fee_proportional_millionths"] == hex(1000)

        # peers_count
        assert node_info["peers_count"] == hex(1)

        assert node_info["features"] is not None, "features should not be None"

    def test_channel_count(self):
        """
        check channel_count
        Returns:
        """
        before_open_channel_node1_info = self.fiber1.get_client().node_info()
        before_open_channel_node2_info = self.fiber2.get_client().node_info()
        # open channel
        self.fiber2.get_client().open_channel(
            {
                "peer_id": self.fiber1.get_peer_id(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
            }
        )
        time.sleep(1)

        # pending_channel_count
        pending_node1_info = self.fiber1.get_client().node_info()
        pending_node2_info = self.fiber2.get_client().node_info()
        assert (
            int(pending_node1_info["pending_channel_count"], 16)
            == int(before_open_channel_node1_info["pending_channel_count"], 16) + 1
        )

        assert (
            int(pending_node2_info["pending_channel_count"], 16)
            == int(before_open_channel_node2_info["pending_channel_count"], 16) + 1
        )

        self.wait_for_channel_state(
            self.fiber2.get_client(), self.fiber1.get_peer_id(), "CHANNEL_READY"
        )
        after_node1_info = self.fiber1.get_client().node_info()
        after_node2_info = self.fiber2.get_client().node_info()
        assert (
            int(after_node1_info["pending_channel_count"], 16)
            == int(pending_node1_info["pending_channel_count"], 16) - 1
        )
        assert int(after_node1_info["channel_count"], 16) == int(
            pending_node1_info["channel_count"], 16
        )
        assert (
            int(after_node2_info["pending_channel_count"], 16)
            == int(pending_node1_info["pending_channel_count"], 16) - 1
        )
        assert int(after_node2_info["channel_count"], 16) == int(
            pending_node1_info["channel_count"], 16
        )
        channel = self.fiber1.get_client().list_channels({})

        # shutdown channel
        self.fiber1.get_client().shutdown_channel(
            {
                "channel_id": channel["channels"][0]["channel_id"],
                "close_script": self.get_account_script(self.fiber1.account_private),
                "fee_rate": "0x3FC",
            }
        )
        time.sleep(20)
        after_shutdown_node1_info = self.fiber1.get_client().node_info()
        after_shutdown_node2_info = self.fiber2.get_client().node_info()
        assert (
            int(after_shutdown_node1_info["channel_count"], 16)
            == int(after_node1_info["channel_count"], 16) - 1
        )
        assert (
            int(after_shutdown_node2_info["channel_count"], 16)
            == int(after_node1_info["channel_count"], 16) - 1
        )

    # def test_pending_channel_count(self):
    #     """
    #     check pending_channel_count
    #     Returns:
    #     """

    @pytest.mark.skip("")
    def test_network_sync_status(self):
        """
        check network_sync_status
        Returns:
        """

    def test_udt_cfg_infos(self):
        """
        check udt_cfg_infos
        Returns:
        """
        # open udt channel
        self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
                "funding_udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
            }
        )
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY"
        )
