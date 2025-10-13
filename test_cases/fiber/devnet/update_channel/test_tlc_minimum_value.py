import time

import pytest

from framework.basic_fiber import FiberTest


class TestTlcMinimumValue(FiberTest):
    def test_01(self):
        """
        0x0 succ
        1ckb succ
        int.max succ
        int.max+1=> falied
        Returns:

        """
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1000 * 100000000)
        self.send_payment(self.fiber1, self.fiber2, 1 * 100000000)
        self.send_payment(self.fiber2, self.fiber1, 1 * 100000000)
        self.fiber1.get_client().update_channel(
            {
                "channel_id": self.fiber1.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "tlc_minimum_value": hex(1 * 100000000),
            }
        )
        time.sleep(1)
        self.send_payment(self.fiber2, self.fiber1, 1 * 100000000 - 1)
        print("failed ")
        with pytest.raises(Exception) as exc_info:
            self.send_payment(self.fiber1, self.fiber2, 1 * 100000000 - 1)
        expected_error_message = "no path found"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

        # 0xffffffff
        self.fiber1.get_client().update_channel(
            {
                "channel_id": self.fiber1.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "tlc_minimum_value": "0xffffffffffffffffffffffffffffffff",
            }
        )
        time.sleep(1)
        graph_channels = self.fiber1.get_client().graph_channels({})
        assert (
            graph_channels["channels"][0]["update_info_of_node2"]["tlc_minimum_value"]
            == "0xffffffffffffffffffffffffffffffff"
            or graph_channels["channels"][0]["update_info_of_node1"][
                "tlc_minimum_value"
            ]
            == "0xffffffffffffffffffffffffffffffff"
        )

        # overflow
        with pytest.raises(Exception) as exc_info:
            self.fiber1.get_client().update_channel(
                {
                    "channel_id": self.fiber1.get_client().list_channels({})[
                        "channels"
                    ][0]["channel_id"],
                    "tlc_minimum_value": "0xfffffffffffffffffffffffffffffffff",
                }
            )
        expected_error_message = "Invalid params"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )
