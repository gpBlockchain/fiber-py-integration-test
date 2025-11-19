import time

import pytest

from framework.basic_fiber import FiberTest
from framework.config import DEFAULT_MIN_DEPOSIT_CKB


class FundingAmount(FiberTest):
    # FiberTest.debug = True
    start_fiber_config = {"fiber_auto_accept_amount": "0"}

    def test_funding_amount_ckb_is_zero(self):
        """
        1. funding_udt_type_script is None ,funding_amount = 0
        Returns:
        """
        with pytest.raises(Exception) as exc_info:
            temporary_channel_id = self.fiber1.get_client().open_channel(
                {
                    "peer_id": self.fiber2.get_peer_id(),
                    "funding_amount": hex(0),
                    "public": True,
                    # "tlc_fee_proportional_millionths": "0x4B0",
                }
            )
        expected_error_message = (
            "The funding amount (0) should be greater than or equal to"
        )
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

    @pytest.mark.skip("仅 dev 报错 script not found")
    def test_funding_amount_udt_is_zero(self):
        temporary_channel_id = self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(0),
                "public": True,
                "funding_udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                # "tlc_fee_proportional_millionths": "0x4B0",
            }
        )
        time.sleep(1)
        # self.fiber2.get_client().accept_channel(
        #     {
        #         "temporary_channel_id": temporary_channel_id["temporary_channel_id"],
        #         "funding_amount": "0x0",
        #     }
        # )
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY", 120
        )

    def test_funding_amount_ckb_lt_62(self):
        """
        1. funding_udt_type_script is None ,funding_amount < 62
        Returns:
        """
        """
                1. funding_udt_type_script is None ,funding_amount = 0
                Returns:
                """
        with pytest.raises(Exception) as exc_info:
            temporary_channel_id = self.fiber1.get_client().open_channel(
                {
                    "peer_id": self.fiber2.get_peer_id(),
                    "funding_amount": hex(DEFAULT_MIN_DEPOSIT_CKB - 1),
                    "public": True,
                    # "tlc_fee_proportional_millionths": "0x4B0",
                }
            )
        expected_error_message = "should be greater than or equal to"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

    def test_funding_amount_ckb_0xfffffffffffffffffffffffffffffff(self):
        """
        1. funding_udt_type_script is None ,funding_amount > account balance
        Returns:
        0xfffffffffffffffffffffffffffffff
        """

        capacity = self.Ckb_cli.wallet_get_capacity(self.account2["address"]["testnet"])
        with pytest.raises(Exception) as exc_info:
            temporary_channel_id = self.fiber1.get_client().open_channel(
                {
                    "peer_id": self.fiber2.get_peer_id(),
                    "funding_amount": "0xfffffffffffffffffffffffffffffff",
                    "public": True,
                    # "tlc_fee_proportional_millionths": "0x4B0",
                }
            )
        expected_error_message = "The funding amount (21267647932558653966460912964485513215) should be less than 18446744073709551615"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

    def test_funding_amount_udt_0xfffffffffffffffffffffffffffffffffffff(self):
        """
        1. funding_udt_type_script is None ,funding_amount > account balance
        Returns:
        0xfffffffffffffffffffffffffffffff
        """

        capacity = self.Ckb_cli.wallet_get_capacity(self.account2["address"]["testnet"])
        with pytest.raises(Exception) as exc_info:
            temporary_channel_id = self.fiber1.get_client().open_channel(
                {
                    "peer_id": self.fiber2.get_peer_id(),
                    "funding_amount": "0xfffffffffffffffffffffffffffffffffffffffffffff",
                    "public": True,
                    "funding_udt_type_script": self.get_account_udt_script(
                        self.fiber1.account_private
                    ),
                    # "tlc_fee_proportional_millionths": "0x4B0",
                }
            )
        expected_error_message = "Invalid params"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

    def test_funding_amount_udt_0xfffffffffffffffffffffffffffffff(self):
        """
        1. funding_udt_type_script is None ,funding_amount > account balance
        Returns:
        0xfffffffffffffffffffffffffffffff
        """
        self.faucet(
            self.fiber1.account_private,
            0,
            self.fiber1.account_private,
            int("0xfffffffffffffffffffffffffffffff", 16) - 1000,
        )
        capacity = self.Ckb_cli.wallet_get_capacity(self.account2["address"]["testnet"])
        # with pytest.raises(Exception) as exc_info:
        temporary_channel_id = self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                # "funding_amount": "0xfffffffffffffffffffffffffffffff",
                "funding_amount": hex(
                    int("0xfffffffffffffffffffffffffffffff", 16) - 1000
                ),
                "public": True,
                "funding_udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                # "tlc_fee_proportional_millionths": "0x4B0",
            }
        )
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY"
        )

    def test_funding_amount_udt_gt_account_balance(self):
        account3_private = self.generate_account(
            1000, self.fiber1.account_private, 1000 * 100000000
        )
        self.fiber3 = self.start_new_fiber(account3_private)
        self.fiber3.connect_peer(self.fiber1)
        time.sleep(1)
        temporary_channel_id = self.fiber3.get_client().open_channel(
            {
                "peer_id": self.fiber1.get_peer_id(),
                "funding_amount": hex(1000 * 100000000 + 1),
                "public": True,
                "funding_udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                # "tlc_fee_proportional_millionths": "0x4B0",
            }
        )
        time.sleep(3)
        # self.wait_for_channel_state(
        #     self.fiber3.get_client(),
        #     self.fiber1.get_peer_id(),
        #     "NEGOTIATING_FUNDING",
        #     120,
        # )
        channels = self.fiber3.get_client().list_channels({})
        assert len(channels["channels"]) == 0
        channels = self.fiber1.get_client().list_channels({})
        assert len(channels["channels"]) == 0

    def test_funding_amount_ckb_gt_account_balance(self):
        """
        1. funding_udt_type_script is None ,funding_amount > account balance
        Returns:
            status : NEGOTIATING_FUNDING
        """

        capacity = self.Ckb_cli.wallet_get_capacity(self.account2["address"]["testnet"])
        temporary_channel_id = self.fiber2.get_client().open_channel(
            {
                "peer_id": self.fiber1.get_peer_id(),
                "funding_amount": hex(int(capacity) * 100000000 * 2),
                "public": True,
                # "tlc_fee_proportional_millionths": "0x4B0",
            }
        )
        self.wait_for_channel_state(
            self.fiber2.get_client(),
            self.fiber1.get_peer_id(),
            "NEGOTIATING_FUNDING",
            120,
        )

    def test_funding_amount_ckb_eq_account_balance(self):
        """
        1. funding_udt_type_script is None ,funding_amount == account balance
        Returns:
        """
        capacity = self.Ckb_cli.wallet_get_capacity(self.account2["address"]["testnet"])
        temporary_channel_id = self.fiber2.get_client().open_channel(
            {
                "peer_id": self.fiber1.get_peer_id(),
                "funding_amount": hex(int(capacity) * 100000000),
                "public": True,
                # "tlc_fee_proportional_millionths": "0x4B0",
            }
        )
        self.wait_for_channel_state(
            self.fiber2.get_client(),
            self.fiber1.get_peer_id(),
            "NEGOTIATING_FUNDING",
            120,
        )

    def test_funding_amount_udt_eq_account_balance(self):
        account3_private = self.generate_account(
            1000, self.fiber1.account_private, 1000 * 100000000
        )
        self.fiber3 = self.start_new_fiber(account3_private)
        self.fiber3.connect_peer(self.fiber1)
        time.sleep(1)
        temporary_channel_id = self.fiber3.get_client().open_channel(
            {
                "peer_id": self.fiber1.get_peer_id(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
                "funding_udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                # "tlc_fee_proportional_millionths": "0x4B0",
            }
        )
        self.wait_for_channel_state(
            self.fiber3.get_client(),
            self.fiber1.get_peer_id(),
            "CHANNEL_READY",
            120,
        )

    def test_funding_amount_udt_lt_account_balance(self):
        account3_private = self.generate_account(
            1000, self.fiber1.account_private, 1000 * 100000000
        )
        self.fiber3 = self.start_new_fiber(account3_private)
        self.fiber3.connect_peer(self.fiber1)
        time.sleep(1)
        temporary_channel_id = self.fiber3.get_client().open_channel(
            {
                "peer_id": self.fiber1.get_peer_id(),
                "funding_amount": hex(1000 * 100000000 - 1),
                "public": True,
                "funding_udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                # "tlc_fee_proportional_millionths": "0x4B0",
            }
        )
        self.wait_for_channel_state(
            self.fiber3.get_client(),
            self.fiber1.get_peer_id(),
            "CHANNEL_READY",
            120,
        )
        account_balance = self.udtContract.list_cell(
            self.node.getClient(),
            self.get_account_script(self.fiber1.account_private)["args"],
            self.get_account_script(self.fiber3.account_private)["args"],
        )
        print(account_balance)
        assert account_balance[0]["balance"] == 1

    @pytest.mark.skip("todo")
    def test_funding_amount_ckb_lt_account_balance(self):
        """
        1. funding_udt_type_script is None ,funding_amount < account balance
        Returns:
        """
        pass
        # self.test_linked_peer()

    def test_funding_amount_gt_int_max(self):
        """
        funding_amount > int.max
        Args:
            self:
        Returns:
        """

        #  The funding amount should be less than 18446744073709551615
        with pytest.raises(Exception) as exc_info:
            temporary_channel_id = self.fiber1.get_client().open_channel(
                {
                    "peer_id": self.fiber2.get_peer_id(),
                    "funding_amount": hex(18446744073709551616),
                    "public": True,
                    # "tlc_fee_proportional_millionths": "0x4B0",
                }
            )
        expected_error_message = "The funding amount (18446744073709551616) should be less than 18446744073709551615"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )
