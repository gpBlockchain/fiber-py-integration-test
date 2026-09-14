"""Regression tests for fiber PR #1217.

PR #1217 keeps channel actors alive across peer disconnects and lets the
channel actor drive reestablish on reconnect. The observable contract from
Python is simple: a ready channel must stay usable after disconnect/reconnect,
including when there is a pending hold TLC during the offline window.

The multi-channel case verifies that every channel actor becomes usable again
after both endpoints restart, including a channel with a pending TLC.
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
    def _build_direct_router(self, sender, receiver, channel_outpoint, amount):
        last_error = None
        for _ in range(60):
            try:
                router = sender.get_client().build_router(
                    {
                        "amount": hex(amount),
                        "udt_type_script": None,
                        "hops_info": [
                            {
                                "pubkey": receiver.get_pubkey(),
                                "channel_outpoint": channel_outpoint,
                            }
                        ],
                        "final_tlc_expiry_delta": None,
                    }
                )["router_hops"]
                assert len(router) == 1
                assert router[0]["channel_outpoint"] == channel_outpoint
                return router
            except Exception as error:
                last_error = error
                time.sleep(0.5)
        assert False, f"could not build router for {channel_outpoint}: {last_error}"

    def _send_keysend_via_channel(self, sender, receiver, channel_outpoint, amount):
        payment = sender.get_client().send_payment_with_router(
            {
                "payment_hash": None,
                "invoice": None,
                "keysend": True,
                "custom_records": None,
                "dry_run": False,
                "udt_type_script": None,
                "router": self._build_direct_router(
                    sender, receiver, channel_outpoint, amount
                ),
            }
        )
        self.wait_payment_state(sender, payment["payment_hash"], "Success", 120)

    def _wait_for_both_peers(self, fiber1_pubkey, fiber2_pubkey, timeout=60):
        consecutive = 0
        for _ in range(timeout * 2):
            fiber1_peers = self.fiber1.get_client().list_peers().get("peers") or []
            fiber2_peers = self.fiber2.get_client().list_peers().get("peers") or []
            fiber1_connected = any(
                peer.get("pubkey") == fiber2_pubkey for peer in fiber1_peers
            )
            fiber2_connected = any(
                peer.get("pubkey") == fiber1_pubkey for peer in fiber2_peers
            )
            if fiber1_connected and fiber2_connected:
                consecutive += 1
                if consecutive == 3:
                    return
            else:
                consecutive = 0
            time.sleep(0.5)
        assert False, "peers did not remain connected on both nodes"

    def _wait_for_channels_without_pending_tlcs(
        self, channel_ids, fiber1_pubkey, fiber2_pubkey, timeout=60
    ):
        last_snapshot = {}
        for _ in range(timeout * 2):
            fiber1_channels = self.fiber1.get_client().list_channels(
                {"pubkey": fiber2_pubkey}
            )["channels"]
            fiber2_channels = self.fiber2.get_client().list_channels(
                {"pubkey": fiber1_pubkey}
            )["channels"]
            fiber1_by_id = {
                channel["channel_id"]: channel for channel in fiber1_channels
            }
            fiber2_by_id = {
                channel["channel_id"]: channel for channel in fiber2_channels
            }
            last_snapshot = {}
            for channel_id in channel_ids:
                fiber1_channel = fiber1_by_id.get(channel_id)
                fiber2_channel = fiber2_by_id.get(channel_id)
                last_snapshot[channel_id] = {
                    "fiber1": (
                        None
                        if fiber1_channel is None
                        else {
                            "state": fiber1_channel.get("state", {}).get("state_name"),
                            "pending_payment_hashes": [
                                tlc.get("payment_hash")
                                for tlc in fiber1_channel.get("pending_tlcs", [])
                            ],
                        }
                    ),
                    "fiber2": (
                        None
                        if fiber2_channel is None
                        else {
                            "state": fiber2_channel.get("state", {}).get("state_name"),
                            "pending_payment_hashes": [
                                tlc.get("payment_hash")
                                for tlc in fiber2_channel.get("pending_tlcs", [])
                            ],
                        }
                    ),
                }
            if all(
                channel_id in fiber1_by_id
                and channel_id in fiber2_by_id
                and fiber1_by_id[channel_id].get("pending_tlcs", []) == []
                and fiber2_by_id[channel_id].get("pending_tlcs", []) == []
                for channel_id in channel_ids
            ):
                return
            time.sleep(0.5)
        assert False, (
            "pending TLCs did not clear on every restarted channel; "
            f"last_snapshot={last_snapshot}"
        )

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

    def test_multiple_channels_with_pending_tlc_survive_both_nodes_restart(self):
        fiber1_pubkey = self.fiber1.get_pubkey()
        fiber2_pubkey = self.fiber2.get_pubkey()
        channel_ids = []
        for _ in range(3):
            channel_ids.append(
                self.open_channel(
                    self.fiber1,
                    self.fiber2,
                    500 * 100000000,
                    0,
                )
            )

        channels = self.fiber1.get_client().list_channels(
            {"pubkey": self.fiber2.get_pubkey()}
        )["channels"]
        channels_by_id = {channel["channel_id"]: channel for channel in channels}
        assert all(channel_id in channels_by_id for channel_id in channel_ids)
        channel_outpoints = [
            channels_by_id[channel_id]["channel_outpoint"] for channel_id in channel_ids
        ]

        # Exercise every channel and give fiber2 enough balance for the
        # reverse, per-channel payments performed after restart.
        for channel_outpoint in channel_outpoints:
            self._send_keysend_via_channel(
                self.fiber1,
                self.fiber2,
                channel_outpoint,
                10 * 100000000,
            )
        self._wait_for_channels_without_pending_tlcs(
            channel_ids, fiber1_pubkey, fiber2_pubkey
        )

        preimage = self.generate_random_preimage()
        payment_hash = _sha256_hex(preimage)
        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(1 * 100000000),
                "currency": "Fibd",
                "description": "multi-channel hold TLC across restart",
                "expiry": "0xe10",
                "final_cltv": "0x28",
                "payment_hash": payment_hash,
                "hash_algorithm": "sha256",
            }
        )
        payment = self.fiber1.get_client().send_payment_with_router(
            {
                "payment_hash": None,
                "invoice": invoice["invoice_address"],
                "keysend": False,
                "custom_records": None,
                "dry_run": False,
                "udt_type_script": None,
                "router": self._build_direct_router(
                    self.fiber1,
                    self.fiber2,
                    channel_outpoints[0],
                    1 * 100000000,
                ),
            }
        )
        self.wait_invoice_state(self.fiber2, payment_hash, "Received", 120, 1)
        self.wait_payment_state(self.fiber1, payment_hash, "Inflight", 30)
        channels = self.fiber1.get_client().list_channels({"pubkey": fiber2_pubkey})[
            "channels"
        ]
        hold_channel = next(
            channel for channel in channels if channel["channel_id"] == channel_ids[0]
        )
        assert any(
            tlc.get("payment_hash") == payment_hash
            for tlc in hold_channel.get("pending_tlcs", [])
        )

        self.fiber1.stop()
        self.fiber2.stop()
        self.fiber2.start(fnn_log_level=self.fnn_log_level)
        self.fiber1.start(fnn_log_level=self.fnn_log_level)
        fiber1_peers = self.fiber1.get_client().list_peers().get("peers") or []
        if not any(peer.get("pubkey") == fiber2_pubkey for peer in fiber1_peers):
            self.fiber1.connect_peer(self.fiber2)
        self._wait_for_both_peers(fiber1_pubkey, fiber2_pubkey)

        assert (
            self.fiber2.get_client().get_invoice({"payment_hash": payment_hash})[
                "status"
            ]
            == "Received"
        )
        self.wait_payment_state(self.fiber1, payment_hash, "Inflight", 30)

        self.fiber2.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )
        self.wait_payment_state(self.fiber1, payment["payment_hash"], "Success", 120)
        self.wait_invoice_state(self.fiber2, payment_hash, "Paid", 120, 1)

        # Stored ChannelReady state alone is not enough: force a real payment
        # through every channel in both directions after reestablishment.
        for channel_outpoint in channel_outpoints:
            self._send_keysend_via_channel(
                self.fiber1,
                self.fiber2,
                channel_outpoint,
                1 * 100000000,
            )
            self._send_keysend_via_channel(
                self.fiber2,
                self.fiber1,
                channel_outpoint,
                1 * 100000000,
            )

        self._wait_for_channels_without_pending_tlcs(
            channel_ids, fiber1_pubkey, fiber2_pubkey
        )
        self._wait_for_both_peers(fiber1_pubkey, fiber2_pubkey)

        for fiber, peer_pubkey in (
            (self.fiber1, fiber2_pubkey),
            (self.fiber2, fiber1_pubkey),
        ):
            channels = fiber.get_client().list_channels({"pubkey": peer_pubkey})[
                "channels"
            ]
            restarted_channels = [
                channel for channel in channels if channel["channel_id"] in channel_ids
            ]
            assert len(restarted_channels) == len(channel_ids)
            assert all(
                channel["state"]["state_name"] == "ChannelReady"
                for channel in restarted_channels
            )
            assert all(
                channel.get("pending_tlcs", []) == [] for channel in restarted_channels
            )
