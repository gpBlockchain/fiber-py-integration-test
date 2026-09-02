"""F-0361 + CCH regression: LocalAnnounced on-chain fulfill settles LND.

receive_btc is LND in, Fiber out. CCH only calls LND ``settleinvoice`` after
the Fiber payment reports ``Success`` with a preimage:

    Inflight          -> OutgoingInFlight  -> TrackOutgoingPayment
    Success+preimage  -> OutgoingSuccess   -> SettleIncomingInvoice

PR #1630 includes offered LocalAnnounced TLCs in on-chain fulfill
collection, so the CCH origin payment becomes Success after the attacker
claims P. CCH then settleinvoice; the LND hold invoice must become SETTLED.

Harness
-------
fiber1 is stock CURRENT_CCH (hub, PR #1630 fnn). The Fiber payee is
attack/fnn so we can drop RAA/CS/RemoveTlc, submit the signed commitment,
and let watchtower claim P.
"""

import hashlib
import time

from framework.basic_fiber_with_cch import FiberCchTest
from framework.attack_fnn import requires_attack_fnn
from framework.p2p_peer import P2pPeer
from framework.test_fiber import FiberConfigPath

PAYMENT_AMOUNT_SATS = 100000
CHANNEL_FUND = 1000 * 100000000
UDT_FAUCET = 10000 * 100000000
# Attack fnn defaults to a tiny final expiry that CCH rejects.
# 9600000 ms is Fiber's minimum and stays under the CCH LND CLTV budget.
FINAL_EXPIRY_DELTA_MS = 9600000


def sha256_hex(preimage_hex):
    raw = bytes.fromhex(preimage_hex.replace("0x", ""))
    return "0x" + hashlib.sha256(raw).digest().hex()


@requires_attack_fnn
class TestF0361CchReceiveBtcInflight(FiberCchTest):
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 5}

    def _udt_script(self):
        return self.get_account_udt_script(self.fiber1.account_private)

    def _channel_of(self, fiber):
        channels = fiber.get_client().list_channels({"include_closed": True})[
            "channels"
        ]
        for channel in channels:
            if channel["channel_id"] == self.channel_id:
                return channel
        raise AssertionError(
            f"channel {self.channel_id} not found on {fiber.get_client().url}"
        )

    def _tlc(self, fiber, payment_hash):
        for tlc in self._channel_of(fiber).get("pending_tlcs") or []:
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
            self.Miner.miner_with_version(self.node, "0x0")
            time.sleep(1)
        raise TimeoutError(
            f"payment {payment_hash} did not settle on-chain within {timeout}s: {last}"
        )

    def _lookup_lnd_invoice(self, payment_hash):
        return self.LNDs[0].ln_cli_with_cmd(
            f"lookupinvoice {payment_hash.removeprefix('0x')}"
        )

    def _lnd_payments(self, payment_hash):
        target = payment_hash.removeprefix("0x").lower()
        payments = (
            self.LNDs[1]
            .ln_cli_with_cmd("listpayments --include_incomplete")
            .get("payments", [])
        )
        return [
            payment
            for payment in payments
            if payment.get("payment_hash", "").lower() == target
        ]

    def _start_attack_payee(self):
        self.attacker = self.start_new_fiber(
            self.generate_account(
                10000,
                self.fiber1.account_private,
                UDT_FAUCET,
            ),
            fiber_version=FiberConfigPath.ATTACK_DEV,
        )
        self.peer = P2pPeer(self.attacker)
        self.fiber1.connect_peer(self.attacker)
        time.sleep(1)
        self.channel_id = self.open_channel(
            self.attacker,
            self.fiber1,
            CHANNEL_FUND,
            CHANNEL_FUND,
            udt=self._udt_script(),
        )
        return self.channel_id

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

    def test_receive_btc_localannounced_onchain_settle_settles_lnd(self):
        """On-chain P settle of a LocalAnnounced CCH payment must settleinvoice."""
        self._start_attack_payee()
        preimage = self.generate_random_preimage()
        payment_hash = sha256_hex(preimage)
        invoice = self.attacker.get_client().new_invoice(
            {
                "amount": hex(PAYMENT_AMOUNT_SATS),
                "currency": "Fibd",
                "description": "F-0361 CCH receive_btc LocalAnnounced on-chain settle",
                "payment_hash": payment_hash,
                "hash_algorithm": "sha256",
                "final_expiry_delta": hex(FINAL_EXPIRY_DELTA_MS),
                "udt_type_script": self._udt_script(),
            }
        )

        order = self.fiber1.get_client().receive_btc(
            {"fiber_pay_req": invoice["invoice_address"]}
        )
        assert order["payment_hash"] == payment_hash

        # Arm before LND pays: CCH sends Fiber outgoing as soon as the hold
        # invoice is accepted. Withhold the ACK that would Commit the TLC.
        self.peer.intercept(
            self.channel_id,
            drop_out=["RevokeAndAck", "CommitmentSigned", "RemoveTlc"],
            drop_raa=True,
        )

        self.LNDs[1].ln_cli_with_cmd_without_json(
            f"payinvoice {order['incoming_invoice']['Lightning']} --force &"
        )
        self.wait_cch_order_state(
            self.fiber1, payment_hash, "OutgoingInFlight", timeout=120
        )
        self.wait_payment_state(self.fiber1, payment_hash, "Inflight", timeout=60)
        cch_tlc = self._wait_tlc(
            self.fiber1, payment_hash, {"Outbound": "LocalAnnounced"}
        )
        attacker_tlc = self._wait_tlc(self.attacker, payment_hash)
        assert "Inbound" in attacker_tlc.get("status", {}), attacker_tlc
        assert cch_tlc.get("payment_hash") == payment_hash

        lnd_invoice = self._lookup_lnd_invoice(payment_hash)
        assert lnd_invoice["state"] == "ACCEPTED", lnd_invoice

        self.attacker.get_client().call(
            "create_preimage",
            [{"payment_hash": payment_hash, "preimage": preimage}],
        )
        self._commit_signed_commitment()
        self.node.getClient().generate_epochs("0x1", wait_time=0)

        # PR #1630: LocalAnnounced offered TLC reconciles as fulfilled, so
        # CCH sees Success+preimage and settleinvoice.
        payment_after = self._wait_payment_success_with_mining(
            self.fiber1, payment_hash
        )
        assert payment_after.get("payment_preimage") == preimage, payment_after

        cch_tlc = self._wait_outbound_remote_removed(self.fiber1, payment_hash)
        if cch_tlc is not None:
            assert cch_tlc["status"] == {"Outbound": "RemoteRemoved"}, cch_tlc

        self.wait_cch_order_state(self.fiber1, payment_hash, "Success", timeout=180)

        lnd_invoice = self._lookup_lnd_invoice(payment_hash)
        assert lnd_invoice["state"] == "SETTLED", lnd_invoice

        lnd_payments = self._lnd_payments(payment_hash)
        assert lnd_payments, f"external LND payment missing for {payment_hash}"
        assert lnd_payments[0]["status"] == "SUCCEEDED", lnd_payments[0]
