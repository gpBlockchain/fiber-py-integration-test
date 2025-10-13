import pytest

from framework.basic_fiber import FiberTest


class TestTlcExpiryDelta(FiberTest):

    @pytest.mark.skip("tlc_expiry_delta = 0xffffffffffffffff, 预期：成功")
    def test_01(self):
        """

        Returns:

        """
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1)
        channel = (
            self.fibers[0]
            .get_client()
            .list_channels({"peer_id": self.fibers[1].get_peer_id()})
        )

        # 0x0
        with pytest.raises(Exception) as exc_info:
            self.fibers[1].get_client().update_channel(
                {
                    "channel_id": channel["channels"][0]["channel_id"],
                    "tlc_expiry_delta": "0x0",
                }
            )
        expected_error_message = (
            "TLC expiry delta is too small, expect larger than 900000"
        )
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

        with pytest.raises(Exception) as exc_info:
            self.fibers[1].get_client().update_channel(
                {
                    "channel_id": channel["channels"][0]["channel_id"],
                    "tlc_expiry_delta": hex(900000 - 1),
                }
            )
        expected_error_message = (
            "TLC expiry delta is too small, expect larger than 900000"
        )
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

        with pytest.raises(Exception) as exc_info:
            self.fibers[1].get_client().update_channel(
                {
                    "channel_id": channel["channels"][0]["channel_id"],
                    "tlc_expiry_delta": "0xffffffffffffffffff",
                }
            )
        expected_error_message = (
            "TLC expiry delta is too small, expect larger than 900000"
        )
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

        self.fibers[1].get_client().update_channel(
            {
                "channel_id": channel["channels"][0]["channel_id"],
                "tlc_expiry_delta": hex(1000000),
            }
        )
        channel = self.fibers[1].get_client().list_channels({})
        print(channel)
        assert channel["tlc_expiry_delta"] == hex(1000000)
