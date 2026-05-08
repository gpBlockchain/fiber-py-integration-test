import time

from framework.basic_fiber import FiberTest
from framework.util import ckb_hash


class TestForceCloseFulfillP2(FiberTest):
    """P2 restart persistence tests for nervosnetwork/fiber PR #1254."""

    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 3}

    def _wait_unlock(self, timeout=600):
        self.node.getClient().generate_epochs("0x1", wait_time=0)
        for _ in range(timeout // 10):
            if len(self.get_commit_cells()) == 0:
                return
            time.sleep(10)
        assert len(self.get_commit_cells()) == 0

    def _restart_fiber(self, fiber):
        """Restart a Fiber node without cleaning its data directory."""
        fiber.stop()
        time.sleep(2)
        fiber.start(fnn_log_level=self.fnn_log_level)
        time.sleep(3)

    def _get_tlc_status(self, fiber, remote_pubkey, channel_id, payment_hash):
        channels = fiber.get_client().list_channels(
            {"pubkey": remote_pubkey, "include_closed": True}
        )["channels"]
        for channel in channels:
            if channel["channel_id"] != channel_id:
                continue
            for tlc in channel.get("pending_tlcs", []):
                if tlc["payment_hash"] == payment_hash:
                    return tlc["status"]
        raise AssertionError(f"TLC {payment_hash} not found in channel {channel_id}")

    def _assert_sender_remote_removed(self, sender, receiver, channel_id, payment_hash):
        status = self._get_tlc_status(
            sender, receiver.get_pubkey(), channel_id, payment_hash
        )
        assert status == {"Outbound": "RemoteRemoved"}

    def _assert_receiver_local_removed(self, receiver, sender, channel_id, payment_hash):
        status = self._get_tlc_status(
            receiver, sender.get_pubkey(), channel_id, payment_hash
        )
        assert status == {"Inbound": "LocalRemoved"}

    def _assert_success_and_paid(self, payer, payee, payment_hash):
        payment = payer.get_client().get_payment({"payment_hash": payment_hash})
        assert payment["status"] == "Success"
        invoice = payee.get_client().get_invoice({"payment_hash": payment_hash})
        assert invoice["status"] == "Paid"

    def test_one_hop_payer_restart_before_payee_settle_invoice(self):
        """
        A -> B CKB payment, payer restarts after force-close.

        A force-closes the channel and then restarts before B reveals the
        preimage. The final Success status should still be recovered from
        persisted channel/payment state, not in-memory channel actor state.
        The closed channel should show RemoteRemoved on A and LocalRemoved on B.
        """
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0)

        preimage = self.generate_random_preimage()
        payment_hash = ckb_hash(preimage)
        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(1 * 100000000),
                "currency": "Fibd",
                "description": "pr1254 p2 payer restart hold invoice",
                "payment_hash": payment_hash,
                "allow_mpp": True,
                "allow_trampoline_routing": True,
            }
        )
        payment = self.fiber1.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "max_fee_rate": hex(1000000000000000),
            }
        )
        assert payment["payment_hash"] == payment_hash
        self.wait_payment_state(self.fiber1, payment_hash, "Inflight")
        self.wait_invoice_state(self.fiber2, payment_hash, "Received")

        channels = self.fiber1.get_client().list_channels(
            {"pubkey": self.fiber2.get_pubkey()}
        )["channels"]
        assert len(channels) > 0
        channel_id = channels[0]["channel_id"]
        self.fiber1.get_client().shutdown_channel(
            {"channel_id": channel_id, "force": True}
        )

        time.sleep(10)
        self._restart_fiber(self.fiber1)

        self.fiber2.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )
        self._wait_unlock()

        self.wait_payment_state(self.fiber1, payment_hash, "Success", timeout=300)
        self.wait_invoice_state(self.fiber2, payment_hash, "Paid", timeout=300)
        self._assert_sender_remote_removed(
            self.fiber1, self.fiber2, channel_id, payment_hash
        )
        self._assert_receiver_local_removed(
            self.fiber2, self.fiber1, channel_id, payment_hash
        )
        self._assert_success_and_paid(self.fiber1, self.fiber2, payment_hash)

    def test_two_hop_intermediate_restart_before_payee_settle_invoice(self):
        """
        A -> B -> C CKB payment, intermediate node restarts.

        B force-closes the downstream B-C channel and then restarts before C
        reveals the preimage. B should still load persisted channel state,
        handle RemoveTlc(Fulfill) on the closed channel, and relay the fulfill
        upstream so A reaches Success. On the closed B-C channel, B should show
        RemoteRemoved and C should show LocalRemoved.
        """
        fiber3 = self.start_new_fiber(
            self.generate_account(
                10000,
                self.fiber1.account_private,
                10000 * 100000000,
            )
        )
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0)
        self.open_channel(self.fiber2, fiber3, 1000 * 100000000, 0)

        preimage = self.generate_random_preimage()
        payment_hash = ckb_hash(preimage)
        invoice = fiber3.get_client().new_invoice(
            {
                "amount": hex(1 * 100000000),
                "currency": "Fibd",
                "description": "pr1254 p2 intermediate restart hold invoice",
                "payment_hash": payment_hash,
                "allow_mpp": True,
                "allow_trampoline_routing": True,
            }
        )
        payment = self.fiber1.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "trampoline_hops": [self.fiber2.get_client().node_info()["pubkey"]],
                "max_fee_rate": hex(1000000000000000),
            }
        )
        assert payment["payment_hash"] == payment_hash
        self.wait_payment_state(self.fiber1, payment_hash, "Inflight")
        self.wait_invoice_state(fiber3, payment_hash, "Received")

        channels_bc = self.fiber2.get_client().list_channels(
            {"pubkey": fiber3.get_pubkey()}
        )["channels"]
        assert len(channels_bc) > 0
        channel_bc = channels_bc[0]["channel_id"]
        self.fiber2.get_client().shutdown_channel(
            {"channel_id": channel_bc, "force": True}
        )

        time.sleep(10)
        self._restart_fiber(self.fiber2)

        fiber3.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )
        self._wait_unlock()

        self.wait_payment_state(self.fiber1, payment_hash, "Success", timeout=300)
        self.wait_invoice_state(fiber3, payment_hash, "Paid", timeout=300)
        self._assert_sender_remote_removed(self.fiber2, fiber3, channel_bc, payment_hash)
        self._assert_receiver_local_removed(fiber3, self.fiber2, channel_bc, payment_hash)
        self._assert_success_and_paid(self.fiber1, fiber3, payment_hash)
