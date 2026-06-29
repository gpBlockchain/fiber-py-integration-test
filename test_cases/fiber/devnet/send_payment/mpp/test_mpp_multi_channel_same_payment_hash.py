"""Regression test for fiber PR #1335 — scenario F.1.

Multi-channel same-payment_hash mixed state:
- Two parallel channels fiber1 <-> fiber2, one channel fiber2 -> fiber3.
- MPP payment splits across both fiber1 <-> fiber2 channels.
- fiber1 force-closes one of the upstream channels while both TLCs are
  still in flight.
- fiber3 settles the invoice off-chain.

Expected:
- The surviving channel's TLC is fulfilled off-chain (preimage propagates
  back to fiber1).
- The force-closed channel's on-chain received-HTLC output is later
  spent by fiber2's watchtower using the *retained* preimage (the PR-1335
  fix). Without the fix, fiber2 would have dropped the preimage after the
  off-chain split was fulfilled and the on-chain cell would stay locked.
"""

import time

from framework.basic_fiber import FiberTest
from framework.util import ckb_hash

UPSTREAM_CAPACITY = 1000 * 100000000
DOWNSTREAM_CAPACITY = 3000 * 100000000
INVOICE_AMOUNT = 1500 * 100000000


class TestMppMultiChannelSamePaymentHash(FiberTest):

    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 5}

    def _channels_by_peer(self, fiber, peer_fiber):
        return fiber.get_client().list_channels(
            {"pubkey": peer_fiber.get_pubkey(), "include_closed": True}
        )["channels"]

    def _pending_hashes(self, channel):
        return [tlc["payment_hash"] for tlc in channel.get("pending_tlcs", [])]

    def test_mpp_with_same_payment_hash(self):
        # --- Topology -----------------------------------------------------
        fiber3 = self.start_new_fiber(self.generate_account(10000))
        self.open_channel(self.fiber1, self.fiber2, UPSTREAM_CAPACITY, 0, 0, 0)
        self.open_channel(self.fiber1, self.fiber2, UPSTREAM_CAPACITY, 0, 0, 0)
        self.open_channel(self.fiber2, fiber3, DOWNSTREAM_CAPACITY, 0, 0, 0)
        self.wait_graph_channels_sync(self.fiber1, 3, timeout=120)

        upstream = self._channels_by_peer(self.fiber1, self.fiber2)
        assert len(upstream) == 2, f"expected 2 upstream channels, got {len(upstream)}"
        force_closed_channel = upstream[0]["channel_id"]
        offchain_channel = upstream[1]["channel_id"]

        # --- Hold MPP invoice on the receiver -----------------------------
        preimage = self.generate_random_preimage()
        payment_hash = ckb_hash(preimage)
        invoice = fiber3.get_client().new_invoice(
            {
                "amount": hex(INVOICE_AMOUNT),
                "currency": "Fibd",
                "description": "PR-1335 multi-channel same-hash MPP",
                "payment_hash": payment_hash,
                "hash_algorithm": "ckb_hash",
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

        # Sanity: both upstream channels carry one outbound TLC each.
        deadline = time.time() + 30
        while time.time() < deadline:
            chans = {
                ch["channel_id"]: ch
                for ch in self._channels_by_peer(self.fiber1, self.fiber2)
            }
            if (
                self._pending_hashes(chans[force_closed_channel]).count(payment_hash)
                >= 1
                and self._pending_hashes(chans[offchain_channel]).count(payment_hash)
                >= 1
            ):
                break
            time.sleep(1)
        else:
            assert False, (
                "expected one outbound TLC per upstream channel; got "
                f"{{cid: ch['pending_tlcs'] for cid, ch in chans.items()}}"
            )

        # --- Force-close one of the upstream channels --------------------
        self.fiber1.get_client().shutdown_channel(
            {"channel_id": force_closed_channel, "force": True}
        )
        force_tx = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, force_tx)
        self.node.getClient().generate_epochs("0x1", wait_time=0)

        # Wait until the closed channel reports Closed and the other one
        # stays ChannelReady.
        for _ in range(30):
            chans = {
                ch["channel_id"]: ch
                for ch in self._channels_by_peer(self.fiber1, self.fiber2)
            }
            state_closed = chans[force_closed_channel]["state"]["state_name"]
            state_alive = chans[offchain_channel]["state"]["state_name"]
            if state_closed == "Closed" and state_alive == "ChannelReady":
                break
            time.sleep(1)
        else:
            assert (
                False
            ), f"unexpected states: closed={state_closed}, alive={state_alive}"

        # --- Settle invoice on receiver ----------------------------------
        fiber3.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )

        # Off-chain split clears on the surviving channel; on the
        # force-closed channel's mirror entry on fiber2 the TLC remains
        # pending (PR-1335 keeps the preimage so it can be used on-chain).
        deadline = time.time() + 60
        while time.time() < deadline:
            chans_fiber2 = {
                ch["channel_id"]: ch
                for ch in self._channels_by_peer(self.fiber2, self.fiber1)
            }
            offchain_cleared = (
                self._pending_hashes(chans_fiber2[offchain_channel]).count(payment_hash)
                == 0
            )
            onchain_still_pending = (
                self._pending_hashes(chans_fiber2[force_closed_channel]).count(
                    payment_hash
                )
                >= 1
            )
            if offchain_cleared and onchain_still_pending:
                break
            time.sleep(1)
        else:
            assert False, (
                "after settle: expected off-chain split cleared and "
                "force-closed split still pending on fiber2; got "
                f"{ {cid: ch['pending_tlcs'] for cid, ch in chans_fiber2.items()} }"
            )

        self.wait_invoice_state(fiber3, payment_hash, "Paid", timeout=60)

        # Record the on-chain split amount for the capacity-diff check.
        onchain_tlc = next(
            tlc
            for tlc in chans_fiber2[force_closed_channel]["pending_tlcs"]
            if tlc["payment_hash"] == payment_hash
        )
        split_amount = int(onchain_tlc["amount"], 16)

        # --- On-chain watchtower-settle verification ---------------------
        # `get_ln_tx_trace` only follows outputs[0] (the to-local
        # timelock cell). When the sender force-closes, the offered-HTLC
        # cells are *not* on outputs[0], so we scan every output of the
        # force-close commitment tx and check whether the counterparty's
        # watchtower has spent it on-chain. The PR-1335 fix is what keeps
        # fiber2's preimage alive long enough to do that.
        ckb_client = self.node.getClient()
        force_tx_outputs = ckb_client.get_transaction(force_tx)["transaction"][
            "outputs"
        ]

        def _find_settle_consumer():
            for idx, out in enumerate(force_tx_outputs):
                txs = ckb_client.get_transactions(
                    {
                        "script": out["lock"],
                        "script_type": "lock",
                        "script_search_mode": "exact",
                    },
                    "asc",
                    "0xff",
                    None,
                )["objects"]
                # The lock should appear once as output (in force_tx)
                # and a second time as input in the watchtower settle tx.
                consumer = next(
                    (
                        t
                        for t in txs
                        if t["io_type"] == "input" and t["tx_hash"] != force_tx
                    ),
                    None,
                )
                if consumer is not None:
                    return idx, consumer["tx_hash"]
            return None, None

        # Mine plenty of blocks so the watchtower interval fires and the
        # preimage-unlock tx lands on-chain. HTLC outputs are not behind a
        # commit-lock timelock, so they can be spent immediately after the
        # force-close lands.
        deadline = time.time() + 300
        spent_idx = consumer_tx = None
        while time.time() < deadline:
            for _ in range(120):
                self.Miner.miner_with_version(self.node, "0x0")
            time.sleep(5)
            spent_idx, consumer_tx = _find_settle_consumer()
            if consumer_tx is not None:
                break
        assert consumer_tx is not None, (
            "no follow-up tx ever spent any output of the force-close "
            "commitment tx — fiber2's watchtower did not settle the "
            "on-chain HTLC using the retained preimage"
        )

        # Verify the consuming tx settles roughly the on-chain split amount
        # out of the spent HTLC cell (tolerate tx fee < 1 CKB). The
        # watchtower tx may also carry unrelated wallet inputs/outputs, so
        # compare the spent cell against the small leftover output created
        # from that same cell instead of netting the whole transaction.
        settle_msg = self.get_tx_message(consumer_tx)
        spent_capacity = int(force_tx_outputs[spent_idx]["capacity"], 16)
        leftover_outputs = [
            cell["capacity"]
            for cell in settle_msg["output_cells"]
            if cell["capacity"] < spent_capacity
        ]
        assert leftover_outputs, (
            "watchtower settle tx did not produce a leftover output from "
            f"the spent HTLC cell; outputs={settle_msg['output_cells']}"
        )
        settled_amount = spent_capacity - min(leftover_outputs)
        assert abs(settled_amount - split_amount) < 100000000, (
            f"watchtower settle tx settled {settled_amount} shannons, "
            f"expected ~{split_amount} (on-chain split amount)"
        )
