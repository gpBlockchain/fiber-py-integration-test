"""F-0361 regression: LocalAnnounced offered TLCs reconcile on-chain.

PR #1630
--------
An offered TLC that is still ``OutboundTlcStatus::LocalAnnounced`` is already
inside the signed remote commitment. A peer can withhold RevokeAndAck,
broadcast that commitment, and spend the TLC on-chain.

On-chain collectors now accept offered ``LocalAnnounced | Committed``, so the
origin payer learns the fulfill: the TLC is marked RemoteRemoved and the
payment session becomes Success with the on-chain preimage.

Harness
-------
Stock victim is the origin payer. Debug attacker receives AddTlc + CS,
drops outbound RAA / CS / RemoveTlc so the victim stays LocalAnnounced,
then submits the signed commitment and lets watchtower claim with P.
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


class TestF0361LocalAnnouncedOnchainReconcile(P2pFiberTest):
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
            if last is None:
                time.sleep(0.5)
                continue
            if expected is None or last.get("status") == expected:
                return last
            time.sleep(0.5)
        raise TimeoutError(
            f"{fiber.tmp_path} TLC {payment_hash} status {last} != {expected}"
        )

    def _submit_signed_commitment(self):
        musig = self.peer.musig2(self.channel_id)
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
                return self.attacker.get_client().call(
                    "submit_commitment_transaction",
                    [
                        {
                            "channel_id": self.channel_id,
                            "commitment_number": hex(number),
                        }
                    ],
                )
            except Exception as err:
                errors.append(f"{number}: {err}")
        raise AssertionError(
            "attacker could not submit the signed remote commitment: "
            f"musig={musig} errors={errors}"
        )

    def _commit_signed_commitment(self, timeout=60):
        deadline = time.time() + timeout
        last_error = None
        while time.time() < deadline:
            try:
                submitted = self._submit_signed_commitment()
                self.Miner.miner_until_tx_committed(self.node, submitted["tx_hash"])
                return submitted
            except Exception as error:
                last_error = error
                if "Expiry" not in str(error):
                    raise
                time.sleep(1)
        raise TimeoutError(
            f"signed commitment did not mature within {timeout}s: {last_error}"
        )

    def _wait_payment_success_with_mining(self, fiber, payment_hash, timeout=180):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = fiber.get_client().get_payment({"payment_hash": payment_hash})
            if last["status"] == "Success":
                return last
            if last["status"] == "Failed":
                raise AssertionError(f"payment failed during on-chain settle: {last}")
            # Watchtower checks asynchronously. Keep producing blocks until its
            # preimage claim and the payer reconciliation are both committed.
            self.Miner.miner_with_version(self.node, "0x0")
            time.sleep(1)
        raise TimeoutError(
            f"payment {payment_hash} did not settle on-chain within {timeout}s: {last}"
        )

    def _wait_outbound_remote_removed(self, fiber, payment_hash, timeout=90):
        last = None
        deadline = time.time() + timeout
        while time.time() < deadline:
            last = self._tlc(fiber, payment_hash)
            if last is None:
                return None
            if last.get("status") == {"Outbound": "RemoteRemoved"}:
                return last
            time.sleep(0.5)
        raise TimeoutError(
            f"{fiber.tmp_path} offered TLC {payment_hash} did not "
            f"reconcile to RemoteRemoved: {last}"
        )

    def test_localannounced_offered_tlc_reconciles_after_onchain_settle(self):
        """On-chain P settle closes the LocalAnnounced origin payment."""
        preimage = self.generate_random_preimage()
        payment_hash = sha256_hex(preimage)
        invoice = self.attacker.get_client().new_invoice(
            {
                "amount": hex(PAYMENT_AMOUNT),
                "currency": "Fibd",
                "description": "F-0361 LocalAnnounced on-chain reconcile",
                "payment_hash": payment_hash,
                "hash_algorithm": "sha256",
                "final_expiry_delta": hex(FINAL_EXPIRY_DELTA),
            }
        )

        # Attacker already has the signed remote commitment after AddTlc+CS.
        # Withhold the ACK that would move the victim to Committed.
        self.peer.intercept(
            self.channel_id,
            drop_out=["RevokeAndAck", "CommitmentSigned", "RemoveTlc"],
            drop_raa=True,
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

        # Honest preimage is known to the attacker; stock create_preimage accepts it.
        self.attacker.get_client().call(
            "create_preimage",
            [{"payment_hash": payment_hash, "preimage": preimage}],
        )

        self._commit_signed_commitment()
        self.node.getClient().generate_epochs("0x1", wait_time=0)

        # PR #1630: LocalAnnounced offered TLCs are in the remote commitment,
        # so on-chain P must finish the origin payment.
        payment_after = self._wait_payment_success_with_mining(
            self.victim, payment_hash
        )
        assert payment_after.get("payment_preimage") == preimage, payment_after

        victim_tlc = self._wait_outbound_remote_removed(self.victim, payment_hash)
        if victim_tlc is not None:
            assert victim_tlc["status"] == {"Outbound": "RemoteRemoved"}, victim_tlc
