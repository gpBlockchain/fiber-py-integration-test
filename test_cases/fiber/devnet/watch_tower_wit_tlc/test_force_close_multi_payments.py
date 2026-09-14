import time

from framework.basic_fiber import FiberTest
from framework.config import DEFAULT_MIN_DEPOSIT_CKB
from framework.util import ckb_hash


class TestForceCloseMultiPayments(FiberTest):
    """
    Regression coverage for one force-closed channel with multiple pending TLCs.

    A sends multiple hold-invoice payments to B on the same channel. A then
    force-closes that channel. After B settles all invoices, every payment
    should reach Success and every invoice should reach Paid.
    """

    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 3}

    def _wait_force_close_unlock(self, timeout=600):
        if len(self.get_commit_cells()) == 0:
            raise Exception("No commit cells found")
        self.node.getClient().generate_epochs("0x1", wait_time=0)
        for _ in range(timeout // 10):
            if len(self.get_commit_cells()) == 0:
                return
            time.sleep(10)
        assert len(self.get_commit_cells()) == 0

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

    def test_one_channel_multiple_payments_force_close(self):
        max_tlc_number_in_flight = 110

        temporary_channel = self.fiber1.get_client().open_channel(
            {
                "pubkey": self.fiber2.get_pubkey(),
                "funding_amount": hex(DEFAULT_MIN_DEPOSIT_CKB + 1000),
                "public": True,
                # "max_tlc_number_in_flight"
                # "max_tlc_number_in_flight": hex(max_tlc_number_in_flight)
            }
        )
        time.sleep(1)
        # 2. Accept the channel with fiber2 as the client
        self.fiber2.get_client().accept_channel(
            {
                "temporary_channel_id": temporary_channel["temporary_channel_id"],
                "funding_amount": hex(1000 * 100000000),
                "max_tlc_number_in_flight": hex(max_tlc_number_in_flight),
            }
        )
        # 3. Wait for the channel state to be "ChannelReady"
        self.wait_for_channel_state(
            self.fiber2.get_client(), self.fiber1.get_pubkey(), "ChannelReady"
        )

        payments = []
        for i in range(50):
            preimage = self.generate_random_preimage()
            payment_hash = ckb_hash(preimage)
            invoice = self.fiber2.get_client().new_invoice(
                {
                    "amount": hex(1),
                    "currency": "Fibd",
                    "description": f"one channel multiple payments {i}",
                    "payment_hash": payment_hash,
                    # "allow_mpp": True,
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
            payments.append((payment_hash, preimage))

        for payment_hash, _ in payments:
            self.wait_payment_state(self.fiber1, payment_hash, "Inflight")
            self.wait_invoice_state(self.fiber2, payment_hash, "Received")
        payments2 = []
        for i in range(50):
            preimage = self.generate_random_preimage()
            payment_hash = ckb_hash(preimage)
            invoice = self.fiber1.get_client().new_invoice(
                {
                    "amount": hex(1 * 100000000),
                    "currency": "Fibd",
                    "description": f"one channel multiple payments {i}",
                    "payment_hash": payment_hash,
                    # "allow_mpp": True,
                    "allow_trampoline_routing": True,
                }
            )
            payment = self.fiber2.get_client().send_payment(
                {
                    "invoice": invoice["invoice_address"],
                    "max_fee_rate": hex(1000000000000000),
                }
            )
            assert payment["payment_hash"] == payment_hash
            payments2.append((payment_hash, preimage))

        for payment_hash, _ in payments2:
            self.wait_payment_state(self.fiber2, payment_hash, "Inflight")
            self.wait_invoice_state(self.fiber1, payment_hash, "Received")

        channels = self.fiber1.get_client().list_channels(
            {"pubkey": self.fiber2.get_pubkey()}
        )["channels"]
        assert len(channels) > 0
        channel_id = channels[0]["channel_id"]
        self.fiber1.get_client().shutdown_channel(
            {"channel_id": channel_id, "force": True}
        )

        time.sleep(10)
        for payment_hash, preimage in payments:
            self.fiber2.get_client().settle_invoice(
                {"payment_hash": payment_hash, "payment_preimage": preimage}
            )

        for payment_hash, preimage in payments2:
            self.fiber1.get_client().settle_invoice(
                {"payment_hash": payment_hash, "payment_preimage": preimage}
            )
        self._wait_force_close_unlock()
        for payment_hash, preimage in payments:
            self.wait_invoice_state(self.fiber2, payment_hash, "Paid")
            self.wait_payment_state(self.fiber1, payment_hash, "Success")

        for payment_hash, preimage in payments2:
            self.wait_invoice_state(self.fiber1, payment_hash, "Paid")
            self.wait_payment_state(self.fiber2, payment_hash, "Success")

    # def test_0000(self):
    #     for i in range(250):
    #         preimage = self.generate_random_preimage()
    #         payment_hash = ckb_hash(preimage)
    #         invoice = self.fiber1.get_client().new_invoice(
    #             {
    #                 "amount": hex(1 ),
    #                 "currency": "Fibd",
    #                 "description": f"one channel multiple payments {i}",
    #                 "payment_hash": payment_hash,
    #                 # "allow_mpp": True,
    #                 "allow_trampoline_routing": True,
    #             }
    #         )
    #         payment = self.fiber2.get_client().send_payment(
    #             {
    #                 "invoice": invoice["invoice_address"],
    #             }
    #         )
