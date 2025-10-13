import time

import pytest

from framework.basic_fiber import FiberTest


class TestLongRouter(FiberTest):
    FiberTest.debug = True

    def test_long_router(self):
        router_length = 13
        for i in range(router_length):
            account_private = self.generate_account(1000)
            fiber = self.start_new_fiber(account_private)
            fiber.connect_peer(self.fiber1)
            fiber.connect_peer(self.fiber2)
        for i in range(len(self.fibers) - 1):
            linked_fiber = self.fibers[(i + 1) % len(self.fibers)]
            current_fiber = self.fibers[i]
            linked_fiber.connect_peer(current_fiber)
            time.sleep(1)
            # open channel
            current_fiber.get_client().open_channel(
                {
                    "peer_id": linked_fiber.get_peer_id(),
                    "funding_amount": hex(500 * 100000000),
                    "public": True,
                }
            )
            # // AWAITING_TX_SIGNATURES
            self.wait_for_channel_state(
                current_fiber.get_client(), linked_fiber.get_peer_id(), "CHANNEL_READY"
            )
            # linked_fiber.get_client().update_channel(
            #     {
            #         "channel_id": current_fiber.get_client().list_channels({})[
            #             "channels"
            #         ][0]["channel_id"],
            #         "tlc_fee_proportional_millionths": hex(2000),
            #     }
            # )
            self.wait_for_channel_state(
                current_fiber.get_client(), linked_fiber.get_peer_id(), "CHANNEL_READY"
            )

        time.sleep(1)
        pub_key = self.fibers[-1].get_client().node_info()["node_id"]
        payment = (
            self.fibers[0]
            .get_client()
            .send_payment(
                {
                    "target_pubkey": pub_key,
                    "amount": hex(10 * 100000000),
                    "keysend": True,
                }
            )
        )
        self.wait_payment_state(self.fibers[0], payment["payment_hash"], "Success")
