import time

from framework.basic_fiber import FiberTest


class TestTlcFeeProportionalMillionths(FiberTest):

    def test_tlc_fee_proportional_millionths(self):
        # 1. Open a new channel with fiber1 as the client and fiber2 as the peer
        temporary_channel = self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(62 * 100000000),
                "public": True,
            }
        )
        time.sleep(1)
        # 2. Accept the channel with fiber2 as the client
        self.fiber2.get_client().accept_channel(
            {
                "temporary_channel_id": temporary_channel["temporary_channel_id"],
                "funding_amount": hex(1000 * 100000000),
                "tlc_fee_proportional_millionths": hex(1 * 100000000),
            }
        )
        # 3. Wait for the channel state to be "CHANNEL_READY"
        self.wait_for_channel_state(
            self.fiber2.get_client(), self.fiber1.get_peer_id(), "CHANNEL_READY"
        )

        self.fiber3 = self.start_new_fiber(self.generate_account(10000))
        self.open_channel(self.fiber3, self.fiber2, 1000 * 100000000, 1)
        payment_hash = self.send_payment(self.fiber3, self.fiber1, 1 * 10000000)
        payment = self.fiber3.get_client().get_payment(
            {
                "payment_hash": payment_hash,
            }
        )
        print("payment", payment)
        fee = self.calculate_tx_fee(1 * 10000000, [100000000])
        assert int(payment["fee"], 16) == fee
