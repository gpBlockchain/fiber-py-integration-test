import time

import pytest

from framework.basic_fiber import FiberTest
from framework.test_fiber import FiberConfigPath


class TestP2p(FiberTest):

    def test_old_fiber(self):
        """
        Returns:
        """
        old_fiber = self.start_new_fiber(
            self.generate_account(10000), fiber_version=FiberConfigPath.V050_DEV
        )
        old_fiber.connect_peer(self.fiber1)
        time.sleep(1)
        with pytest.raises(Exception) as exc_info:
            self.fiber1.get_client().open_channel(
                {
                    "peer_id": old_fiber.get_peer_id(),
                    "funding_amount": hex(1000 + 62 * 100000000),
                    "tlc_fee_proportional_millionths": hex(1000),
                    "public": True,
                }
            )
        expected_error_message = "feature not found"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

        old_fiber.get_client().open_channel(
            {
                "peer_id": self.fiber1.get_peer_id(),
                "funding_amount": hex(2000 * 100000000),
                "tlc_fee_proportional_millionths": hex(1000),
                "public": True,
            }
        )
        time.sleep(1)
        channel = old_fiber.get_client().list_channels({})
        assert len(channel["channels"]) == 0
