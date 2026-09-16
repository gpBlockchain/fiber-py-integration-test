"""Regression: stale pre-TLC commitment is settled on-chain.

Attacker withholds RevokeAndAck so the victim offered TLC stays
LocalAnnounced, then broadcasts the pre-TLC signed commitment (not the
latest commitment that contains the TLC).

FNN now settles that previous commitment on-chain. After the remote close
and the delay epoch the watchtower spends the commitment cell. The
test checks that fiber1 recovers on-chain funds, the outgoing TLC is
resolved, the payment fails before TLC expiry, and the channel clears
WAITING_ONCHAIN_SETTLEMENT without a local shutdown RPC or restart.
"""

import hashlib
import time

from framework.basic_p2p import P2pFiberTest

CKB = 100000000
PAYMENT_AMOUNT = 1 * CKB
FINAL_EXPIRY_DELTA = 24 * 60 * 60 * 1000


def sha256_hex(preimage_hex):
    raw = bytes.fromhex(preimage_hex.replace("0x", ""))
    return "0x" + hashlib.sha256(raw).digest().hex()


def _cn(value):
    if value is None:
        return None
    return int(value, 16) if isinstance(value, str) else int(value)


class TestStaleCommitmentRaaPendingTlcStuck(P2pFiberTest):
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 5}

    def _tlc(self, fiber, payment_hash):
        channel = self.channel_of(fiber, include_closed=True)
        for tlc in channel.get("pending_tlcs") or []:
            if tlc.get("payment_hash") == payment_hash:
                return tlc
        return None

    def _wait_tlc(self, fiber, payment_hash, expected=None, timeout=60):
        last = None
        deadline = time.time() + timeout
        while time.time() < deadline:
            last = self._tlc(fiber, payment_hash)
            if last is not None and (
                expected is None or last.get("status") == expected
            ):
                return last
            time.sleep(0.5)
        raise TimeoutError(
            f"{fiber.tmp_path} TLC {payment_hash} status {last} != {expected}"
        )

    def _mine_watchtower_rounds(self, rounds=4):
        interval = self.start_fiber_config["fiber_watchtower_check_interval_seconds"]
        deadline = time.time() + interval * rounds + 2
        while time.time() < deadline:
            pool = self.node.getClient().get_raw_tx_pool()
            pending = list(pool.get("pending") or [])
            if pending:
                self.Miner.miner_until_tx_committed(self.node, pending[0])
            else:
                self.Miner.miner_with_version(self.node, "0x0")
            time.sleep(1)

    def _chain_ckb(self, fiber):
        lock_script = self.get_account_script(fiber.account_private)
        return int(
            self.node.getClient().get_cells_capacity(
                {
                    "script": lock_script,
                    "script_type": "lock",
                    "script_search_mode": "exact",
                }
            )["capacity"],
            16,
        )

    def _submit_previous_commitment(self, pre_local_cn, pre_remote_cn):
        # Dev RPC keys stored txs by lock-args commitment version, which is
        # typically musig_cn or musig_cn-1. Prefer older numbers first so we
        # publish the pre-TLC commitment, not the F-0361 post-TLC one.
        errors = []
        tried = set()
        candidates = []
        for number in (pre_local_cn, pre_remote_cn):
            if number is None:
                continue
            candidates.append(max(number - 1, 0))
            candidates.append(number)
        for number in candidates:
            if number in tried:
                continue
            tried.add(number)
            try:
                submitted = self.attacker.get_client().call(
                    "submit_commitment_transaction",
                    [
                        {
                            "channel_id": self.channel_id,
                            "commitment_number": hex(number),
                        }
                    ],
                )
                return number, submitted
            except Exception as err:
                errors.append(f"{number}: {err}")
        raise AssertionError(
            "attacker could not submit the pre-TLC commitment: "
            f"pre_local_cn={pre_local_cn} pre_remote_cn={pre_remote_cn} "
            f"errors={errors}"
        )

    # TEST-MAP: SETTLE-01
    def test_stale_pre_tlc_commitment_closes_and_payment_fails(self):
        # 1. P2pFiberTest already opened a ChannelReady channel.
        ready = self.channel_of(self.victim)
        assert ready["state"]["state_name"] == "ChannelReady", ready

        # Snapshot victim's on-chain balance; settlement must bring the
        # channel funds back to it. We do not care about the attacker's side.
        victim_ckb_before = self._chain_ckb(self.victim)

        # 2. Record the pre-TLC commitment numbers only.
        pre_musig = self.peer.musig2(self.channel_id)
        pre_local_cn = _cn(pre_musig.get("local_commitment_number"))
        pre_remote_cn = _cn(pre_musig.get("remote_commitment_number"))

        # 3. Attacker receives AddTlc+CS but withholds RAA, so victim stays
        # waiting_ack / LocalAnnounced.
        self.peer.intercept(
            self.channel_id,
            drop_out=["RevokeAndAck", "CommitmentSigned", "RemoveTlc"],
            drop_raa=True,
        )

        # 4. Hold invoice: payment_hash is known, preimage is not registered.
        preimage = self.generate_random_preimage()
        payment_hash = sha256_hex(preimage)
        invoice = self.attacker.get_client().new_invoice(
            {
                "amount": hex(PAYMENT_AMOUNT),
                "currency": "Fibd",
                "description": "stale pre-TLC commitment settles previous tx",
                "payment_hash": payment_hash,
                "hash_algorithm": "sha256",
                "final_expiry_delta": hex(FINAL_EXPIRY_DELTA),
            }
        )
        payment = self.victim.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "max_fee_rate": hex(1000000000000000),
            }
        )
        assert payment["payment_hash"] == payment_hash
        self.wait_payment_state(self.victim, payment_hash, "Inflight", timeout=60)
        victim_tlc = self._wait_tlc(
            self.victim, payment_hash, {"Outbound": "LocalAnnounced"}
        )
        attacker_tlc = self._wait_tlc(self.attacker, payment_hash)
        assert "Inbound" in attacker_tlc.get("status", {}), attacker_tlc
        assert victim_tlc.get("payment_hash") == payment_hash

        # 5. Broadcast the pre-TLC commitment, not the latest TLC-containing one.
        previous_cn, submitted = self._submit_previous_commitment(
            pre_local_cn, pre_remote_cn
        )
        close_tx = submitted["tx_hash"]

        # 6. Confirm the close tx. The published tx is the pre-TLC commitment,
        # so there is no HTLC output; the TLC amount sits in the commitment lock.
        self.Miner.miner_until_tx_committed(self.node, close_tx)
        # 7. Once the delay epoch passes, fiber1 must recover on-chain funds.
        deadline = time.time() + 120
        victim_ckb_after = self._chain_ckb(self.fiber1)
        self.node.getClient().generate_epochs("0x1", wait_time=0)
        while time.time() < deadline and victim_ckb_after <= victim_ckb_before:
            self._mine_watchtower_rounds(1)
            victim_ckb_after = self._chain_ckb(self.fiber1)
        assert victim_ckb_after > victim_ckb_before, (
            f"fiber1 did not recover on-chain funds: "
            f"before={victim_ckb_before}, after={victim_ckb_after}"
        )

        # Remote close must finish automatically, before the excluded TLC expires.
        # Check only this payment's outgoing TLC; cleanup may remove it entirely.
        # Allow the 300s remote-close check plus 120s for TLC/payment processing.
        deadline = time.monotonic() + 420
        while True:
            after = self.channel_of(self.fiber1, include_closed=True)
            payment_after = self.fiber1.get_client().get_payment(
                {"payment_hash": payment_hash}
            )
            tlc_after = next(
                (
                    tlc
                    for tlc in after["pending_tlcs"]
                    if tlc["payment_hash"] == payment_hash
                ),
                None,
            )
            tlc_settled = tlc_after is None or tlc_after["status"] == {
                "Outbound": "RemoteRemoved"
            }
            closed = after["state"][
                "state_name"
            ] == "Closed" and "WAITING_ONCHAIN_SETTLEMENT" not in str(
                after["state"].get("state_flags")
            )
            if tlc_settled and payment_after["status"] == "Failed" and closed:
                break
            if time.monotonic() >= deadline:
                break
            self._mine_watchtower_rounds(1)
        assert tlc_settled, after
        assert payment_after["status"] == "Failed", payment_after

    def test_router_stale_pre_tlc_commitment_fails_upstream_without_shutdown(self):
        # Payer -- Router(victim) -- Attacker; reuse the ready downstream channel.
        router = self.victim
        payer = self.start_new_fiber(self.generate_account(10000))
        upstream_id = self.open_channel(payer, router, 200 * CKB, 200 * CKB)
        self.wait_graph_channels_sync(payer, 2, timeout=90)
        upstream_before = next(
            channel
            for channel in payer.get_client().list_channels({})["channels"]
            if channel["channel_id"] == upstream_id
        )
        assert upstream_before["state"]["state_name"] == "ChannelReady"
        funding_outpoint = bytes.fromhex(upstream_before["channel_outpoint"][2:])
        funding_hash = "0x" + funding_outpoint[:32].hex()
        funding_index = hex(int.from_bytes(funding_outpoint[32:], "little"))
        assert (
            self.node.getClient().get_live_cell(funding_index, funding_hash)["status"]
            == "live"
        )

        pre_musig = self.peer.musig2(self.channel_id)
        self.peer.intercept(
            self.channel_id,
            drop_out=["RevokeAndAck", "CommitmentSigned", "RemoveTlc"],
            drop_raa=True,
        )
        payment_hash = sha256_hex(self.generate_random_preimage())
        invoice = self.attacker.get_client().new_invoice(
            {
                "amount": hex(PAYMENT_AMOUNT),
                "currency": "Fibd",
                "description": "router stale commitment fails upstream without shutdown",
                "payment_hash": payment_hash,
                "hash_algorithm": "sha256",
                "final_expiry_delta": hex(FINAL_EXPIRY_DELTA),
            }
        )
        payment = payer.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "max_fee_rate": hex(1000000000000000),
            }
        )
        assert payment["payment_hash"] == payment_hash
        self.wait_payment_state(payer, payment_hash, "Inflight", timeout=60)
        router_out = self._wait_tlc(
            router, payment_hash, {"Outbound": "LocalAnnounced"}
        )
        self._wait_tlc(self.attacker, payment_hash)

        # Prove the upstream TLC exists on BOTH peers before publishing the close.
        upstream_expiries = []
        for fiber, direction in ((payer, "Outbound"), (router, "Inbound")):
            channel = next(
                channel
                for channel in fiber.get_client().list_channels({})["channels"]
                if channel["channel_id"] == upstream_id
            )
            tlc = next(
                tlc
                for tlc in channel["pending_tlcs"]
                if tlc["payment_hash"] == payment_hash
            )
            assert direction in tlc["status"], channel
            assert channel["state"]["state_name"] == "ChannelReady", channel
            upstream_expiries.append(_cn(tlc["expiry"]))

        previous_cn, submitted = self._submit_previous_commitment(
            _cn(pre_musig.get("local_commitment_number")),
            _cn(pre_musig.get("remote_commitment_number")),
        )
        close_tx = submitted["tx_hash"]
        self.Miner.miner_until_tx_committed(self.node, close_tx)
        self.node.getClient().generate_epochs("0x1", wait_time=0)

        # Allow the 300s remote-close scan plus settlement/relay processing.
        # Check upstream throughout: a transient ShuttingDown is also a failure.
        deadline = time.monotonic() + 420
        while True:
            downstream = self.channel_of(router, include_closed=True)
            payment_after = payer.get_client().get_payment(
                {"payment_hash": payment_hash}
            )
            outgoing = [
                tlc
                for tlc in downstream["pending_tlcs"]
                if tlc["payment_hash"] == payment_hash
            ]
            outgoing_removed = all(
                tlc["status"]
                in (
                    {"Outbound": "RemoteRemoved"},
                    {"Outbound": "RemoveAckConfirmed"},
                )
                for tlc in outgoing
            )
            remote_closed = downstream["state"]["state_name"] == "Closed"
            upstream_channels = []
            for fiber in (payer, router):
                channel = next(
                    channel
                    for channel in fiber.get_client().list_channels(
                        {"include_closed": True}
                    )["channels"]
                    if channel["channel_id"] == upstream_id
                )
                assert channel["state"]["state_name"] == "ChannelReady", (
                    f"upstream shutdown on {fiber.tmp_path}: {channel}; "
                    f"downstream={downstream}; payment={payment_after}"
                )
                assert channel.get("shutdown_transaction_hash") is None, channel
                upstream_channels.append(channel)
            # list_channels exposes all stored TLCs, including completed ones.
            # RemoveAckConfirmed means resolved; physical removal can happen later.
            upstream_resolved = all(
                all(
                    tlc["status"] == {direction: "RemoveAckConfirmed"}
                    for tlc in channel["pending_tlcs"]
                    if tlc["payment_hash"] == payment_hash
                )
                for channel, direction in zip(
                    upstream_channels, ("Outbound", "Inbound")
                )
            )
            if (
                # remote_closed
                outgoing_removed
                and upstream_resolved
                and (payment_after["status"] == "Failed")
            ):
                break
            assert time.monotonic() < deadline, (
                f"upstream failure propagation incomplete: "
                f"remote_closed={remote_closed}, outgoing_removed={outgoing_removed}, "
                f"upstream_resolved={upstream_resolved}; downstream={downstream}; "
                f"payment={payment_after}; upstream={upstream_channels}"
            )
            self._mine_watchtower_rounds(1)

        # Failure must come from settlement, not the normal TLC-expiry path.
        earliest_expiry = min(_cn(router_out["expiry"]), *upstream_expiries)
        assert int(time.time() * 1000) < earliest_expiry, router_out
