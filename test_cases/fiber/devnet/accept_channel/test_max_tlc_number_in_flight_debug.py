import time

import pytest

from framework.basic_fiber import FiberTest
from framework.test_fiber import FiberConfigPath


class TestMaxTlcNumberInFlightDebug(FiberTest):
    fiber_version = FiberConfigPath.CURRENT_DEV_DEBUG

    def test_max_tlc_number_in_flight(self):
        """

        Returns:

        """
        # 1. Open a new channel with fiber1 as the client and fiber2 as the peer
        temporary_channel = self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(98 * 100000000),
                "public": True,
            }
        )
        time.sleep(1)
        # 2. Accept the channel with fiber2 as the client
        self.fiber2.get_client().accept_channel(
            {
                "temporary_channel_id": temporary_channel["temporary_channel_id"],
                "funding_amount": hex(1000 * 100000000),
                "max_tlc_number_in_flight": hex(1),
            }
        )
        # 3. Wait for the channel state to be "CHANNEL_READY"
        self.wait_for_channel_state(
            self.fiber2.get_client(), self.fiber1.get_peer_id(), "CHANNEL_READY"
        )
        # node1 send_payment to node2
        self.fiber2.get_client().add_tlc(
            {
                "channel_id": self.fiber2.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "amount": hex(100),
                # "payment_hash": invoice_list[i]['invoice']['data']['payment_hash'],
                "payment_hash": self.generate_random_preimage(),
                "expiry": hex((int(time.time()) + 40) * 1000),
                "hash_algorithm": "sha256",
            }
        )

        with pytest.raises(Exception) as exc_info:
            self.fiber2.get_client().add_tlc(
                {
                    "channel_id": self.fiber2.get_client().list_channels({})[
                        "channels"
                    ][0]["channel_id"],
                    "amount": hex(100),
                    # "payment_hash": invoice_list[i]['invoice']['data']['payment_hash'],
                    "payment_hash": self.generate_random_preimage(),
                    "expiry": hex((int(time.time()) + 40) * 1000),
                    "hash_algorithm": "sha256",
                }
            )

        expected_error_message = "TemporaryChannelFailure"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )
