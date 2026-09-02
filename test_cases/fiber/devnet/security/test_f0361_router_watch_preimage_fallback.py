"""F-0361 regression: a live router relays LocalAnnounced on-chain fulfill.

Alice (stock) -- Router (stock) -- Bob (debug attacker)

Bob withholds the ACK so the router's outgoing TLC stays LocalAnnounced,
then broadcasts that commitment and claims P on-chain. PR #1630 includes
offered LocalAnnounced TLCs in on-chain fulfill collection, so the router
relays RemoveTlc(Fulfill) upstream. Alice must reach Success and the
router must not be left holding the incoming hop.
"""

import hashlib
import time

from framework.basic_p2p import P2pFiberTest
from framework.p2p_peer import P2pPeer
from framework.test_fiber import FiberConfigPath

CKB = 100000000
PAYMENT_AMOUNT = 1 * CKB
CHANNEL_FUND = 200 * CKB
FINAL_EXPIRY_DELTA = 24 * 60 * 60 * 1000


def sha256_hex(preimage_hex):
    raw = bytes.fromhex(preimage_hex.replace("0x", ""))
    return "0x" + hashlib.sha256(raw).digest().hex()


class TestF0361RouterWatchPreimageFallback(P2pFiberTest):
    auto_open_channel = False
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 5}

    def _open_alice_router_bob(self):
        self.alice = self.fiber1
        # P2pFiberTest releases fiber2 before it starts the debug attacker.
        # Start a live stock node explicitly for the middle hop instead of
        # reusing that stopped Fiber object.
        self.router = self.start_new_fiber(
            self.generate_account(10000),
            fiber_version=FiberConfigPath.CURRENT_DEV,
        )
        self.bob = self.attacker
        self.peer = P2pPeer(self.bob)
        self.bob.connect_peer(self.router)
        time.sleep(1)
        self.ch_alice = self.open_channel(
            self.alice, self.router, CHANNEL_FUND, CHANNEL_FUND
        )
        self.ch_bob = self.open_channel(
            self.router, self.bob, CHANNEL_FUND, CHANNEL_FUND
        )
        time.sleep(3)
        self.wait_graph_channels_sync(self.alice, 2, timeout=90)
        self.wait_graph_channels_sync(self.router, 2, timeout=90)
        return self.ch_alice, self.ch_bob

    def _channel(self, fiber, channel_id):
        channels = fiber.get_client().list_channels({"include_closed": True})[
            "channels"
        ]
        for channel in channels:
            if channel["channel_id"] == channel_id:
                return channel
        raise AssertionError(f"channel {channel_id} not found on {fiber.tmp_path}")

    def _tlc(self, fiber, channel_id, payment_hash):
        channel = self._channel(fiber, channel_id)
        for tlc in channel.get("pending_tlcs") or []:
            if tlc.get("payment_hash") == payment_hash:
                return tlc
        return None

    def _wait_tlc(self, fiber, channel_id, payment_hash, expected=None, timeout=60):
        last = None
        deadline = time.time() + timeout
        while time.time() < deadline:
            last = self._tlc(fiber, channel_id, payment_hash)
            if last is None:
                time.sleep(0.5)
                continue
            if expected is None or last.get("status") == expected:
                return last
            time.sleep(0.5)
        raise TimeoutError(
            f"{fiber.tmp_path} {channel_id} TLC {payment_hash} "
            f"status {last} != {expected}"
        )

    def _submit_signed_commitment(self, channel_id):
        musig = self.peer.musig2(channel_id)
        numbers = []
        for key in ("local_commitment_number", "remote_commitment_number"):
            raw = musig.get(key)
            if raw is None:
                continue
            number = int(raw, 16) if isinstance(raw, str) else int(raw)
            numbers.extend([number, max(number - 1, 0), number + 1])
        errors = []
        tried = set()
        for number in numbers:
            if number in tried:
                continue
            tried.add(number)
            try:
                return self.bob.get_client().call(
                    "submit_commitment_transaction",
                    [
                        {
                            "channel_id": channel_id,
                            "commitment_number": hex(number),
                        }
                    ],
                )
            except Exception as err:
                errors.append(f"{number}: {err}")
        raise AssertionError(
            "bob could not submit the signed remote commitment: "
            f"musig={musig} errors={errors}"
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

    def test_live_router_recovers_upstream_after_localannounced_claim(self):
        """Bob claims the LocalAnnounced hop; on-chain reconcile fulfills Alice."""
        ch_alice, ch_bob = self._open_alice_router_bob()
        preimage = self.generate_random_preimage()
        payment_hash = sha256_hex(preimage)
        invoice = self.bob.get_client().new_invoice(
            {
                "amount": hex(PAYMENT_AMOUNT),
                "currency": "Fibd",
                "description": "F-0361 live router LocalAnnounced on-chain fulfill",
                "payment_hash": payment_hash,
                "hash_algorithm": "sha256",
                "final_expiry_delta": hex(FINAL_EXPIRY_DELTA),
            }
        )

        self.peer.intercept(
            ch_bob,
            drop_out=["RevokeAndAck", "CommitmentSigned", "RemoveTlc"],
            drop_raa=True,
        )

        payment = self.alice.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "max_fee_rate": hex(1000000000000000),
            }
        )
        assert payment["payment_hash"] == payment_hash
        self.wait_payment_state(self.alice, payment_hash, "Inflight", timeout=60)
        self._wait_tlc(
            self.router, ch_bob, payment_hash, {"Outbound": "LocalAnnounced"}
        )
        router_in = self._wait_tlc(self.router, ch_alice, payment_hash)
        assert "Inbound" in router_in.get("status", {}), router_in

        self.bob.get_client().call(
            "create_preimage",
            [{"payment_hash": payment_hash, "preimage": preimage}],
        )
        submitted = self._submit_signed_commitment(ch_bob)
        self.Miner.miner_until_tx_committed(self.node, submitted["tx_hash"])
        self.node.getClient().generate_epochs("0x1", wait_time=0)
        self._mine_watchtower_rounds(4)
        self.wait_invoice_state(self.bob, payment_hash, "Paid", timeout=90)

        # PR #1630: LocalAnnounced offered TLC is collected as fulfilled and
        # relayed upstream, so Alice is paid from the on-chain preimage.
        self.wait_payment_state(self.alice, payment_hash, "Success", timeout=120)
        payment_after = self.alice.get_client().get_payment(
            {"payment_hash": payment_hash}
        )
        assert payment_after.get("payment_preimage") == preimage, payment_after

        # The force-closed channel may retain its last LocalAnnounced TLC
        # snapshot while waiting for commitment confirmation. The observable
        # reconciliation contract is that the channel is closing on-chain and
        # the preimage has already been relayed to the upstream hop.
        router_out_channel = self._channel(self.router, ch_bob)
        assert router_out_channel["state"]["state_name"] in (
            "ShuttingDown",
            "Closed",
        ), router_out_channel
        router_out = self._tlc(self.router, ch_bob, payment_hash)
        if router_out is not None:
            assert router_out["status"] in (
                {"Outbound": "LocalAnnounced"},
                {"Outbound": "RemoteRemoved"},
            ), router_out

        router_in_after = self._tlc(self.router, ch_alice, payment_hash)
        if router_in_after is not None:
            status = router_in_after.get("status") or {}
            inbound = status.get("Inbound")
            assert inbound in (
                "LocalRemoved",
                "RemoveWaitAck",
                "RemoveAckConfirmed",
            ), router_in_after
