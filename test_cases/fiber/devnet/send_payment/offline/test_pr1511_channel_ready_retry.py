import hashlib
import time

from framework.basic_fiber import FiberTest


ONE_CKB = 100000000


def _sha256_hex(preimage_hex):
    raw = bytes.fromhex(preimage_hex.replace("0x", ""))
    return "0x" + hashlib.sha256(raw).hexdigest()


def _pending_tlcs(fiber, peer_pubkey):
    channels = fiber.get_client().list_channels({"pubkey": peer_pubkey})["channels"]
    assert channels, f"no channel with peer {peer_pubkey}"
    return channels[0].get("pending_tlcs", [])


class TestPR1511ChannelReadyRetry(FiberTest):
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

    def test_channel_ready_scan_does_not_duplicate_channel_owned_created_attempt(self):
        self.open_channel(self.fiber1, self.fiber2, 500 * ONE_CKB, 100 * ONE_CKB)

        preimage = self.generate_random_preimage()
        payment_hash = _sha256_hex(preimage)
        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(1 * ONE_CKB),
                "currency": "Fibd",
                "description": "PR-1511 channel-owned Created attempt",
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

        before = _pending_tlcs(self.fiber1, self.fiber2.get_pubkey())
        assert len(before) == 1
        assert before[0]["payment_hash"] == payment_hash

        self._disconnect_and_reconnect()

        after = _pending_tlcs(self.fiber1, self.fiber2.get_pubkey())
        assert len(after) == 1
        assert after[0]["payment_hash"] == payment_hash

        self.fiber2.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )
        self.wait_payment_state(self.fiber1, payment["payment_hash"], "Success", 120)
        self.wait_invoice_state(self.fiber2, payment_hash, "Paid", 120, 1)
        assert _pending_tlcs(self.fiber1, self.fiber2.get_pubkey()) == []
