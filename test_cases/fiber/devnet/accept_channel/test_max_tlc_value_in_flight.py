import time

from framework.basic_fiber import FiberTest


class TestMaxTlcValueInFlight(FiberTest):

    def test_max_tlc_value_in_flight_exist(self):
        """
        max_tlc_value_in_flight = 1 ckb
        Returns:

        """
        # 1. Open a new channel with fiber1 as the client and fiber2 as the peer
        temporary_channel = self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(98 * 100000000),
                "public": True,
            }
        )
        time.sleep(1)
        # 2. Accept the channel with fiber2 as the client
        self.fiber2.get_client().accept_channel(
            {
                "temporary_channel_id": temporary_channel["temporary_channel_id"],
                "funding_amount": hex(1000 * 100000000),
                "max_tlc_value_in_flight": hex(1 * 100000000),
            }
        )
        # 3. Wait for the channel state to be "CHANNEL_READY"
        self.wait_for_channel_state(
            self.fiber2.get_client(), self.fiber1.get_peer_id(), "CHANNEL_READY"
        )
        # node1 send_payment to node2
        self.send_payment(self.fiber2, self.fiber1, 1 * 100000000)
        self.send_payment(self.fiber2, self.fiber1, 1 * 100000000)
        payment_hash = self.send_payment(
            self.fiber2, self.fiber1, 1 * 100000000 + 1, False
        )
        self.wait_payment_state(self.fiber2, payment_hash, "Failed")
        self.send_payment(self.fiber1, self.fiber2, 1 * 100000000 + 1)
