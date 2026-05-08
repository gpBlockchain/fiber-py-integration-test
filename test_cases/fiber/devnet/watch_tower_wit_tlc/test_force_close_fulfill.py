from framework.basic_fiber import FiberTest
from framework.util import ckb_hash


class TestForceCloseFulfill(FiberTest):
    """
    Regression tests for nervosnetwork/fiber PR #1254.

    When a channel is force-closed and the channel actor is gone, a later
    RemoveTlc(Fulfill) from the peer should still be handled through persisted
    channel state, so the payer payment does not stay Inflight forever.
    """

    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 3}

    def test_one_hop_force_close_payee_settle_invoice(self):
        """
        A -> B

        B creates a hold invoice. A sends payment and force-closes the channel
        while the payment is Inflight. B settles the invoice with the preimage.
        A should eventually mark the payment as Success and B should mark the
        invoice as Paid.
        """
        self.open_channel(
            self.fiber1,
            self.fiber2,
            1000 * 100000000,
            0,
        )

        preimage = self.generate_random_preimage()
        payment_hash = ckb_hash(preimage)

        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(1 * 100000000),
                "currency": "Fibd",
                "description": "pr1254 one-hop hold invoice",
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
            {
                "channel_id": channel_id,
                "force": True,
            }
        )

        self.wait_for_channel_state(
            self.fiber1.get_client(),
            self.fiber2.get_pubkey(),
            "Closed",
            timeout=120,
            include_closed=True,
            channel_id=channel_id,
        )

        self.fiber2.get_client().settle_invoice(
            {
                "payment_hash": payment_hash,
                "payment_preimage": preimage,
            }
        )

        self.wait_payment_state(self.fiber1, payment_hash, "Success", timeout=300)
        self.wait_invoice_state(self.fiber2, payment_hash, "Paid", timeout=300)

        payment_result = self.fiber1.get_client().get_payment(
            {"payment_hash": payment_hash}
        )
        assert payment_result["status"] == "Success"

        invoice_result = self.fiber2.get_client().get_invoice(
            {"payment_hash": payment_hash}
        )
        assert invoice_result["status"] == "Paid"

    def test_two_hop_force_close_downstream_payee_settle_invoice(self):
        """
        A -> B -> C

        C creates a hold invoice. A sends payment to C through B. B force-closes
        the downstream B-C channel while the payment is Inflight. C settles the
        invoice with the preimage. B should handle fulfill on the closed channel
        and relay it upstream, so A should eventually mark the payment as Success.
        """
        fiber3 = self.start_new_fiber(
            self.generate_account(
                10000,
                self.fiber1.account_private,
                10000 * 100000000,
            )
        )

        self.open_channel(
            self.fiber1,
            self.fiber2,
            1000 * 100000000,
            0,
        )
        self.open_channel(
            self.fiber2,
            fiber3,
            1000 * 100000000,
            0,
        )

        preimage = self.generate_random_preimage()
        payment_hash = ckb_hash(preimage)

        invoice = fiber3.get_client().new_invoice(
            {
                "amount": hex(1 * 100000000),
                "currency": "Fibd",
                "description": "pr1254 two-hop hold invoice",
                "payment_hash": payment_hash,
                "allow_mpp": True,
                "allow_trampoline_routing": True,
            }
        )

        payment = self.fiber1.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "trampoline_hops": [
                    self.fiber2.get_client().node_info()["pubkey"],
                ],
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
            {
                "channel_id": channel_bc,
                "force": True,
            }
        )

        self.wait_for_channel_state(
            self.fiber2.get_client(),
            fiber3.get_pubkey(),
            "Closed",
            timeout=120,
            include_closed=True,
            channel_id=channel_bc,
        )

        fiber3.get_client().settle_invoice(
            {
                "payment_hash": payment_hash,
                "payment_preimage": preimage,
            }
        )

        self.wait_payment_state(self.fiber1, payment_hash, "Success", timeout=300)
        self.wait_invoice_state(fiber3, payment_hash, "Paid", timeout=300)

        payment_result = self.fiber1.get_client().get_payment(
            {"payment_hash": payment_hash}
        )
        assert payment_result["status"] == "Success"

        invoice_result = fiber3.get_client().get_invoice(
            {"payment_hash": payment_hash}
        )
        assert invoice_result["status"] == "Paid"
