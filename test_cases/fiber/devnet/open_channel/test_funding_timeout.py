import time

import pytest

from framework.basic_fiber import FiberTest


class TestFundingTimeout(FiberTest):
    start_fiber_config = {"fiber_funding_timeout_seconds": 10}

    def test_funding_timeout(self):
        self.fiber3 = self.start_new_fiber(self.generate_account(10000))
        # self.generate_account(10000))

        # self.fiber3 = self.start_new_fiber(self.generate_account(10000))
        self.fiber3.connect_peer(self.fiber2)
        time.sleep(1)
        for i in range(5):
            temporary_channel1 = self.fiber3.get_client().open_channel(
                {
                    "peer_id": self.fiber2.get_peer_id(),
                    "funding_amount": hex(100 * 100000000),
                    "public": True,
                    "tlc_fee_proportional_millionths": "0x4B0",
                }
            )
            time.sleep(5)
            channels = self.fiber3.get_client().list_channels({})
            assert len(channels["channels"]) == 0
            time.sleep(15)
            self.fiber2.get_client().accept_channel(
                {
                    "temporary_channel_id": temporary_channel1["temporary_channel_id"],
                    "funding_amount": hex(62 * 100000000),
                }
            )
            channels = self.fiber2.get_client().list_channels({})
            assert len(channels["channels"]) == 0
            time.sleep(1)
            channels = self.fiber2.get_client().list_channels({})
            assert len(channels["channels"]) == 0
            channels = self.fiber3.get_client().list_channels({})
            assert len(channels["channels"]) == 0
        self.open_channel(self.fiber3, self.fiber2, 1000 * 100000000, 0)
        self.open_channel(self.fiber3, self.fiber2, 1000 * 100000000, 0)
        self.open_channel(self.fiber3, self.fiber2, 1000 * 100000000, 0)

    @pytest.mark.skip("todo check ")
    def test_channel_status_is_sign_or_await_ready(self):
        temporary_channel1 = self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
                "tlc_fee_proportional_millionths": "0x4B0",
            }
        )
        time.sleep(2)
        self.fiber2.stop()
        time.sleep(20)
        self.fiber1.get_client().list_channels({})
        self.fiber2.start()
        self.fiber2.get_client().list_channels({})
        # todo
        # check fiber1 channels status
        # check fiber2 channels status
