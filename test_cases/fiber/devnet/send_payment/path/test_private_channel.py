import time

import pytest

from framework.basic_fiber import FiberTest


class TestPrivateChannel(FiberTest):

    # @pytest.mark.skip("https://github.com/nervosnetwork/fiber/pull/502")
    def test_private_channel(self):
        """
        a-私-b-c-d-私-a
        1. a->b
        2. a->c
        3. a->d
        4. a->a
        Returns:

        """
        self.start_new_fiber(self.generate_account(10000))
        self.start_new_fiber(self.generate_account(10000))

        fiber1_balance = 1000 * 100000000
        fiber1_fee = 1000
        self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(fiber1_balance + DEFAULT_MIN_DEPOSIT_CKB),
                "tlc_fee_proportional_millionths": hex(fiber1_fee),
                "public": False,
            }
        )
        time.sleep(1)
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY"
        )
        self.open_channel(self.fibers[1], self.fibers[2], 1000 * 100000000, 1)
        self.open_channel(self.fibers[2], self.fibers[3], 1000 * 100000000, 1)

        self.fibers[3].connect_peer(self.fibers[0])
        time.sleep(1)
        self.fibers[3].get_client().open_channel(
            {
                "peer_id": self.fibers[0].get_peer_id(),
                "funding_amount": hex(fiber1_balance + DEFAULT_MIN_DEPOSIT_CKB),
                "tlc_fee_proportional_millionths": hex(fiber1_fee),
                "public": False,
            }
        )
        time.sleep(1)
        self.wait_for_channel_state(
            self.fibers[3].get_client(), self.fibers[0].get_peer_id(), "CHANNEL_READY"
        )
        time.sleep(1)
        for i in range(1, len(self.fibers)):
            self.send_payment(self.fibers[0], self.fibers[i], 1 * 100000000)

        self.send_payment(self.fibers[0], self.fibers[0], 1 * 100000000)
