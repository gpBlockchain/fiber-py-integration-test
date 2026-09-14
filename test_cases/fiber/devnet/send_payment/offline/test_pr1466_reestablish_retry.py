"""Regression coverage for fiber PR #1466.

Payments with pending TLCs must still finish after rapid reconnect/reestablish.
"""

import hashlib
import time

from framework.basic_fiber import FiberTest


def _sha256_hex(preimage):
    return "0x" + hashlib.sha256(bytes.fromhex(preimage.removeprefix("0x"))).hexdigest()


class TestPR1466ReestablishRetry(FiberTest):
    def _wait_for_pending_tlcs(self):
        for _ in range(30):
            channels = self.fiber1.get_client().list_channels(
                {"pubkey": self.fiber2.get_pubkey()}
            )["channels"]
            assert channels, "no channel with fiber2"
            if channels[0].get("pending_tlcs", []):
                return
            time.sleep(1)
        assert False, "payment batch never created pending TLCs"

    def _disconnect_and_reconnect(self):
        self.fiber1.get_client().disconnect_peer({"pubkey": self.fiber2.get_pubkey()})
        time.sleep(1)
        self.fiber1.connect_peer(self.fiber2)
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_pubkey(), "ChannelReady", 120
        )
        self.wait_for_channel_state(
            self.fiber2.get_client(), self.fiber1.get_pubkey(), "ChannelReady", 120
        )

    def test_pending_payments_finish_after_rapid_reconnects(self):
        self.open_channel(self.fiber1, self.fiber2, 500 * 100000000, 100 * 100000000)

        payment_hashes = []
        for _ in range(5):
            payment_hashes.append(
                self.send_payment(self.fiber1, self.fiber2, 1 * 100000000, False)
            )

        self._wait_for_pending_tlcs()
        self._disconnect_and_reconnect()
        self._disconnect_and_reconnect()

        for payment_hash in payment_hashes:
            self.wait_payment_state(self.fiber1, payment_hash, "Success", 120)

        channels = self.fiber1.get_client().list_channels(
            {"pubkey": self.fiber2.get_pubkey()}
        )["channels"]
        assert channels[0].get("pending_tlcs", []) == []

    def test_pending_hold_payments_settle_after_rapid_reconnects(self):
        """Reproduce the receiver RemoveTlc queue deadlock seen in CI."""
        self.open_channel(self.fiber1, self.fiber2, 500 * 100000000, 100 * 100000000)

        payments = []
        for index in range(5):
            preimage = self.generate_random_preimage()
            payment_hash = _sha256_hex(preimage)
            invoice = self.fiber2.get_client().new_invoice(
                {
                    "amount": hex(1 * 100000000),
                    "currency": "Fibd",
                    "description": f"reestablish pending hold payment {index}",
                    "expiry": hex(3600),
                    "final_cltv": hex(40),
                    "payment_hash": payment_hash,
                    "hash_algorithm": "sha256",
                }
            )
            payment = self.fiber1.get_client().send_payment(
                {"invoice": invoice["invoice_address"]}
            )
            assert payment["payment_hash"] == payment_hash
            payments.append((payment_hash, preimage))

        # Hold invoices keep all five TLCs pending until the receiver settles them.
        for payment_hash, _ in payments:
            self.wait_invoice_state(self.fiber2, payment_hash, "Received", 120, 1)
            self.wait_payment_state(self.fiber1, payment_hash, "Inflight", 30)

        self._wait_for_pending_tlcs()

        for payment_hash, preimage in payments:
            self.fiber2.get_client().settle_invoice(
                {"payment_hash": payment_hash, "payment_preimage": preimage}
            )

        # Interrupt the receiver while its batch of RemoveTlc operations is in flight.
        self._disconnect_and_reconnect()
        self._disconnect_and_reconnect()

        for payment_hash, _ in payments:
            self.wait_payment_state(self.fiber1, payment_hash, "Success", 30)
            self.wait_invoice_state(self.fiber2, payment_hash, "Paid", 30, 1)

        for fiber, peer_pubkey in (
            (self.fiber1, self.fiber2.get_pubkey()),
            (self.fiber2, self.fiber1.get_pubkey()),
        ):
            channels = fiber.get_client().list_channels({"pubkey": peer_pubkey})[
                "channels"
            ]
            assert channels, f"no channel with peer {peer_pubkey}"
            assert channels[0].get("pending_tlcs", []) == []
