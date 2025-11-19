import time

import pytest

from framework.basic_fiber import FiberTest


class TestAbandonChannel(FiberTest):
    """
    abandon channel
    存在的chain id
        tmp_id
        channel 状态
            sign
            await tx ready
            ready
            shutdown
            close
        批量open_channel  随机abandon channel
    不存在的channel id
    """

    def test_tmp_id(self):
        channel = self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(1 + 98 * 100000000),
                "public": True,
            }
        )
        time.sleep(1)
        response = self.fiber1.get_client().abandon_channel(
            {"channel_id": channel["temporary_channel_id"]}
        )
        self.fiber2.get_client().accept_channel(
            {
                "temporary_channel_id": channel["temporary_channel_id"],
                "funding_amount": hex(98 * 100000000),
            }
        )
        channel = self.fiber1.get_client().list_channels({})
        assert len(channel["channels"]) == 0
        channel = self.fiber2.get_client().list_channels({})
        assert len(channel["channels"]) == 0

    def test_abandon_channel_accept(self):
        channel = self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(1 + 98 * 100000000),
                "public": True,
            }
        )
        time.sleep(1)
        self.fiber2.get_client().accept_channel(
            {
                "temporary_channel_id": channel["temporary_channel_id"],
                "funding_amount": hex(98 * 100000000),
            }
        )
        # self.wait_and_check_tx_pool_fee(1000, False, 120)
        time.sleep(1)
        # Test AbandonChannel
        channel_id = self.fiber1.get_client().list_channels({})["channels"][0][
            "channel_id"
        ]

        with pytest.raises(Exception) as exc_info:
            response = self.fiber1.get_client().abandon_channel(
                {"channel_id": channel_id}
            )
        expected_error_message = " our signature has been sent. It cannot be abandoned"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

    def test_abandon_channel_when_tx_send(self):
        channel = self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
            }
        )
        self.wait_and_check_tx_pool_fee(1000, False, 120)
        with pytest.raises(Exception) as exc_info:
            channel = self.fiber1.get_client().list_channels({})
            response = self.fiber1.get_client().abandon_channel(
                {"channel_id": channel["channels"][0]["channel_id"]}
            )

        expected_error_message = "is in state AwaitingTxSignatures"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

    def test_chain_status_ready_or_shutdown_close(self):
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1)
        channels = self.fiber1.get_client().list_channels({})

        # Channel Ready
        with pytest.raises(Exception) as exc_info:
            response = self.fiber1.get_client().abandon_channel(
                {"channel_id": channels["channels"][0]["channel_id"]}
            )
        expected_error_message = (
            "cannot be abandoned, please shutdown the channel instead"
        )
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

        # shutdown
        self.fiber1.get_client().shutdown_channel(
            {
                "channel_id": channels["channels"][0]["channel_id"],
                "close_script": self.get_account_script(self.Config.ACCOUNT_PRIVATE_1),
                "fee_rate": "0x3FC",
            }
        )
        with pytest.raises(Exception) as exc_info:
            response = self.fiber1.get_client().abandon_channel(
                {"channel_id": channels["channels"][0]["channel_id"]}
            )
        expected_error_message = (
            "cannot be abandoned, please shutdown the channel instead"
        )
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )
        # closed
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_peer_id(), "CLOSED", 120, True
        )
        with pytest.raises(Exception) as exc_info:
            response = self.fiber1.get_client().abandon_channel(
                {"channel_id": channels["channels"][0]["channel_id"]}
            )
        expected_error_message = (
            "cannot be abandoned, please shutdown the channel instead"
        )
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )
