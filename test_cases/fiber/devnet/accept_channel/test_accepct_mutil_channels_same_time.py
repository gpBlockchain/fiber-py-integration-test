import time

import pytest

from framework.basic_fiber import FiberTest


class TestAcceptMutilChannelsSameTime(FiberTest):
    # FiberTest.debug = True

    def test_accept_chanel_same_channel_same_time(self):
        """
        accept_channel: Accept the same channel multiple times at the same time

            1. Open a new channel with fiber1 as the client and fiber2 as the peer
            2. Accept the channel with fiber2 as the client
            3. Attempt to accept the same channel again, expecting an exception
            4. Verify that the expected error message is in the exception
            5. Wait for the channel state to be "CHANNEL_READY"

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
                "funding_amount": hex(200000 * 100000000),
            }
        )

        # 3. Attempt to accept the same channel again, expecting an exception
        with pytest.raises(Exception) as exc_info:
            self.fiber2.get_client().accept_channel(
                {
                    "temporary_channel_id": temporary_channel["temporary_channel_id"],
                    "funding_amount": hex(200000 * 100000000),
                }
            )

        # 4. Verify that the expected error message is in the exception
        expected_error_message = "No channel with temp id"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

        # 5. Wait for the channel state to be "CHANNEL_READY"
        self.wait_for_channel_state(
            self.fiber2.get_client(), self.fiber1.get_peer_id(), "CHANNEL_READY"
        )

    @pytest.mark.skip("https://github.com/nervosnetwork/fiber/issues/246")
    def test_accept_channel_diff_channel_same_time(self):
        """
        accept channel: Accept multiple different channels at the same time
        Steps:
            1. Generate a new account with 1000 units of balance
            2. Start a new fiber with the generated account
            3. Connect fiber3 to fiber2
            4. Open a new channel with fiber1 as the client and fiber2 as the peer
            5. Open another new channel with fiber3 as the client and fiber2 as the peer
            6. Accept the first channel with fiber2 as the client
            7. Accept the second channel with fiber2 as the client
            8. Wait for the first channel state to be "CHANNEL_READY"
            9. Wait for the second channel state to be "AWAITING_TX_SIGNATURES"
            10. Open another new channel with fiber3 as the client and fiber2 as the peer
            11. Accept the new channel with fiber2 as the client
            12. Wait for the new channel state to be "CHANNEL_READY"

        Returns:
        """
        # Step 1: Generate a new account with 1000 units of balance
        account3 = self.generate_account(1000)

        # Step 2: Start a new fiber with the generated account
        fiber3 = self.start_new_fiber(account3)

        # Step 3: Connect fiber3 to fiber2
        fiber3.connect_peer(self.fiber2)
        time.sleep(1)

        # Step 4: Open a new channel with fiber1 as the client and fiber2 as the peer
        temporary_channel1 = self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(98 * 100000000),
                "public": True,
            }
        )

        # Step 5: Open another new channel with fiber3 as the client and fiber2 as the peer
        temporary_channel2 = fiber3.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(98 * 100000000),
                "public": True,
            }
        )
        time.sleep(1)
        temporary_other_channels = []
        for i in range(5):
            temporary_other_channels.append(
                fiber3.get_client().open_channel(
                    {
                        "peer_id": self.fiber2.get_peer_id(),
                        "funding_amount": hex(98 * 100000000),
                        "public": True,
                    }
                )
            )

        # Step 6: Accept the first channel with fiber2 as the client
        self.fiber2.get_client().accept_channel(
            {
                "temporary_channel_id": temporary_channel1["temporary_channel_id"],
                "funding_amount": hex(98 * 100000000),
            }
        )
        time.sleep(0.1)

        # Step 7: Accept the second channel with fiber2 as the client
        self.fiber2.get_client().accept_channel(
            {
                "temporary_channel_id": temporary_channel2["temporary_channel_id"],
                "funding_amount": hex(98 * 100000000),
            }
        )
        time.sleep(0.1)
        for i in range(len(temporary_other_channels)):
            self.fiber2.get_client().accept_channel(
                {
                    "temporary_channel_id": temporary_other_channels[i][
                        "temporary_channel_id"
                    ],
                    "funding_amount": hex(98 * 100000000),
                }
            )
            time.sleep(0.1)

        # Step 8: Wait for the first channel state to be "CHANNEL_READY"
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY"
        )

        # Step 9: Wait for the second channel state to be "AWAITING_TX_SIGNATURES"
        self.wait_for_channel_state(
            self.fiber2.get_client(), fiber3.get_peer_id(), "AWAITING_TX_SIGNATURES"
        )

        time.sleep(5)

        # Step 10: Open another new channel with fiber3 as the client and fiber2 as the peer
        temporary_channel2 = fiber3.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(98 * 100000000),
                "public": True,
            }
        )
        time.sleep(1)

        # Step 11: Accept the new channel with fiber2 as the client
        self.fiber2.get_client().accept_channel(
            {
                "temporary_channel_id": temporary_channel2["temporary_channel_id"],
                "funding_amount": hex(98 * 100000000),
            }
        )

        # Step 12: Wait for the new channel state to be "CHANNEL_READY"
        self.wait_for_channel_state(
            self.fiber2.get_client(), fiber3.get_peer_id(), "CHANNEL_READY"
        )
