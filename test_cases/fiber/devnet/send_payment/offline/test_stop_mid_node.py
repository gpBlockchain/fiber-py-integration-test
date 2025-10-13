import time

import pytest

from framework.basic_fiber import FiberTest


class TestStopMidNode(FiberTest):

    # @pytest.mark.skip("https://github.com/nervosnetwork/fiber/issues/464")
    def test_stop_mid_node(self):
        """

        Returns:
        """
        fiber = self.start_new_fiber(self.generate_account(10000))
        self.fiber1.connect_peer(fiber)
        fiber = self.start_new_fiber(self.generate_account(10000))
        self.fiber1.connect_peer(fiber)
        self.open_channel(self.fibers[0], self.fibers[1], 1000 * 100000000, 1)
        self.open_channel(self.fibers[1], self.fibers[2], 1000 * 100000000, 1)
        self.open_channel(self.fibers[2], self.fibers[3], 1000 * 100000000, 1)
        payment = (
            self.fibers[0]
            .get_client()
            .send_payment(
                {
                    "target_pubkey": self.fibers[3].get_client().node_info()["node_id"],
                    "amount": hex(10 * 100000000),
                    "keysend": True,
                }
            )
        )
        self.wait_payment_state(self.fibers[0], payment["payment_hash"], "Success", 120)

        self.fibers[2].stop()
        # fiber0 send payment to fiber3
        payment = (
            self.fibers[0]
            .get_client()
            .send_payment(
                {
                    "target_pubkey": self.fibers[3].get_client().node_info()["node_id"],
                    "amount": hex(10 * 100000000),
                    "keysend": True,
                }
            )
        )
        self.wait_payment_state(self.fibers[0], payment["payment_hash"], "Failed", 120)
        self.fibers[2].start()
        time.sleep(10)
        payment = (
            self.fibers[0]
            .get_client()
            .send_payment(
                {
                    "target_pubkey": self.fibers[3].get_client().node_info()["node_id"],
                    "amount": hex(10 * 100000000),
                    "keysend": True,
                }
            )
        )
        self.wait_payment_state(self.fibers[0], payment["payment_hash"], "Success", 120)
