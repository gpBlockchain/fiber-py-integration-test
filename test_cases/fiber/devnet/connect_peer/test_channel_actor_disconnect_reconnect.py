"""Regression tests for fiber PR #1217.

PR #1217 keeps channel actors alive across peer disconnects and lets the
channel actor drive reestablish on reconnect. The observable contract from
Python is simple: a ready channel must stay usable after disconnect/reconnect,
including when there is a pending hold TLC during the offline window.
"""

import hashlib
import time

from framework.basic_fiber import FiberTest


def _sha256_hex(preimage_hex):
    raw = bytes.fromhex(preimage_hex.replace("0x", ""))
    return "0x" + hashlib.sha256(raw).hexdigest()


def _pending_tlc_count(fiber, peer_pubkey):
    channels = fiber.get_client().list_channels({"pubkey": peer_pubkey})["channels"]
    assert channels, f"no channel with peer {peer_pubkey}"
    return len(channels[0].get("pending_tlcs", []))


class TestChannelActorDisconnectReconnect(FiberTest):
    def _disconnect_and_reconnect(self):
        self.fiber1.get_client().disconnect_peer({"pubkey": self.fiber2.get_pubkey()})
        time.sleep(2)
        self.fiber1.connect_peer(self.fiber2)
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_pubkey(), "ChannelReady", 120
        )
        self.wait_for_channel_state(
            self.fiber2.get_client(), self.fiber1.get_pubkey(), "ChannelReady", 120
        )

    def test_channel_remains_usable_after_disconnect_reconnect(self):
        self.open_channel(self.fiber1, self.fiber2, 500 * 100000000, 100 * 100000000)

        before = self.send_payment(self.fiber1, self.fiber2, 1 * 100000000)
        self.wait_payment_state(self.fiber1, before, "Success")

        self._disconnect_and_reconnect()

        after = self.send_payment(self.fiber1, self.fiber2, 1 * 100000000)
        self.wait_payment_state(self.fiber1, after, "Success")
        assert _pending_tlc_count(self.fiber1, self.fiber2.get_pubkey()) == 0
        assert _pending_tlc_count(self.fiber2, self.fiber1.get_pubkey()) == 0

    def test_pending_hold_tlc_settles_after_disconnect_reconnect(self):
        self.open_channel(self.fiber1, self.fiber2, 500 * 100000000, 100 * 100000000)

        preimage = self.generate_random_preimage()
        payment_hash = _sha256_hex(preimage)
        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(1 * 100000000),
                "currency": "Fibd",
                "description": "PR-1217 hold TLC across reconnect",
                "expiry": "0xe10",
                "final_cltv": "0x28",
                "payment_hash": payment_hash,
                "hash_algorithm": "sha256",
            }
        )
        payment = self.fiber1.get_client().send_payment(
            {"invoice": invoice["invoice_address"]}
        )
        self.wait_invoice_state(self.fiber2, payment_hash, "Received", 120, 1)

        assert _pending_tlc_count(self.fiber1, self.fiber2.get_pubkey()) > 0
        self._disconnect_and_reconnect()

        self.fiber2.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )
        self.wait_payment_state(self.fiber1, payment["payment_hash"], "Success", 120)
        self.wait_invoice_state(self.fiber2, payment_hash, "Paid", 120, 1)
        assert _pending_tlc_count(self.fiber1, self.fiber2.get_pubkey()) == 0
        assert _pending_tlc_count(self.fiber2, self.fiber1.get_pubkey()) == 0
