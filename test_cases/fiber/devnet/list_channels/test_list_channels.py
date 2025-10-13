import datetime
import time

import pytest

from framework.basic_fiber import FiberTest


class TestListChannels(FiberTest):
    """
    状态
    -  显示 pending 的channels
    -  显示待 accept 的channel
    -  显示 closed channels 信息
    -  显示 channel 的其他信息
    -  显示 accept dan没成功的channel
    -  显示 下线的channel? 如何判断channels 是否在线
    """

    # FiberTest.debug = True

    def test_peer_id(self):
        """
        only show peer id 's  channels

        Returns:
        """
        account3_private_key = self.generate_account(1000)
        fiber3 = self.start_new_fiber(account3_private_key)
        fiber3.connect_peer(self.fiber1)
        temporary_channel_id = self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
                "funding_udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
            }
        )
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY", 120
        )

        temporary_channel_id = self.fiber1.get_client().open_channel(
            {
                "peer_id": fiber3.get_peer_id(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
            }
        )
        self.wait_for_channel_state(
            self.fiber1.get_client(), fiber3.get_peer_id(), "CHANNEL_READY", 120
        )
        n12_channels = self.fiber1.get_client().list_channels(
            {"peer_id": self.fiber2.get_peer_id()}
        )

        n21_channels = self.fiber2.get_client().list_channels(
            {"peer_id": self.fiber1.get_peer_id()}
        )
        assert (
            n12_channels["channels"][0]["channel_id"]
            == n21_channels["channels"][0]["channel_id"]
        )

        n13_channels = self.fiber1.get_client().list_channels(
            {"peer_id": fiber3.get_peer_id()}
        )
        n31_channels = fiber3.get_client().list_channels(
            {"peer_id": self.fiber1.get_peer_id()}
        )
        assert (
            n13_channels["channels"][0]["channel_id"]
            == n31_channels["channels"][0]["channel_id"]
        )

    def test_empty(self):
        """
        show all channels
        Returns:
        """
        account3_private_key = self.generate_account(1000)
        fiber3 = self.start_new_fiber(account3_private_key)
        fiber3.connect_peer(self.fiber1)
        temporary_channel_id = self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
                "funding_udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
            }
        )
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY", 120
        )

        temporary_channel_id = self.fiber1.get_client().open_channel(
            {
                "peer_id": fiber3.get_peer_id(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
            }
        )
        self.wait_for_channel_state(
            self.fiber1.get_client(), fiber3.get_peer_id(), "CHANNEL_READY", 120
        )
        channels = self.fiber1.get_client().list_channels({})
        assert len(channels["channels"]) == 2

    def test_funding_udt_type_script(self):
        """
        funding_udt_type_script

        Returns:
        """
        account3_private_key = self.generate_account(1000)
        fiber3 = self.start_new_fiber(account3_private_key)
        fiber3.connect_peer(self.fiber1)
        temporary_channel_id = self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
                "funding_udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
            }
        )
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY", 120
        )
        channels = self.fiber1.get_client().list_channels({})
        assert channels["channels"][0][
            "funding_udt_type_script"
        ] == self.get_account_udt_script(self.fiber1.account_private)

    @pytest.mark.skip("pass")
    def test_funding_udt_type_script_none(self):
        """
        funding_udt_type_script: none  ==  ckb
        Returns:
        """

    def test_created_at(self):
        """

        Returns:

        """
        account3_private_key = self.generate_account(1000)
        fiber3 = self.start_new_fiber(account3_private_key)
        fiber3.connect_peer(self.fiber1)
        temporary_channel_id = self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
                "funding_udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
            }
        )
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY", 120
        )
        channels = self.fiber1.get_client().list_channels({})
        created_at_hex = int(channels["channels"][0]["created_at"], 16) / 1000

        assert (
            int(int(datetime.datetime.now().timestamp()) / 1000)
            - int(created_at_hex / 1000)
            < 10
        )

    def test_is_public(self):
        """
        Returns:
        """
        temporary_channel_id = self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
                "funding_udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
            }
        )
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY", 120
        )
        channels = self.fiber1.get_client().list_channels({})
        assert channels["channels"][0]["is_public"] == True

    def test_check_channel_result(self):
        """
        Returns:
        """
        begin_time = time.time()
        temporary_channel_id = self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
                "funding_udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
            }
        )
        # self.wait_for_channel_state(
        #     self.fiber1.get_client(), self.fiber2.get_peer_id(), "NEGOTIATING_FUNDING", 120
        # )
        # channels = self.fiber1.get_client().list_channels({"peer_id": self.fiber2.get_peer_id()})
        # assert channels['channels'][0]['channel_outpoint'] is None
        open_tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY", 120
        )
        channels = self.fiber1.get_client().list_channels(
            {"peer_id": self.fiber2.get_peer_id()}
        )
        # channel_id
        assert channels["channels"][0]["channel_id"] is not None

        # is_public
        assert channels["channels"][0]["is_public"] is True

        # channel_outpoint
        assert channels["channels"][0]["channel_outpoint"] is not None
        print("channel_outpoint:", channels["channels"][0]["channel_outpoint"])
        assert open_tx_hash in channels["channels"][0]["channel_outpoint"]

        # peer_id
        assert channels["channels"][0]["peer_id"] == self.fiber2.get_peer_id()

        # funding_udt_type_script
        assert channels["channels"][0][
            "funding_udt_type_script"
        ] == self.get_account_udt_script(self.fiber1.account_private)

        # state
        assert channels["channels"][0]["state"]["state_name"] == "CHANNEL_READY"

        # local_balance
        assert channels["channels"][0]["local_balance"] == hex(1000 * 100000000)

        # offered_tlc_balance
        assert channels["channels"][0]["offered_tlc_balance"] == hex(0)

        # remote_balance
        assert channels["channels"][0]["remote_balance"] == hex(0)

        # received_tlc_balance
        assert channels["channels"][0]["received_tlc_balance"] == hex(0)

        # created_at
        assert int(channels["channels"][0]["created_at"], 16) / 1000 > begin_time
        assert int(channels["channels"][0]["created_at"], 16) / 1000 < time.time()

        # enabled
        assert channels["channels"][0]["enabled"] is True

        # tlc_expiry_delta
        assert channels["channels"][0]["tlc_expiry_delta"] == hex(14400000)

        # tlc_fee_proportional_millionths
        assert channels["channels"][0]["tlc_fee_proportional_millionths"] == hex(1000)

        # force shutdown latest_commitment_transaction_hash == hash
        self.fiber1.get_client().shutdown_channel(
            {
                "channel_id": channels["channels"][0]["channel_id"],
                "force": True,
            }
        )
        tx_hash = self.wait_and_check_tx_pool_fee(1000, False, 120)
        # assert tx_hash == channels["channels"][0]["latest_commitment_transaction_hash"]
        #
        channel = self.fiber1.get_client().list_channels(
            {
                "peer_id": self.fiber2.get_peer_id(),
            }
        )
        assert len(channel["channels"]) == 0
        channel = self.fiber1.get_client().list_channels(
            {"peer_id": self.fiber2.get_peer_id(), "include_closed": True}
        )
        assert len(channel["channels"]) == 1

    def test_close_channels(self):
        temporary_channel_id = self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
                "funding_udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
            }
        )

        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY", 120
        )
        channels = self.fiber1.get_client().list_channels(
            {"peer_id": self.fiber2.get_peer_id()}
        )
        N1N2_CHANNEL_ID = channels["channels"][0]["channel_id"]
        self.fiber1.get_client().shutdown_channel(
            {
                "channel_id": N1N2_CHANNEL_ID,
                "close_script": self.get_account_script(self.Config.ACCOUNT_PRIVATE_1),
                "fee_rate": "0x3FC",
            }
        )
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_peer_id(), "CLOSED", 120, True
        )
