import time

import pytest

from framework.basic_fiber import FiberTest
from framework.config import DEFAULT_MIN_DEPOSIT_CKB
from framework.test_fiber import FiberConfigPath


class TestP2p(FiberTest):
    # debug = True
    # @pytest.mark.skip("todo")
    def test_old_fiber(self):
        """
        Returns:
        """
        old_fiber = self.start_new_fiber(
            self.generate_account(10000), fiber_version=FiberConfigPath.V091_DEV
        )
        self.open_channel(self.fiber1, old_fiber, 1000 * 100000000, 1000 * 100000000)
        self.open_channel(old_fiber, self.fiber2, 1000 * 100000000, 1000 * 100000000)
        self.open_channel(self.fiber2, self.fiber1, 1000 * 100000000, 1000 * 100000000)
        for fiber in self.fibers:
            for fiber2 in self.fibers:
                self.send_payment(fiber, fiber2, 1)

        channel_id = self.fiber1.get_client().list_channels(
            {"pubkey": old_fiber.get_pubkey()}
        )["channels"][0]["channel_id"]
        self.fiber1.get_client().shutdown_channel({"channel_id": channel_id})
        self.wait_for_channel_state(
            self.fiber1.get_client(),
            old_fiber.get_pubkey(),
            "Closed",
            include_closed=True,
            channel_id=channel_id,
        )
