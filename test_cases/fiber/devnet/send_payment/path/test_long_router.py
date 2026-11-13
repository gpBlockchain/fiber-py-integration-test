import time

import pytest

from framework.basic_fiber import FiberTest


class TestLongRouter(FiberTest):

    def test_long_router(self):
        router_length = 13
        for i in range(router_length):
            account_private = self.generate_account(1000)
            fiber = self.start_new_fiber(account_private)
            fiber.connect_peer(self.fiber1)
            fiber.connect_peer(self.fiber2)
        for i in range(len(self.fibers) - 1):
            linked_fiber = self.fibers[i + 1]
            current_fiber = self.fibers[i]
            linked_fiber.connect_peer(current_fiber)
            time.sleep(1)
            # open channel
            current_fiber.get_client().open_channel(
                {
                    "pubkey": linked_fiber.get_pubkey(),
                    "funding_amount": hex(500 * 100000000),
                    "public": True,
                }
            )
            # // AWAITING_TX_SIGNATURES
            self.wait_for_channel_state(
                current_fiber.get_client(), linked_fiber.get_pubkey(), "ChannelReady"
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
                current_fiber.get_client(), linked_fiber.get_pubkey(), "ChannelReady"
            )

        before_balance = self.get_fibers_balance()

        time.sleep(1)
        pub_key = self.fibers[-1].get_client().node_info()["pubkey"]
        payment = (
            self.fibers[0]
            .get_client()
            .send_payment(
                {
                    "target_pubkey": pub_key,
                    "amount": hex(10 * 100000000),
                    "keysend": True,
                    "max_fee_rate": hex(99),
                }
            )
        )
        self.wait_payment_state(self.fibers[0], payment["payment_hash"], "Success")
        # Payment Success can precede channel balance updates along the route.
        for fiber in self.fibers:
            self.wait_fibers_pending_tlc_eq0(fiber)
        after_balance = self.get_fibers_balance()
        result = self.get_channel_balance_change(before_balance, after_balance)
        print(result)
        assert result == [
            {
                "local_balance": 1013078296,
                "offered_tlc_balance": 0,
                "received_tlc_balance": 0,
            },
            {
                "local_balance": -1012067,
                "offered_tlc_balance": 0,
                "received_tlc_balance": 0,
            },
            {
                "local_balance": -1011056,
                "offered_tlc_balance": 0,
                "received_tlc_balance": 0,
            },
            {
                "local_balance": -1010046,
                "offered_tlc_balance": 0,
                "received_tlc_balance": 0,
            },
            {
                "local_balance": -1009037,
                "offered_tlc_balance": 0,
                "received_tlc_balance": 0,
            },
            {
                "local_balance": -1008029,
                "offered_tlc_balance": 0,
                "received_tlc_balance": 0,
            },
            {
                "local_balance": -1007022,
                "offered_tlc_balance": 0,
                "received_tlc_balance": 0,
            },
            {
                "local_balance": -1006016,
                "offered_tlc_balance": 0,
                "received_tlc_balance": 0,
            },
            {
                "local_balance": -1005011,
                "offered_tlc_balance": 0,
                "received_tlc_balance": 0,
            },
            {
                "local_balance": -1004007,
                "offered_tlc_balance": 0,
                "received_tlc_balance": 0,
            },
            {
                "local_balance": -1003004,
                "offered_tlc_balance": 0,
                "received_tlc_balance": 0,
            },
            {
                "local_balance": -1002001,
                "offered_tlc_balance": 0,
                "received_tlc_balance": 0,
            },
            {
                "local_balance": -1001000,
                "offered_tlc_balance": 0,
                "received_tlc_balance": 0,
            },
            {
                "local_balance": -1000000,
                "offered_tlc_balance": 0,
                "received_tlc_balance": 0,
            },
            {
                "local_balance": -1000000000,
                "offered_tlc_balance": 0,
                "received_tlc_balance": 0,
            },
        ]
