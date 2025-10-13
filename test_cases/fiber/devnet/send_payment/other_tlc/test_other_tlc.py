import time

from framework.basic_fiber import FiberTest
from framework.test_fiber import FiberConfigPath


class OtherTlcTest(FiberTest):

    debug = True
    fiber_version = FiberConfigPath.CURRENT_DEV_DEBUG

    def test_other_tlc(self):
        """
        1. open_channel fiber1-fiber2 N channels
        2.
        Returns:
        """
        self.fiber3 = self.start_new_fiber(self.generate_account(10000))
        #
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0, 0, 0)
        CHANNEL_ID = self.fiber1.get_client().list_channels({})["channels"][0][
            "channel_id"
        ]

        tlc = self.fiber1.get_client().add_tlc(
            {
                "channel_id": CHANNEL_ID,
                "amount": hex(1 * 100000001),
                "payment_hash": self.generate_random_preimage(),
                "expiry": hex((int(time.time()) + 10) * 1000),
            }
        )

    def test_00011(self):
        channels = self.fiber1.get_client().list_channels({})
        for i in range(200):
            for channel in channels["channels"][1:]:
                CHANNEL_ID = channel["channel_id"]
                tlc = self.fiber1.get_client().add_tlc(
                    {
                        "channel_id": CHANNEL_ID,
                        "amount": hex(1 * 100000001),
                        "payment_hash": self.generate_random_preimage(),
                        "expiry": hex((int(time.time()) + 10000) * 1000),
                    }
                )
                time.sleep(0.5)
            self.get_fiber_graph_balance()

    def test_000222(self):
        for i in range(2000):
            self.send_payment(self.fiber1, self.fiber2, 1)

    def test_02231313(self):
        self.get_fiber_graph_balance()
