import time

from framework.basic_fiber import FiberTest
from framework.util import ckb_hash


class TestForceCloseFulfillP1(FiberTest):
    """P1 UDT regression tests for nervosnetwork/fiber PR #1254."""

    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 3}

    def _wait_unlock(self, timeout=600):
        self.node.getClient().generate_epochs("0x1", wait_time=0)
        for _ in range(timeout // 10):
            if len(self.get_commit_cells()) == 0:
                return
            time.sleep(10)
        assert len(self.get_commit_cells()) == 0

    def _settle_and_wait(self, payee, payment_hash, preimage):
        time.sleep(10)
        payee.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )
        self._wait_unlock()

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

    def test_one_hop_udt_force_close_payee_settle_invoice(self):
        """
        A -> B UDT payment.

        A sends a UDT hold-invoice payment to B. A then force-closes the UDT
        channel while the payment is Inflight. B settles with the preimage.
        A should recover the fulfill from the closed channel and mark payment
        Success; B should mark the invoice Paid. The TLC status should become
        RemoteRemoved on A and LocalRemoved on B.
        """
        udt_script = self.get_account_udt_script(self.fiber1.account_private)
        self.faucet(
            self.fiber2.account_private,
            0,
            self.fiber1.account_private,
            10000 * 100000000,
        )
        self.open_channel(
            self.fiber1,
            self.fiber2,
            1000 * 100000000,
            0,
            udt=udt_script,
        )

        preimage = self.generate_random_preimage()
        payment_hash = ckb_hash(preimage)
        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(1 * 100000000),
                "currency": "Fibd",
                "description": "pr1254 p1 one-hop UDT hold invoice",
                "udt_type_script": udt_script,
                "payment_hash": payment_hash,
                "allow_mpp": True,
                "allow_trampoline_routing": True,
            }
        )
        payment = self.fiber1.get_client().send_payment(
            {"invoice": invoice["invoice_address"], "max_fee_rate": hex(1000000000000000)}
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

        self._settle_and_wait(self.fiber2, payment_hash, preimage)
        self.wait_payment_state(self.fiber1, payment_hash, "Success", timeout=300)
        self.wait_invoice_state(self.fiber2, payment_hash, "Paid", timeout=300)
        self._assert_sender_remote_removed(
            self.fiber1, self.fiber2, channel_id, payment_hash
        )
        self._assert_receiver_local_removed(
            self.fiber2, self.fiber1, channel_id, payment_hash
        )
        self._assert_success_and_paid(self.fiber1, self.fiber2, payment_hash)

    def test_two_hop_udt_force_close_downstream_payee_settle_invoice(self):
        """
        A -> B -> C UDT payment.

        B force-closes the downstream B-C UDT channel while A's payment to C is
        Inflight. C settles with the preimage. B should recover fulfill from the
        closed channel and relay it upstream so A reaches Success. On the closed
        B-C channel, B should show RemoteRemoved and C should show LocalRemoved.
        """
        udt_script = self.get_account_udt_script(self.fiber1.account_private)
        fiber3 = self.start_new_fiber(
            self.generate_account(
                10000,
                self.fiber1.account_private,
                10000 * 100000000,
            )
        )
        self.faucet(
            self.fiber2.account_private,
            0,
            self.fiber1.account_private,
            10000 * 100000000,
        )
        self.open_channel(
            self.fiber1,
            self.fiber2,
            1000 * 100000000,
            0,
            udt=udt_script,
        )
        self.open_channel(
            self.fiber2,
            fiber3,
            1000 * 100000000,
            0,
            udt=udt_script,
        )

        preimage = self.generate_random_preimage()
        payment_hash = ckb_hash(preimage)
        invoice = fiber3.get_client().new_invoice(
            {
                "amount": hex(1 * 100000000),
                "currency": "Fibd",
                "description": "pr1254 p1 two-hop UDT hold invoice",
                "udt_type_script": udt_script,
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

        self._settle_and_wait(fiber3, payment_hash, preimage)
        self.wait_payment_state(self.fiber1, payment_hash, "Success", timeout=300)
        self.wait_invoice_state(fiber3, payment_hash, "Paid", timeout=300)
        self._assert_sender_remote_removed(self.fiber2, fiber3, channel_bc, payment_hash)
        self._assert_receiver_local_removed(fiber3, self.fiber2, channel_bc, payment_hash)
        self._assert_success_and_paid(self.fiber1, fiber3, payment_hash)
