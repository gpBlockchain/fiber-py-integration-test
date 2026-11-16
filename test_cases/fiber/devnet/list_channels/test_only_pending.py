import time

from framework.basic_fiber import FiberTest


class TestListChannelsOnlyPending(FiberTest):
    start_fiber_config = {
        "fiber_open_channel_auto_accept_min_ckb_funding_amount": 10100000000
    }
    """
    Tests for `list_channels` with the `only_pending` parameter introduced in
    nervosnetwork/fiber#1134.

    When `only_pending` is true the RPC should return:
    - Channels in pre-ChannelReady states (NegotiatingFunding, CollaboratingFundingTx,
      SigningCommitment, AwaitingTxSignatures, AwaitingChannelReady)
    - Failed outbound openings (Closed(FUNDING_ABORTED) / Closed(ABANDONED)) with
      `failure_detail`
    - Inbound channel requests waiting for `accept_channel` (shown as
      NegotiatingFunding with is_acceptor=true on the acceptor node)
    """

    def test_only_pending_shows_pending_channel(self):
        """
        When a channel is opened with auto-accept, it goes through pending states
        before reaching CHANNEL_READY. We verify that `only_pending=true` returns
        the channel while it is in a pending state, and stops returning it once
        it reaches CHANNEL_READY.

        Steps:
        1. Open a channel between fiber1 and fiber2 (auto-accepted).
        2. Wait until the channel reaches CHANNEL_READY on both sides.
        3. Verify `only_pending=true` does NOT include CHANNEL_READY channels on fiber1.
        4. Verify `only_pending=true` does NOT include CHANNEL_READY channels on fiber2.
        5. Verify `only_pending=false` (default) still shows the ready channel on both sides.
        """
        # Step 1: Open channel (auto-accepted, large funding amount)
        self.fiber1.get_client().open_channel(
            {
                "pubkey": self.fiber2.get_pubkey(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
            }
        )

        # Step 2: Wait for CHANNEL_READY on both sides
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_pubkey(), "ChannelReady", 120
        )
        self.wait_for_channel_state(
            self.fiber2.get_client(), self.fiber1.get_pubkey(), "ChannelReady", 120
        )

        # Step 3: only_pending=true should NOT include CHANNEL_READY channels on fiber1
        pending_channels_fiber1 = self.fiber1.get_client().list_channels(
            {"only_pending": True}
        )
        ready_in_pending_fiber1 = [
            ch
            for ch in pending_channels_fiber1["channels"]
            if ch["state"]["state_name"] == "ChannelReady"
        ]
        assert (
            len(ready_in_pending_fiber1) == 0
        ), "CHANNEL_READY channels should not appear when only_pending=true on fiber1"

        # Step 4: only_pending=true should NOT include CHANNEL_READY channels on fiber2
        pending_channels_fiber2 = self.fiber2.get_client().list_channels(
            {"only_pending": True}
        )
        ready_in_pending_fiber2 = [
            ch
            for ch in pending_channels_fiber2["channels"]
            if ch["state"]["state_name"] == "ChannelReady"
        ]
        assert (
            len(ready_in_pending_fiber2) == 0
        ), "CHANNEL_READY channels should not appear when only_pending=true on fiber2"

        # Step 5: Default list (only_pending=false) should still show the channel on both sides
        all_channels_fiber1 = self.fiber1.get_client().list_channels({})
        assert len(all_channels_fiber1["channels"]) >= 1
        assert (
            all_channels_fiber1["channels"][0]["state"]["state_name"] == "ChannelReady"
        )

        all_channels_fiber2 = self.fiber2.get_client().list_channels({})
        assert len(all_channels_fiber2["channels"]) >= 1
        assert (
            all_channels_fiber2["channels"][0]["state"]["state_name"] == "ChannelReady"
        )

    def test_only_pending_default_false(self):
        """
        Verify that the default behavior of `list_channels` (without `only_pending`)
        is unchanged: it returns active channels on both sides.

        Steps:
        1. Open a channel and wait for CHANNEL_READY on both sides.
        2. Call list_channels with only_pending=false (explicit) on both nodes.
        3. Verify the CHANNEL_READY channel is returned on both sides.
        """
        self.fiber1.get_client().open_channel(
            {
                "pubkey": self.fiber2.get_pubkey(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
            }
        )
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_pubkey(), "ChannelReady", 120
        )
        self.wait_for_channel_state(
            self.fiber2.get_client(), self.fiber1.get_pubkey(), "ChannelReady", 120
        )

        # Check fiber1 side
        channels_fiber1 = self.fiber1.get_client().list_channels(
            {"only_pending": False}
        )
        assert len(channels_fiber1["channels"]) >= 1
        assert channels_fiber1["channels"][0]["state"]["state_name"] == "ChannelReady"

        # Check fiber2 side
        channels_fiber2 = self.fiber2.get_client().list_channels(
            {"only_pending": False}
        )
        assert len(channels_fiber2["channels"]) >= 1
        assert channels_fiber2["channels"][0]["state"]["state_name"] == "ChannelReady"

    def test_only_pending_shows_inbound_waiting_for_accept(self):
        """
        When a channel is opened with a funding amount below the auto-accept
        threshold, the accepting node must manually call `accept_channel`.
        Before acceptance, the channel should appear in both nodes'
        `list_channels(only_pending=true)` results.

        Steps:
        1. Get the auto-accept min funding amount.
        2. Open a channel with funding_amount below the threshold (not auto-accepted).
        3. On the initiator (fiber1), verify the pending outbound channel appears.
        4. On the acceptor (fiber2), verify the pending inbound channel appears
           with is_acceptor=true and state NegotiatingFunding.
        5. Accept the channel and wait for CHANNEL_READY on both sides.
        6. Verify only_pending=true no longer returns the channel on both sides.
        """
        # Step 1: Get auto-accept threshold
        node_info = self.fiber1.get_client().node_info()
        auto_accept_min = int(
            node_info["open_channel_auto_accept_min_ckb_funding_amount"], 16
        )

        # Step 2: Open channel below threshold (requires manual accept)
        temporary_channel = self.fiber1.get_client().open_channel(
            {
                "pubkey": self.fiber2.get_pubkey(),
                "funding_amount": hex(auto_accept_min - 1),
                "public": True,
            }
        )
        time.sleep(2)

        # Step 3: On the initiator (fiber1), verify pending outbound channel
        pending_channels_fiber1 = self.fiber1.get_client().list_channels(
            {"only_pending": True}
        )
        outbound_pending = [
            ch
            for ch in pending_channels_fiber1["channels"]
            if ch["is_acceptor"] is False
        ]
        assert len(outbound_pending) >= 1, (
            "Initiator (fiber1) should see pending outbound channel "
            "in list_channels(only_pending=true) before peer accepts"
        )

        # Step 4: On the acceptor (fiber2), verify pending inbound channel
        pending_channels_fiber2 = self.fiber2.get_client().list_channels(
            {"only_pending": True}
        )
        inbound_pending = [
            ch
            for ch in pending_channels_fiber2["channels"]
            if ch["state"]["state_name"] == "NegotiatingFunding"
            and ch["is_acceptor"] is True
        ]
        assert (
            len(inbound_pending) >= 1
        ), "Inbound channel waiting for accept_channel should appear in only_pending=true on fiber2"
        # The remote_balance should reflect the initiator's funding amount
        assert int(inbound_pending[0]["remote_balance"], 16) > 0

        # Step 5: Accept the channel and wait for CHANNEL_READY on both sides
        self.fiber2.get_client().accept_channel(
            {
                "temporary_channel_id": temporary_channel["temporary_channel_id"],
                "funding_amount": hex(100 * 100000000),
            }
        )
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_pubkey(), "ChannelReady", 120
        )
        self.wait_for_channel_state(
            self.fiber2.get_client(), self.fiber1.get_pubkey(), "ChannelReady", 120
        )

        # Step 6: only_pending=true should no longer return the channel on either side
        pending_after_fiber1 = self.fiber1.get_client().list_channels(
            {"only_pending": True}
        )
        ready_in_pending_fiber1 = [
            ch
            for ch in pending_after_fiber1["channels"]
            if ch["state"]["state_name"] == "ChannelReady"
        ]
        assert (
            len(ready_in_pending_fiber1) == 0
        ), "CHANNEL_READY should not appear in only_pending=true on fiber1"

        pending_after_fiber2 = self.fiber2.get_client().list_channels(
            {"only_pending": True}
        )
        ready_in_pending_fiber2 = [
            ch
            for ch in pending_after_fiber2["channels"]
            if ch["state"]["state_name"] == "ChannelReady"
        ]
        assert (
            len(ready_in_pending_fiber2) == 0
        ), "CHANNEL_READY should not appear in only_pending=true on fiber2"

    def test_only_pending_initiator_sees_outbound_pending_channel(self):
        """
        When fiber1 opens a channel with a funding amount below the auto-accept
        threshold, the channel is not auto-accepted and requires manual
        `accept_channel` from fiber2. Before acceptance, the initiator (fiber1)
        should also be able to see its own pending outbound channel via
        `list_channels(only_pending=true)`.

        This complements test_only_pending_shows_inbound_waiting_for_accept which
        only verifies the acceptor (fiber2) side.

        Steps:
        1. Get the auto-accept min funding amount from fiber2.
        2. fiber1 opens a channel with funding_amount below the threshold.
        3. fiber1 calls list_channels(only_pending=true).
        4. Verify the pending outbound channel is returned on the initiator side
           (fiber1) with is_acceptor=false.
        5. fiber2 calls list_channels(only_pending=true).
        6. Verify fiber2 also sees the pending inbound channel.
        """
        # Step 1: Get auto-accept threshold
        node_info = self.fiber2.get_client().node_info()
        auto_accept_min = int(
            node_info["open_channel_auto_accept_min_ckb_funding_amount"], 16
        )

        # Step 2: Open channel below threshold (requires manual accept)
        self.fiber1.get_client().open_channel(
            {
                "pubkey": self.fiber2.get_pubkey(),
                "funding_amount": hex(auto_accept_min - 1),
                "public": True,
            }
        )
        time.sleep(2)

        # Step 3: On the initiator (fiber1), list_channels with only_pending=true
        pending_channels_fiber1 = self.fiber1.get_client().list_channels(
            {"only_pending": True}
        )

        # Step 4: Verify the outbound pending channel appears on the initiator side
        outbound_pending = [
            ch
            for ch in pending_channels_fiber1["channels"]
            if ch["is_acceptor"] is False
        ]
        assert len(outbound_pending) >= 1, (
            "Initiator (fiber1) should see its own pending outbound channel "
            "in list_channels(only_pending=true) before peer accepts"
        )

        # Step 5: On the acceptor (fiber2), list_channels with only_pending=true
        pending_channels_fiber2 = self.fiber2.get_client().list_channels(
            {"only_pending": True}
        )

        # Step 6: Verify fiber2 also sees the pending inbound channel
        inbound_pending = [
            ch
            for ch in pending_channels_fiber2["channels"]
            if ch["is_acceptor"] is True
        ]
        assert len(inbound_pending) >= 1, (
            "Acceptor (fiber2) should see the pending inbound channel "
            "in list_channels(only_pending=true)"
        )

    def test_only_pending_shows_failed_channel_with_failure_detail(self):
        """
        When an outbound channel opening fails (e.g. channel is abandoned during
        opening), the failed record should appear in `list_channels(only_pending=true)`
        with `failure_detail` set. Both the abandoning node and the peer should be checked.

        Steps:
        1. Start fiber3 and open a channel to fiber2 below auto-accept threshold.
        2. Abandon the channel on fiber3 to trigger a failure.
        3. Call list_channels(only_pending=true) on fiber3.
        4. Verify a failed channel record appears with appropriate close flags
           and a non-empty failure_detail string.
        5. Call list_channels(only_pending=true) on fiber2 to verify peer side state.
        """
        # Step 1: Open channel below auto-accept threshold from fiber3 to fiber2
        # so that it requires manual accept, giving us time to abandon it
        account3_private_key = self.generate_account(1000)
        fiber3 = self.start_new_fiber(account3_private_key)
        fiber3.connect_peer(self.fiber2)
        time.sleep(1)

        node_info = fiber3.get_client().node_info()
        auto_accept_min = int(
            node_info["open_channel_auto_accept_min_ckb_funding_amount"], 16
        )

        temporary_channel = fiber3.get_client().open_channel(
            {
                "pubkey": self.fiber2.get_pubkey(),
                "funding_amount": hex(auto_accept_min - 1),
                "public": True,
            }
        )
        time.sleep(1)

        # Step 2: Abandon the channel to make it fail
        fiber3.get_client().abandon_channel(
            {"channel_id": temporary_channel["temporary_channel_id"]}
        )
        time.sleep(2)

        # Step 3: Check only_pending=true on fiber3 (the node that abandoned)
        pending_channels_fiber3 = fiber3.get_client().list_channels(
            {"only_pending": True}
        )

        # Step 4: There MUST be at least one failed/abandoned channel record
        failed_channels_fiber3 = [
            ch
            for ch in pending_channels_fiber3["channels"]
            if ch["state"]["state_name"] == "Closed"
        ]
        assert (
            len(failed_channels_fiber3) >= 1
        ), "Abandoned channel should appear as CLOSED in only_pending=true on fiber3"

        for fc in failed_channels_fiber3:
            # Verify close flags
            assert fc["state"]["state_flags"] in (
                "FUNDING_ABORTED",
            ), f"Unexpected close flag on fiber3: {fc['state']['state_flags']}"
            # Verify failure_detail is present and non-empty
            assert (
                fc["failure_detail"] is not None
            ), "failure_detail must not be None for abandoned channel on fiber3"
            assert isinstance(
                fc["failure_detail"], str
            ), f"failure_detail should be a string, got {type(fc['failure_detail'])}"
            assert (
                len(fc["failure_detail"]) > 0
            ), "failure_detail must not be empty for abandoned channel on fiber3"

        # Step 5: Check only_pending=true on fiber2 (the peer side)
        # After fiber3 abandons, fiber2 should not have a CHANNEL_READY channel
        pending_channels_fiber2 = self.fiber2.get_client().list_channels(
            {"only_pending": True}
        )
        ready_in_pending_fiber2 = [
            ch
            for ch in pending_channels_fiber2["channels"]
            if ch["state"]["state_name"] == "ChannelReady"
        ]
        assert len(ready_in_pending_fiber2) == 0, (
            "CHANNEL_READY should not appear in only_pending=true on fiber2 "
            "after fiber3 abandoned the channel"
        )

    def test_only_pending_with_peer_id_filter(self):
        """
        Verify that `only_pending=true` respects the `peer_id` filter on both sides.

        Steps:
        1. Open channel with fiber2 and wait for CHANNEL_READY.
        2. Create fiber3 and open a channel below auto-accept threshold to fiber1.
        3. On fiber1, list only_pending with peer_id=fiber3 → should have pending channel.
        4. On fiber1, list only_pending with peer_id=fiber2 → should have no pending.
        5. On fiber3, list only_pending with peer_id=fiber1 → should have pending channel.
        """
        # Step 1: Open channel with fiber2 (auto-accepted)
        self.fiber1.get_client().open_channel(
            {
                "pubkey": self.fiber2.get_pubkey(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
            }
        )
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_pubkey(), "ChannelReady", 120
        )

        # Step 2: Create fiber3 and open a channel that needs manual accept
        account3_private_key = self.generate_account(1000)
        fiber3 = self.start_new_fiber(account3_private_key)
        fiber3.connect_peer(self.fiber1)
        time.sleep(1)

        node_info = fiber3.get_client().node_info()
        auto_accept_min = int(
            node_info["open_channel_auto_accept_min_ckb_funding_amount"], 16
        )

        fiber3.get_client().open_channel(
            {
                "pubkey": self.fiber1.get_pubkey(),
                "funding_amount": hex(auto_accept_min - 1),
                "public": True,
            }
        )
        time.sleep(2)

        # Step 3: On fiber1, list only_pending with peer_id=fiber3's peer_id
        pending_from_fiber3 = self.fiber1.get_client().list_channels(
            {"only_pending": True, "pubkey": fiber3.get_pubkey()}
        )
        assert len(pending_from_fiber3["channels"]) >= 1
        for ch in pending_from_fiber3["channels"]:
            assert ch["pubkey"] == fiber3.get_pubkey()

        # Step 4: Verify peer_id filter for fiber2 returns no pending (its channel is READY)
        pending_from_fiber2 = self.fiber1.get_client().list_channels(
            {"only_pending": True, "pubkey": self.fiber2.get_pubkey()}
        )
        ready_channels = [
            ch
            for ch in pending_from_fiber2["channels"]
            if ch["state"]["state_name"] == "ChannelReady"
        ]
        assert (
            len(ready_channels) == 0
        ), "CHANNEL_READY channels should not appear in only_pending=true"

        # Step 5: On fiber3 (initiator), list only_pending with peer_id=fiber1
        pending_on_fiber3 = fiber3.get_client().list_channels(
            {"only_pending": True, "pubkey": self.fiber1.get_pubkey()}
        )
        assert len(pending_on_fiber3["channels"]) >= 1, (
            "Initiator (fiber3) should see pending outbound channel "
            "in list_channels(only_pending=true, peer_id=fiber1)"
        )
        for ch in pending_on_fiber3["channels"]:
            assert ch["pubkey"] == self.fiber1.get_pubkey()

    def test_failure_detail_null_for_ready_channels(self):
        """
        Verify that the `failure_detail` field is null for successfully
        opened channels on both sides.

        Steps:
        1. Open a channel and wait for CHANNEL_READY on both sides.
        2. List channels on both nodes (without only_pending).
        3. Verify `failure_detail` is null on both sides.
        """
        self.fiber1.get_client().open_channel(
            {
                "pubkey": self.fiber2.get_pubkey(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
            }
        )
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_pubkey(), "ChannelReady", 120
        )
        self.wait_for_channel_state(
            self.fiber2.get_client(), self.fiber1.get_pubkey(), "ChannelReady", 120
        )

        # Check fiber1 side
        channels_fiber1 = self.fiber1.get_client().list_channels({})
        assert len(channels_fiber1["channels"]) >= 1
        assert (
            channels_fiber1["channels"][0]["failure_detail"] is None
        ), "failure_detail should be null for a successfully opened channel on fiber1"

        # Check fiber2 side
        channels_fiber2 = self.fiber2.get_client().list_channels({})
        assert len(channels_fiber2["channels"]) >= 1
        assert (
            channels_fiber2["channels"][0]["failure_detail"] is None
        ), "failure_detail should be null for a successfully opened channel on fiber2"

    def test_only_pending_peer_disconnect_shows_failure(self):
        """
        When a peer disconnects during channel opening, the channel should
        appear as failed in `list_channels(only_pending=true)`. Both sides
        should be checked.

        Steps:
        1. Start fiber3 and connect to fiber2.
        2. Open a channel from fiber3 to fiber2 below auto-accept threshold.
        3. Disconnect fiber2 from fiber3 to simulate disconnect before accept.
        4. Wait for the channel actor to detect the disconnect and mark failure.
        5. Verify list_channels(only_pending=true) on fiber3 has no CHANNEL_READY.
        6. Verify list_channels(only_pending=true) on fiber2 has no CHANNEL_READY.
        7. Verify normal list_channels on both sides has no CHANNEL_READY.
        """
        account3_private_key = self.generate_account(1000)
        fiber3 = self.start_new_fiber(account3_private_key)
        fiber3.connect_peer(self.fiber2)
        time.sleep(1)

        node_info = fiber3.get_client().node_info()
        auto_accept_min = int(
            node_info["open_channel_auto_accept_min_ckb_funding_amount"], 16
        )

        temporary_channel = fiber3.get_client().open_channel(
            {
                "pubkey": self.fiber2.get_pubkey(),
                "funding_amount": hex(auto_accept_min - 1),
                "public": True,
            }
        )
        time.sleep(1)

        # Disconnect fiber2 from fiber3
        fiber3.get_client().disconnect_peer({"pubkey": self.fiber2.get_pubkey()})
        # Wait for channel to detect disconnection and fail
        time.sleep(5)

        # Step 5: Check pending channels on fiber3
        pending_channels_fiber3 = fiber3.get_client().list_channels(
            {"only_pending": True}
        )
        ready_in_pending_fiber3 = [
            ch
            for ch in pending_channels_fiber3["channels"]
            if ch["state"]["state_name"] == "ChannelReady"
        ]
        assert (
            len(ready_in_pending_fiber3) == 0
        ), "CHANNEL_READY channels should not appear when only_pending=true on fiber3"

        # Step 6: Check pending channels on fiber2 (the peer side)
        pending_channels_fiber2 = self.fiber2.get_client().list_channels(
            {"only_pending": True}
        )
        ready_in_pending_fiber2 = [
            ch
            for ch in pending_channels_fiber2["channels"]
            if ch["state"]["state_name"] == "ChannelReady"
        ]
        assert (
            len(ready_in_pending_fiber2) == 0
        ), "CHANNEL_READY channels should not appear when only_pending=true on fiber2"

        # Step 7: Also verify no CHANNEL_READY in the normal list on both sides
        normal_channels_fiber3 = fiber3.get_client().list_channels({})
        ready_channels_fiber3 = [
            ch
            for ch in normal_channels_fiber3["channels"]
            if ch["state"]["state_name"] == "ChannelReady"
        ]
        assert len(ready_channels_fiber3) == 0

        normal_channels_fiber2 = self.fiber2.get_client().list_channels({})
        ready_channels_fiber2 = [
            ch
            for ch in normal_channels_fiber2["channels"]
            if ch["state"]["state_name"] == "ChannelReady"
            and ch["pubkey"] == fiber3.get_pubkey()
        ]
        assert len(ready_channels_fiber2) == 0

    def test_only_pending_survives_restart_ready_channel(self):
        """
        After a channel reaches CHANNEL_READY, restarting both nodes should not
        affect the `only_pending=true` query: the CHANNEL_READY channel should
        still be excluded from `only_pending=true` and still visible in the
        default `list_channels` on both sides.

        Steps:
        1. Open a channel and wait for CHANNEL_READY on both sides.
        2. Verify only_pending=true excludes it on both sides before restart.
        3. Restart fiber1 and fiber2.
        4. Reconnect and wait for peers.
        5. Verify only_pending=true still excludes the CHANNEL_READY channel
           on both sides after restart.
        6. Verify default list_channels still shows the CHANNEL_READY channel
           on both sides after restart.
        """
        # Step 1: Open channel and wait for CHANNEL_READY
        self.fiber1.get_client().open_channel(
            {
                "pubkey": self.fiber2.get_pubkey(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
            }
        )
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_pubkey(), "ChannelReady", 120
        )
        self.wait_for_channel_state(
            self.fiber2.get_client(), self.fiber1.get_pubkey(), "ChannelReady", 120
        )

        # Step 2: Verify only_pending=true excludes CHANNEL_READY before restart
        pending_fiber1_before = self.fiber1.get_client().list_channels(
            {"only_pending": True}
        )
        assert all(
            ch["state"]["state_name"] != "ChannelReady"
            for ch in pending_fiber1_before["channels"]
        ), "CHANNEL_READY should not appear in only_pending=true on fiber1 before restart"

        pending_fiber2_before = self.fiber2.get_client().list_channels(
            {"only_pending": True}
        )
        assert all(
            ch["state"]["state_name"] != "ChannelReady"
            for ch in pending_fiber2_before["channels"]
        ), "CHANNEL_READY should not appear in only_pending=true on fiber2 before restart"

        # Step 3: Restart both fiber nodes
        self.fiber1.stop()
        self.fiber2.stop()
        self.fiber1.start()
        self.fiber2.start()
        time.sleep(3)

        # Step 4: Reconnect peers
        self.fiber1.connect_peer(self.fiber2)
        time.sleep(3)

        # Step 5: Verify only_pending=true still excludes CHANNEL_READY after restart
        pending_fiber1_after = self.fiber1.get_client().list_channels(
            {"only_pending": True}
        )
        assert all(
            ch["state"]["state_name"] != "ChannelReady"
            for ch in pending_fiber1_after["channels"]
        ), "CHANNEL_READY should not appear in only_pending=true on fiber1 after restart"

        pending_fiber2_after = self.fiber2.get_client().list_channels(
            {"only_pending": True}
        )
        assert all(
            ch["state"]["state_name"] != "ChannelReady"
            for ch in pending_fiber2_after["channels"]
        ), "CHANNEL_READY should not appear in only_pending=true on fiber2 after restart"

        # Step 6: Verify default list still shows the CHANNEL_READY channel after restart
        all_channels_fiber1 = self.fiber1.get_client().list_channels({})
        ready_fiber1 = [
            ch
            for ch in all_channels_fiber1["channels"]
            if ch["state"]["state_name"] == "ChannelReady"
        ]
        assert (
            len(ready_fiber1) >= 1
        ), "CHANNEL_READY channel should still be visible in default list on fiber1 after restart"

        all_channels_fiber2 = self.fiber2.get_client().list_channels({})
        ready_fiber2 = [
            ch
            for ch in all_channels_fiber2["channels"]
            if ch["state"]["state_name"] == "ChannelReady"
        ]
        assert (
            len(ready_fiber2) >= 1
        ), "CHANNEL_READY channel should still be visible in default list on fiber2 after restart"

    def test_only_pending_survives_restart_abandoned_channel(self):
        """
        After a channel is abandoned and visible in `only_pending=true` with
        a non-empty `failure_detail`, restarting the node should preserve that
        failed record: it should still appear in `only_pending=true` with the
        same failure information.

        Steps:
        1. Start fiber3 and open a channel to fiber2 below auto-accept threshold.
        2. Abandon the channel on fiber3.
        3. Verify the abandoned channel appears in only_pending=true on fiber3
           with failure_detail and correct close flags.
        4. Restart fiber3.
        5. After restart, verify the abandoned channel still appears in
           only_pending=true on fiber3 with failure_detail and correct close flags.
        """
        # Step 1: Start fiber3 and open channel below auto-accept threshold
        account3_private_key = self.generate_account(1000)
        fiber3 = self.start_new_fiber(account3_private_key)
        fiber3.connect_peer(self.fiber2)
        time.sleep(1)

        node_info = fiber3.get_client().node_info()
        auto_accept_min = int(
            node_info["open_channel_auto_accept_min_ckb_funding_amount"], 16
        )

        temporary_channel = fiber3.get_client().open_channel(
            {
                "pubkey": self.fiber2.get_pubkey(),
                "funding_amount": hex(auto_accept_min - 1),
                "public": True,
            }
        )
        time.sleep(1)

        # Step 2: Abandon the channel
        fiber3.get_client().abandon_channel(
            {"channel_id": temporary_channel["temporary_channel_id"]}
        )
        time.sleep(2)

        # Step 3: Verify before restart — failed channel must appear with failure_detail
        pending_before = fiber3.get_client().list_channels({"only_pending": True})
        failed_before = [
            ch
            for ch in pending_before["channels"]
            if ch["state"]["state_name"] == "Closed"
        ]
        assert (
            len(failed_before) >= 1
        ), "Abandoned channel should appear as CLOSED in only_pending=true on fiber3 before restart"

        for fc in failed_before:
            assert fc["state"]["state_flags"] in (
                "FUNDING_ABORTED",
            ), f"Unexpected close flag before restart: {fc['state']['state_flags']}"
            assert (
                fc["failure_detail"] is not None
            ), "failure_detail must not be None for abandoned channel before restart"
            assert (
                isinstance(fc["failure_detail"], str) and len(fc["failure_detail"]) > 0
            ), "failure_detail must be a non-empty string before restart"

        # Save channel info for comparison after restart
        failed_channel_id_before = failed_before[0]["channel_id"]
        failure_detail_before = failed_before[0]["failure_detail"]

        # Step 4: Restart fiber3
        fiber3.stop()
        fiber3.start()
        time.sleep(3)

        # Step 5: After restart, verify the abandoned channel still appears
        pending_after = fiber3.get_client().list_channels({"only_pending": True})
        failed_after = [
            ch
            for ch in pending_after["channels"]
            if ch["state"]["state_name"] == "Closed"
        ]
        assert (
            len(failed_after) >= 1
        ), "Abandoned channel should still appear as CLOSED in only_pending=true on fiber3 after restart"

        for fc in failed_after:
            assert fc["state"]["state_flags"] in (
                "FUNDING_ABORTED",
            ), f"Unexpected close flag after restart: {fc['state']['state_flags']}"
            assert (
                fc["failure_detail"] is not None
            ), "failure_detail must not be None for abandoned channel after restart"
            assert (
                isinstance(fc["failure_detail"], str) and len(fc["failure_detail"]) > 0
            ), "failure_detail must be a non-empty string after restart"

        # Verify same channel is present and failure_detail is preserved
        failed_channel_ids_after = [fc["channel_id"] for fc in failed_after]
        assert (
            failed_channel_id_before in failed_channel_ids_after
        ), "The same abandoned channel should be present after restart"
        matching_channel = next(
            (fc for fc in failed_after if fc["channel_id"] == failed_channel_id_before),
            None,
        )
        assert (
            matching_channel is not None
        ), "Abandoned channel should be found by channel_id after restart"
        assert matching_channel["failure_detail"] == failure_detail_before, (
            f"failure_detail should be preserved after restart. "
            f"Before: {failure_detail_before}, After: {matching_channel['failure_detail']}"
        )

    def test_only_pending_no_duplicate_channel_ids_before_accept(self):
        """
        When fiber1 opens a channel with funding_amount below the auto-accept
        threshold (requiring manual accept_channel from fiber2), the
        `list_channels(only_pending=true)` results on both sides should:

        - Return exactly one channel entry per channel_id (no duplicates).
        - On the initiator (fiber1), local_balance should reflect the
          funding amount (not 0x0).
        - On the acceptor (fiber2), remote_balance should reflect the
          initiator's funding amount (not 0x0), and there should be
          exactly one channel entry (not two with different remote_balance).

        This reproduces a bug where fiber2 returned two channels with the
        same channel_id: one with remote_balance=0x0 and another with
        remote_balance != 0x0.

        Steps:
        1. Get the auto-accept min funding amount.
        2. Open a channel with funding_amount below the threshold.
        3. On both sides, call list_channels(only_pending=true).
        4. Verify no duplicate channel_ids on either side.
        5. Verify fiber1's local_balance matches funding_amount.
        6. Verify fiber2's remote_balance matches funding_amount.
        """
        # Step 1: Get auto-accept threshold
        node_info = self.fiber1.get_client().node_info()
        auto_accept_min = int(
            node_info["open_channel_auto_accept_min_ckb_funding_amount"], 16
        )

        # Step 2: Open channel below threshold (requires manual accept)
        funding_amount = auto_accept_min - 1
        self.fiber1.get_client().open_channel(
            {
                "pubkey": self.fiber2.get_pubkey(),
                "funding_amount": hex(funding_amount),
                "public": True,
            }
        )
        time.sleep(2)

        # Step 3: Query both sides
        pending_fiber1 = self.fiber1.get_client().list_channels({"only_pending": True})
        pending_fiber2 = self.fiber2.get_client().list_channels({"only_pending": True})

        # Step 4a: Verify no duplicate channel_ids on fiber1
        channel_ids_fiber1 = [ch["channel_id"] for ch in pending_fiber1["channels"]]
        assert len(channel_ids_fiber1) == len(set(channel_ids_fiber1)), (
            f"fiber1 list_channels(only_pending=true) returned duplicate channel_ids: "
            f"{channel_ids_fiber1}"
        )

        # Step 4b: Verify no duplicate channel_ids on fiber2
        channel_ids_fiber2 = [ch["channel_id"] for ch in pending_fiber2["channels"]]
        assert len(channel_ids_fiber2) == len(set(channel_ids_fiber2)), (
            f"fiber2 list_channels(only_pending=true) returned duplicate channel_ids: "
            f"{channel_ids_fiber2}"
        )

        # Step 5: Verify fiber1 (initiator) has exactly one pending outbound channel
        # and its local_balance reflects the funding_amount
        outbound_pending = [
            ch for ch in pending_fiber1["channels"] if ch["is_acceptor"] is False
        ]
        assert len(outbound_pending) == 1, (
            f"fiber1 should have exactly 1 pending outbound channel, "
            f"got {len(outbound_pending)}"
        )
        fiber1_local_balance = int(outbound_pending[0]["local_balance"], 16)
        assert fiber1_local_balance > 0, (
            f"Initiator (fiber1) local_balance should reflect the funding amount "
            f"(expected > 0), got {hex(fiber1_local_balance)}"
        )

        # Step 6: Verify fiber2 (acceptor) has exactly one pending inbound channel
        # and its remote_balance reflects the initiator's funding amount
        inbound_pending = [
            ch for ch in pending_fiber2["channels"] if ch["is_acceptor"] is True
        ]
        assert len(inbound_pending) == 1, (
            f"fiber2 should have exactly 1 pending inbound channel, "
            f"got {len(inbound_pending)}"
        )
        fiber2_remote_balance = int(inbound_pending[0]["remote_balance"], 16)
        assert fiber2_remote_balance > 0, (
            f"Acceptor (fiber2) remote_balance should reflect the initiator's "
            f"funding amount (expected > 0), got {hex(fiber2_remote_balance)}"
        )
