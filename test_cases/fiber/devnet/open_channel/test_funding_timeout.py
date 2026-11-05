import time

import pytest

from framework.basic_fiber import FiberTest
from framework.config import DEFAULT_MIN_DEPOSIT_CKB


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
                    "pubkey": self.fiber2.get_pubkey(),
                    "funding_amount": hex(100 * 100000000),
                    "public": True,
                    "tlc_fee_proportional_millionths": "0x4B0",
                }
            )
            time.sleep(5)
            channels = self.fiber3.get_client().list_channels({})
            time.sleep(15)
            self.fiber2.get_client().accept_channel(
                {
                    "temporary_channel_id": temporary_channel1["temporary_channel_id"],
                    "funding_amount": hex(DEFAULT_MIN_DEPOSIT_CKB),
                }
            )
            time.sleep(1)
            self.wait_channel_size(self.fiber2, 0)
            self.wait_channel_size(self.fiber3, 0)

        self.open_channel(self.fiber3, self.fiber2, 1000 * 100000000, 0)
        self.open_channel(self.fiber3, self.fiber2, 1000 * 100000000, 0)
        self.open_channel(self.fiber3, self.fiber2, 1000 * 100000000, 0)

    def wait_channel_size(self, fiber, size):
        for i in range(60):
            channel = fiber.get_client().list_channels({})
            if len(channel["channels"]) == size:
                return
            time.sleep(1)
        channel = self.fiber1.get_client().list_channels({})
        raise Exception(f"time out for channel size:{len(channel['channels'])}")

    @pytest.mark.skip("todo check ")
    def test_channel_status_is_sign_or_await_ready(self):
        temporary_channel1 = self.fiber1.get_client().open_channel(
            {
                "pubkey": self.fiber2.get_pubkey(),
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
