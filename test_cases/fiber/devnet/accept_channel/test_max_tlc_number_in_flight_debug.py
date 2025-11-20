import time

import pytest

from framework.basic_fiber import FiberTest
from framework.config import DEFAULT_MIN_DEPOSIT_CKB
from framework.test_fiber import FiberConfigPath


class TestMaxTlcNumberInFlightDebug(FiberTest):

    def test_max_tlc_number_in_flight(self):
        """
        Returns:
        """
        # 1. Open a new channel with fiber1 as the client and fiber2 as the peer
        temporary_channel = self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(DEFAULT_MIN_DEPOSIT_CKB),
                "public": True,
            }
        )
        time.sleep(1)
        # 2. Accept the channel with fiber2 as the client
        self.fiber2.get_client().accept_channel(
            {
                "temporary_channel_id": temporary_channel["temporary_channel_id"],
                "funding_amount": hex(1000 * 100000000),
                "max_tlc_number_in_flight": hex(1),
            }
        )
        # 3. Wait for the channel state to be "CHANNEL_READY"
        self.wait_for_channel_state(
            self.fiber2.get_client(), self.fiber1.get_peer_id(), "CHANNEL_READY"
        )
        # node1 send_payment to node2
        fiber1_invoices = self.fiber1.get_client().new_invoice(
            {
                "amount": hex(1),
                "currency": "Fibd",
                "description": "test invoice",
                "payment_hash": self.generate_random_preimage(),
            }
        )
        self.fiber2.get_client().send_payment(
            {
                "invoice": fiber1_invoices["invoice_address"],
            }
        )
        fiber1_invoices = self.fiber1.get_client().new_invoice(
            {
                "amount": hex(1),
                "currency": "Fibd",
                "description": "test invoice",
                "payment_hash": self.generate_random_preimage(),
            }
        )
        payment = self.fiber2.get_client().send_payment(
            {
                "invoice": fiber1_invoices["invoice_address"],
            }
        )
        self.wait_payment_state(self.fiber2, payment["payment_hash"], "Failed")
