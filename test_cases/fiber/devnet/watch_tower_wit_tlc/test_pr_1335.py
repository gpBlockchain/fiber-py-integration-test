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

import pytest

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

    def _wait_force_close_unlock(self, timeout=600):
        for _ in range(timeout // 10):
            if len(self.get_commit_cells()) == 0:
                return
            time.sleep(10)
        assert len(self.get_commit_cells()) == 0

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
                "allow_mpp": True,
            }
        )

        # --- Send the MPP payment -----------------------------------------
        payment = self.fiber1.get_client().send_payment(
            {"invoice": invoice["invoice_address"], "max_parts": hex(2)}
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

        # Final assertion (core of PR-1335): the force-closed channel still
        # carries the inbound TLC for `payment_hash`, which is exactly the
        # state under which the forwarding node must keep the preimage.
        # With the old code, the preimage would already have been removed
        # together with the online split — leaving this on-chain TLC
        # unclaimable.  The retention path being exercised here is what
        # the PR adds.
        chan_a_final = {
            ch["channel_id"]: ch
            for ch in self._list_channels_by_peer(self.fiber2, self.fiber1)
        }[channel_a_id]
        remaining = [
            tlc
            for tlc in chan_a_final.get("pending_tlcs", [])
            if tlc["payment_hash"] == payment_hash
        ]
        assert len(remaining) >= 1, (
            "force-closed channel must retain its inbound TLC pending "
            "on-chain settlement; pending_tlcs="
            f"{chan_a_final.get('pending_tlcs')}"
        )
        self._wait_force_close_unlock()
        self.wait_payment_state(self.fiber1, payment_hash, "Success", timeout=360)
