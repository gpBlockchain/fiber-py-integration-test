"""Regression test for fiber PR #1335.

PR #1335 — *Fix preimage retention for MPP during force-close scenarios*
(fixes issue https://github.com/nervosnetwork/fiber/issues/1332).

Background
----------
For an MPP payment that splits across two parallel *upstream* channels of a
forwarding node, the forwarding node used to drop the payment preimage as
soon as the **online** split was fulfilled off-chain.  If the **other**
upstream channel had been force-closed in the meantime, its received-HTLC
output on-chain could no longer be claimed (the watchtower / on-chain
settlement path needs the preimage), so the forwarding node would lose the
forwarded funds and the on-chain settlement would stall.

The PR keeps the preimage as long as another TLC with the same
`payment_hash` is still pending on-chain settlement
(``has_onchain_tlc_for_payment_hash``).

Topology
--------
::

    fiber1 (sender)  ==[ chA ]==  fiber2 (mid)  ==[ chC ]==  fiber3 (receiver)
                     ==[ chB ]==

The Python integration test cannot read the preimage store directly, so it
asserts the observable consequences of the fix:

1. Both partial TLCs reach fiber3 (invoice goes to ``Received``).
2. fiber2 force-closes the first upstream channel (chA) while both TLCs
   are still in-flight.
3. fiber3 settles the invoice.
4. The **online** split is fulfilled off-chain — the corresponding inbound
   TLC on fiber2's chB and outbound TLC on fiber1's chB disappear.
5. The **force-closed** split's inbound TLC on fiber2's chA must remain
   pending (waiting for on-chain settlement).  This proves the new
   retention path is on: with the old code, the preimage would have been
   dropped together with the online split, and the on-chain TLC could
   never be claimed.
"""

import hashlib
import time

from framework.basic_fiber import FiberTest


def _sha256_hex(preimage_hex: str) -> str:
    raw = bytes.fromhex(preimage_hex.replace("0x", ""))
    return "0x" + hashlib.sha256(raw).hexdigest()


# Channel capacities (in shannons). Each upstream channel can carry less
# than the invoice amount on its own, forcing the MPP router to split.
UPSTREAM_CAPACITY = 500 * 100000000
DOWNSTREAM_CAPACITY = 2000 * 100000000
INVOICE_AMOUNT = 600 * 100000000


class TestPR1335MppForceClosePreimageRetention(FiberTest):

    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 5}

    def _list_channels_by_peer(self, fiber, peer_fiber):
        return fiber.get_client().list_channels(
            {"pubkey": peer_fiber.get_pubkey(), "include_closed": True}
        )["channels"]

    def _pending_payment_hashes(self, channel):
        return [tlc["payment_hash"] for tlc in channel.get("pending_tlcs", [])]

    def test_preimage_retained_for_onchain_split(self):
        # --- Topology -----------------------------------------------------
        fiber3 = self.start_new_fiber(
            self.generate_account(10000, self.fiber1.account_private, 0)
        )
        # Two parallel upstream channels fiber1 -> fiber2.
        self.open_channel(self.fiber1, self.fiber2, UPSTREAM_CAPACITY, 0, 0, 0)
        self.open_channel(self.fiber1, self.fiber2, UPSTREAM_CAPACITY, 0, 0, 0)
        # Single downstream channel fiber2 -> fiber3 with enough room for
        # both forwarded splits.
        self.open_channel(self.fiber2, fiber3, DOWNSTREAM_CAPACITY, 0, 0, 0)

        # Wait for graph sync so the sender sees all three channels.
        self.wait_graph_channels_sync(self.fiber1, 3, timeout=120)

        upstream_channels = self._list_channels_by_peer(self.fiber2, self.fiber1)
        assert (
            len(upstream_channels) == 2
        ), f"expected 2 parallel upstream channels, got {len(upstream_channels)}"
        channel_a_id = upstream_channels[0]["channel_id"]
        channel_b_id = upstream_channels[1]["channel_id"]

        # --- Hold MPP invoice on the receiver -----------------------------
        preimage = self.generate_random_preimage()
        payment_hash = _sha256_hex(preimage)
        invoice = fiber3.get_client().new_invoice(
            {
                "amount": hex(INVOICE_AMOUNT),
                "currency": "Fibd",
                "description": "PR-1335 MPP force-close hold invoice",
                "payment_hash": payment_hash,
                "hash_algorithm": "sha256",
                "expiry": "0xe10",
                "final_cltv": "0x28",
                "allow_mpp": True,
            }
        )

        # --- Send the MPP payment -----------------------------------------
        payment = self.fiber1.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "max_parts": hex(2),
                "max_fee_rate": hex(1000000000000000),
            }
        )
        assert payment["payment_hash"] == payment_hash

        # Wait until both partial TLCs land on fiber3 (invoice = Received).
        self.wait_invoice_state(fiber3, payment_hash, "Received", timeout=120)

        # Sanity: fiber2 must be forwarding two inbound TLCs (one per
        # upstream channel) for this payment_hash.
        deadline = time.time() + 30
        while time.time() < deadline:
            upstream_channels = self._list_channels_by_peer(self.fiber2, self.fiber1)
            inbound_per_channel = {
                ch["channel_id"]: self._pending_payment_hashes(ch).count(payment_hash)
                for ch in upstream_channels
            }
            if (
                inbound_per_channel.get(channel_a_id, 0) >= 1
                and inbound_per_channel.get(channel_b_id, 0) >= 1
            ):
                break
            time.sleep(1)
        else:
            assert False, (
                "fiber2 did not receive one inbound TLC per upstream "
                f"channel: {inbound_per_channel}"
            )

        # --- Force-close channel A (one of the upstream channels) ---------
        self.fiber2.get_client().shutdown_channel(
            {"channel_id": channel_a_id, "force": True}
        )
        force_tx = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, force_tx)
        # A few extra blocks so the close is fully observed by the node.
        self.node.getClient().generate_epochs("0x1", wait_time=0)

        # Confirm channel A is closed while channel B is still ChannelReady.
        for _ in range(30):
            upstream_channels = self._list_channels_by_peer(self.fiber2, self.fiber1)
            by_id = {ch["channel_id"]: ch for ch in upstream_channels}
            state_a = by_id[channel_a_id]["state"]["state_name"]
            state_b = by_id[channel_b_id]["state"]["state_name"]
            if state_a == "Closed" and state_b == "ChannelReady":
                break
            time.sleep(1)
        else:
            assert False, f"channel states unexpected: A={state_a}, B={state_b}"

        # --- Settle the invoice on the receiver ---------------------------
        fiber3.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )

        # The online split must be fulfilled off-chain: channel B's inbound
        # TLC on fiber2 (and the corresponding outbound on fiber1) goes
        # away. The force-closed split's inbound TLC on channel A must
        # remain pending until on-chain settlement completes.
        deadline = time.time() + 60
        chan_a = chan_b = None
        while time.time() < deadline:
            upstream_channels = self._list_channels_by_peer(self.fiber2, self.fiber1)
            by_id = {ch["channel_id"]: ch for ch in upstream_channels}
            chan_a = by_id.get(channel_a_id)
            chan_b = by_id.get(channel_b_id)
            online_cleared = (
                chan_b is not None
                and self._pending_payment_hashes(chan_b).count(payment_hash) == 0
            )
            onchain_pending = (
                chan_a is not None
                and self._pending_payment_hashes(chan_a).count(payment_hash) >= 1
            )
            if online_cleared and onchain_pending:
                break
            time.sleep(1)
        else:
            assert False, (
                "after settle: expected online split cleared and on-chain "
                f"split still pending; chan_a.pending_tlcs="
                f"{chan_a and chan_a.get('pending_tlcs')}, "
                f"chan_b.pending_tlcs="
                f"{chan_b and chan_b.get('pending_tlcs')}"
            )

        # Receiver's invoice should be Paid once the off-chain settle event
        # is processed (this also implicitly checks the preimage was
        # accepted and the invoice store committed it).
        self.wait_invoice_state(fiber3, payment_hash, "Paid", timeout=60)

        # Mid-assertion (PR-1335 core, off-chain side): the force-closed
        # channel still carries the inbound TLC for `payment_hash`. With
        # the old code, the preimage would already have been removed
        # together with the online split — leaving the on-chain TLC
        # unclaimable.
        chan_a_mid = {
            ch["channel_id"]: ch
            for ch in self._list_channels_by_peer(self.fiber2, self.fiber1)
        }[channel_a_id]
        on_chain_tlcs = [
            tlc
            for tlc in chan_a_mid.get("pending_tlcs", [])
            if tlc["payment_hash"] == payment_hash
        ]
        assert len(on_chain_tlcs) >= 1, (
            "force-closed channel must retain its inbound TLC pending "
            "on-chain settlement; pending_tlcs="
            f"{chan_a_mid.get('pending_tlcs')}"
        )
        split_amount_on_a = int(on_chain_tlcs[0]["amount"], 16)

        # --- On-chain verification ---------------------------------------
        # The real proof of PR-1335: fiber2's watchtower must use the
        # *retained* preimage to spend the received-HTLC output of the
        # force-closed commitment tx. Mine ~600 blocks (one delay epoch
        # window used by the existing watchtower tests) so the watchtower
        # check interval triggers a settle tx on-chain.
        for _ in range(600):
            self.Miner.miner_with_version(self.node, "0x0")

        deadline = time.time() + 60
        tx_trace = []
        while time.time() < deadline:
            tx_trace = self.get_ln_tx_trace(force_tx)
            # tx_trace[0] = the force-close commitment tx itself
            # tx_trace[1] = the watchtower preimage-unlock settle tx
            if len(tx_trace) >= 2:
                break
            for _ in range(30):
                self.Miner.miner_with_version(self.node, "0x0")
            time.sleep(2)
        assert len(tx_trace) >= 2, (
            "watchtower did not spend the on-chain received-HTLC cell "
            "using the retained preimage; only saw force-close tx. "
            "tx_trace="
            f"{[t['tx_hash'] for t in tx_trace]}"
        )

        # The preimage-unlock tx must consume capacity equal to the TLC
        # amount that landed on channel A (modulo tx fee, sub-CKB).
        settle_msg = tx_trace[1]["msg"]
        capacity_diff = (
            settle_msg["input_cells"][0]["capacity"]
            - settle_msg["output_cells"][0]["capacity"]
        )
        assert abs(capacity_diff - split_amount_on_a) < 100000000, (
            f"settle tx capacity diff {capacity_diff} does not match "
            f"on-chain split amount {split_amount_on_a}"
        )
        # Reaching this point proves PR-1335: fiber2 still held the
        # preimage after the online split was fulfilled, and used it to
        # produce the on-chain settle tx for the force-closed split.

    def test_preimage_retained_when_settle_before_channel_closed(self):
        """Variant of the above: settle the invoice **immediately after
        the force-close commitment tx is confirmed on-chain**, without
        waiting for fiber2's channel A to transition to ``Closed``.

        Motivation (from PR-1335 discussion): in the original repro the
        forwarding node could already drop the preimage if the *online*
        split was fulfilled while channel A was still mid-shutdown (i.e.
        before its state machine fully reached ``Closed``). This test
        pins down that timing — settle is invoked as soon as the
        shutdown tx lands in the tx pool / one block confirmation — and
        asserts the same observable consequences of the fix as the main
        test (online split off-chain cleared, force-closed split
        retained, invoice → Paid, watchtower settles on-chain with the
        retained preimage).
        """

        # --- Topology -----------------------------------------------------
        fiber3 = self.start_new_fiber(
            self.generate_account(10000, self.fiber1.account_private, 0)
        )
        self.open_channel(self.fiber1, self.fiber2, UPSTREAM_CAPACITY, 0, 0, 0)
        self.open_channel(self.fiber1, self.fiber2, UPSTREAM_CAPACITY, 0, 0, 0)
        self.open_channel(self.fiber2, fiber3, DOWNSTREAM_CAPACITY, 0, 0, 0)
        self.wait_graph_channels_sync(self.fiber1, 3, timeout=120)

        upstream_channels = self._list_channels_by_peer(self.fiber2, self.fiber1)
        assert (
            len(upstream_channels) == 2
        ), f"expected 2 parallel upstream channels, got {len(upstream_channels)}"
        channel_a_id = upstream_channels[0]["channel_id"]
        channel_b_id = upstream_channels[1]["channel_id"]

        # --- Hold MPP invoice on the receiver -----------------------------
        preimage = self.generate_random_preimage()
        payment_hash = _sha256_hex(preimage)
        invoice = fiber3.get_client().new_invoice(
            {
                "amount": hex(INVOICE_AMOUNT),
                "currency": "Fibd",
                "description": "PR-1335 settle-before-closed hold invoice",
                "payment_hash": payment_hash,
                "hash_algorithm": "sha256",
                "expiry": "0xe10",
                "final_cltv": "0x28",
                "allow_mpp": True,
            }
        )

        # --- Send the MPP payment -----------------------------------------
        payment = self.fiber1.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "max_parts": hex(2),
                "max_fee_rate": hex(1000000000000000),
            }
        )
        assert payment["payment_hash"] == payment_hash
        self.wait_invoice_state(fiber3, payment_hash, "Received", timeout=120)

        # Sanity: one inbound TLC per upstream channel on fiber2.
        deadline = time.time() + 30
        while time.time() < deadline:
            upstream_channels = self._list_channels_by_peer(self.fiber2, self.fiber1)
            inbound_per_channel = {
                ch["channel_id"]: self._pending_payment_hashes(ch).count(payment_hash)
                for ch in upstream_channels
            }
            if (
                inbound_per_channel.get(channel_a_id, 0) >= 1
                and inbound_per_channel.get(channel_b_id, 0) >= 1
            ):
                break
            time.sleep(1)
        else:
            assert False, (
                "fiber2 did not receive one inbound TLC per upstream "
                f"channel: {inbound_per_channel}"
            )

        # --- Force-close channel A and settle immediately after the
        # commitment tx is confirmed on-chain (do NOT wait for state =
        # Closed). This is the timing variation PR-1335 must also cover.
        self.fiber2.get_client().shutdown_channel(
            {"channel_id": channel_a_id, "force": True}
        )
        force_tx = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, force_tx)

        # Settle immediately — channel A is still in the shutdown flow,
        # NOT yet Closed. With the buggy version, fulfilling the online
        # split here would also evict the preimage even though channel
        # A's HTLC is now on-chain only.
        fiber3.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )

        # Off-chain: online split (chB) clears; on-chain split (chA)
        # retained.
        deadline = time.time() + 60
        chan_a = chan_b = None
        while time.time() < deadline:
            upstream_channels = self._list_channels_by_peer(self.fiber2, self.fiber1)
            by_id = {ch["channel_id"]: ch for ch in upstream_channels}
            chan_a = by_id.get(channel_a_id)
            chan_b = by_id.get(channel_b_id)
            online_cleared = (
                chan_b is not None
                and self._pending_payment_hashes(chan_b).count(payment_hash) == 0
            )
            onchain_pending = (
                chan_a is not None
                and self._pending_payment_hashes(chan_a).count(payment_hash) >= 1
            )
            if online_cleared and onchain_pending:
                break
            time.sleep(1)
        else:
            assert False, (
                "after settle-before-closed: expected online split cleared "
                "and on-chain split still pending; "
                f"chan_a.pending_tlcs={chan_a and chan_a.get('pending_tlcs')}, "
                f"chan_b.pending_tlcs={chan_b and chan_b.get('pending_tlcs')}"
            )

        self.wait_invoice_state(fiber3, payment_hash, "Paid", timeout=60)

        chan_a_mid = {
            ch["channel_id"]: ch
            for ch in self._list_channels_by_peer(self.fiber2, self.fiber1)
        }[channel_a_id]
        on_chain_tlcs = [
            tlc
            for tlc in chan_a_mid.get("pending_tlcs", [])
            if tlc["payment_hash"] == payment_hash
        ]
        assert len(on_chain_tlcs) >= 1, (
            "force-closed channel must retain its inbound TLC pending "
            "on-chain settlement even when settle was issued before "
            "channel reached Closed; pending_tlcs="
            f"{chan_a_mid.get('pending_tlcs')}"
        )
        split_amount_on_a = int(on_chain_tlcs[0]["amount"], 16)

        # --- On-chain verification: same as the main case ----------------
        for _ in range(600):
            self.Miner.miner_with_version(self.node, "0x0")

        deadline = time.time() + 60
        tx_trace = []
        while time.time() < deadline:
            tx_trace = self.get_ln_tx_trace(force_tx)
            if len(tx_trace) >= 2:
                break
            for _ in range(30):
                self.Miner.miner_with_version(self.node, "0x0")
            time.sleep(2)
        assert len(tx_trace) >= 2, (
            "watchtower did not spend the on-chain received-HTLC cell "
            "using the retained preimage (settle-before-closed timing); "
            f"tx_trace={[t['tx_hash'] for t in tx_trace]}"
        )

        settle_msg = tx_trace[1]["msg"]
        capacity_diff = (
            settle_msg["input_cells"][0]["capacity"]
            - settle_msg["output_cells"][0]["capacity"]
        )
        assert abs(capacity_diff - split_amount_on_a) < 100000000, (
            f"settle tx capacity diff {capacity_diff} does not match "
            f"on-chain split amount {split_amount_on_a}"
        )
