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
                    "target_pubkey": node_info["pubkey"],
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
        #     "pubkey": node_info["pubkey"],
        #     "funding_amount": hex(1000 * 100000000),
        #     "public": True,
        # })
        # self.wait_for_channel_state(self.fiber2.get_client(), node_info["pubkey"], "ChannelReady")
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
            98 * 100000000
        )

        # tlc_expiry_delta
        assert node_info["tlc_expiry_delta"] == hex(14400000)

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
        """Count actors through funding and cooperative-close cleanup."""
        client1 = self.fiber1.get_client()
        client2 = self.fiber2.get_client()
        before_node1_info = client1.node_info()
        before_node2_info = client2.node_info()

        # Hold funding unconfirmed so neither peer can skip the pending stage.
        self.node.stop_miner()
        try:
            client2.open_channel(
                {
                    "pubkey": self.fiber1.get_pubkey(),
                    "funding_amount": hex(1000 * 100000000),
                    "public": True,
                }
            )
            for client, before in (
                (client1, before_node1_info),
                (client2, before_node2_info),
            ):
                self.wait_for_node_info_counts(
                    client,
                    channel_count=int(before["channel_count"], 16) + 1,
                    pending_channel_count=int(before["pending_channel_count"], 16) + 1,
                )
        finally:
            self.node.start_miner()

        channel_id = self.wait_for_channel_state(
            client1, self.fiber2.get_pubkey(), "ChannelReady"
        )
        self.wait_for_channel_state(
            client2, self.fiber1.get_pubkey(), "ChannelReady", channel_id=channel_id
        )
        for client, before in (
            (client1, before_node1_info),
            (client2, before_node2_info),
        ):
            self.wait_for_node_info_counts(
                client,
                channel_count=int(before["channel_count"], 16) + 1,
                pending_channel_count=int(before["pending_channel_count"], 16),
            )

        client1.shutdown_channel(
            {
                "channel_id": channel_id,
                "close_script": self.get_account_script(self.fiber1.account_private),
                "fee_rate": "0x3FC",
            }
        )
        for client, pubkey, before in (
            (client1, self.fiber2.get_pubkey(), before_node1_info),
            (client2, self.fiber1.get_pubkey(), before_node2_info),
        ):
            self.wait_for_channel_state(
                client, pubkey, "Closed", include_closed=True, channel_id=channel_id
            )
            # Closed is persisted before NetworkActor removes the channel actor.
            self.wait_for_node_info_counts(
                client,
                channel_count=int(before["channel_count"], 16),
                pending_channel_count=int(before["pending_channel_count"], 16),
            )

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
                "pubkey": self.fiber2.get_pubkey(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
                "funding_udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
            }
        )
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_pubkey(), "ChannelReady"
        )
