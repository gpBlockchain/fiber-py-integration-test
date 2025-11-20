import time

import pytest

from framework.basic_fiber import FiberTest


class TestAmount(FiberTest):

    def test_ckb(self):
        """
        10 亿的chanel

        1. send zero to n2
        2. send zero to n3

        3. send 1 to n2
        4. send 1 to n3

        5. send local_balance to n2
        6. send local_balance to n3

        7. send amount > local_balance to n2
        8. send amount > local_balance to n3

        9. send u128 max to node2
        10. send  u128 max to node3

        Returns:

        """
        self.fiber3 = self.start_new_fiber(self.generate_account(1000))
        self.fiber3.connect_peer(self.fiber2)
        # open very big channel for n1-n2
        self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex((1000000000-36) * 100000000 ),
                "public": True,
            }
        )
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY"
        )
        # open very big channel for n2-n3
        self.fiber2.get_client().open_channel(
            {
                "peer_id": self.fiber3.get_peer_id(),
                "funding_amount": hex((1000000000-36) * 100000000),
                "public": True,
            }
        )
        self.wait_for_channel_state(
            self.fiber3.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY"
        )
        time.sleep(1)

        # send amount ： hex(0)
        with pytest.raises(Exception) as exc_info:
            self.fiber1.get_client().send_payment(
                {
                    "target_pubkey": self.fiber2.get_client().node_info()["node_id"],
                    "amount": hex(0),
                    "keysend": True,
                    "dry_run": True,
                }
            )
        expected_error_message = "amount must be greater than 0"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

        with pytest.raises(Exception) as exc_info:
            self.fiber1.get_client().send_payment(
                {
                    "target_pubkey": self.fiber3.get_client().node_info()["node_id"],
                    "amount": hex(0),
                    "keysend": True,
                    "dry_run": True,
                }
            )
        expected_error_message = "amount must be greater than 0"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

        # send amount ： hex(1)
        payment = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": self.fiber2.get_client().node_info()["node_id"],
                "amount": hex(1),
                "keysend": True,
                "dry_run": True,
            }
        )
        assert payment["fee"] == hex(0)
        payment = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": self.fiber3.get_client().node_info()["node_id"],
                "amount": hex(1),
                "keysend": True,
                "dry_run": True,
            }
        )
        assert payment["fee"] == hex(1)

        # send amount ： local_balance
        # todo expected success
        # current max = local_balance / 1.001
        # channels = self.fiber1.get_client().list_channels({})
        # payment = self.fiber1.get_client().send_payment({
        #     "target_pubkey": self.fiber2.get_client().node_info()['public_key'],
        #     "amount": channels[0]['local_balance'],
        #     "keysend": True,
        #     "dry_run": True,
        # })
        channels = self.fiber1.get_client().list_channels({})
        payment = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": self.fiber2.get_client().node_info()["node_id"],
                "amount": hex(
                    int(int(channels["channels"][0]["local_balance"], 16) / 1.001)
                ),
                "keysend": True,
                "dry_run": True,
            }
        )

        # current send to fiber3  max  = local_balance /  1.002001
        # todo expected max  = local_balance /  1.001
        payment = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": self.fiber3.get_client().node_info()["node_id"],
                "amount": hex(
                    int(int(channels["channels"][0]["local_balance"], 16) / 1.002001)
                ),
                "keysend": True,
                "dry_run": True,
            }
        )
        assert payment["fee"] == "0x5ac4909beb3c"

        # with pytest.raises(Exception) as exc_info:
        #     self.fiber1.get_client().send_payment(
        #         {
        #             "target_pubkey": self.fiber3.get_client().node_info()["node_id"],
        #             "amount": hex(
        #                 int(int(channels["channels"][0]["local_balance"], 16) / 1.001)
        #             ),
        #             "keysend": True,
        #             "dry_run": True,
        #         }
        #     )
        # expected_error_message = "no path found"
        # assert expected_error_message in exc_info.value.args[0], (
        #     f"Expected substring '{expected_error_message}' "
        #     f"not found in actual string '{exc_info.value.args[0]}'"
        # )
        payment = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": self.fiber3.get_client().node_info()["node_id"],
                "amount": hex(
                    int(int(channels["channels"][0]["local_balance"], 16) / 1.00101)
                ),
                "keysend": True,
            }
        )
        self.wait_payment_state(self.fiber1, payment["payment_hash"], "Success")

    def test_over_flow_panic(self):
        self.fiber3 = self.start_new_fiber(self.generate_account(1000))
        self.fiber3.connect_peer(self.fiber2)
        # open very big channel for n1-n2
        self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(1000000000 * 100000000),
                "public": True,
            }
        )
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY"
        )
        # open very big channel for n2-n3
        self.fiber2.get_client().open_channel(
            {
                "peer_id": self.fiber3.get_peer_id(),
                "funding_amount": hex(1000000000 * 100000000),
                "public": True,
            }
        )
        self.wait_for_channel_state(
            self.fiber3.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY"
        )
        time.sleep(1)

        # amount : 0xfffffffffffffffffffffffffffffff
        channels = self.fiber1.get_client().list_channels({})
        with pytest.raises(Exception) as exc_info:
            payment = self.fiber1.get_client().send_payment(
                {
                    "target_pubkey": self.fiber3.get_client().node_info()["node_id"],
                    "amount": "0xfffffffffffffffffffffffffffffff",
                    "keysend": True,
                    "dry_run": True,
                }
            )
        expected_error_message = "should be less than"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

    # @pytest.mark.skip("https://github.com/nervosnetwork/fiber/issues/359")
    def test_send_mutil_channel(self):
        # open channel a1-b1
        self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
            }
        )
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY"
        )
        self.wait_for_channel_state(
            self.fiber2.get_client(), self.fiber1.get_peer_id(), "CHANNEL_READY"
        )
        self.fiber2.get_client().open_channel(
            {
                "peer_id": self.fiber1.get_peer_id(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
            }
        )
        time.sleep(2)
        self.wait_for_channel_state(
            self.fiber2.get_client(), self.fiber1.get_peer_id(), "CHANNEL_READY"
        )
        time.sleep(1)
        self.fiber1.get_client().send_payment(
            {
                "target_pubkey": self.fiber2.get_client().node_info()["node_id"],
                "amount": hex(100 * 100000000),
                "keysend": True,
                "dry_run": True,
            }
        )
        payment1 = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": self.fiber2.get_client().node_info()["node_id"],
                "amount": hex(100 * 100000000),
                "keysend": True,
            }
        )
        self.wait_payment_state(self.fiber1, payment1["payment_hash"], "Success")
        self.fiber2.get_client().send_payment(
            {
                "target_pubkey": self.fiber1.get_client().node_info()["node_id"],
                "amount": hex(200 * 100000000),
                "keysend": True,
                "dry_run": True,
            }
        )
        payment2 = self.fiber2.get_client().send_payment(
            {
                "target_pubkey": self.fiber1.get_client().node_info()["node_id"],
                "amount": hex(200 * 100000000),
                "keysend": True,
            }
        )
        self.wait_payment_state(self.fiber2, payment2["payment_hash"], "Success")

    def test_udt(self):
        """
        open_channel 1亿亿

        Returns:

        """
        # todo use 10000000000000000
        open_chanel_balance = 10000000000000000 * 100000000
        # open_chanel_balance = 1000000000 * 100000000
        self.faucet(
            self.fiber2.account_private,
            0,
            self.fiber1.account_private,
            open_chanel_balance,
        )
        self.faucet(
            self.fiber1.account_private,
            0,
            self.fiber1.account_private,
            open_chanel_balance,
        )
        self.fiber3 = self.start_new_fiber(self.generate_account(1000))
        self.fiber2.connect_peer(self.fiber3)
        # open very big channel for n1-n2
        self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(open_chanel_balance),
                "funding_udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                "public": True,
            }
        )
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY"
        )
        # open very big channel for n2-n3
        self.fiber2.get_client().open_channel(
            {
                "peer_id": self.fiber3.get_peer_id(),
                "funding_amount": hex(open_chanel_balance),
                "funding_udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                "public": True,
            }
        )
        self.wait_for_channel_state(
            self.fiber3.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY"
        )
        time.sleep(1)

        # send amount ： hex(0)
        with pytest.raises(Exception) as exc_info:
            self.fiber1.get_client().send_payment(
                {
                    "target_pubkey": self.fiber2.get_client().node_info()["node_id"],
                    "amount": hex(0),
                    "keysend": True,
                    "dry_run": True,
                    "udt_type_script": self.get_account_udt_script(
                        self.fiber1.account_private
                    ),
                }
            )
        expected_error_message = "amount must be greater than 0"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

        with pytest.raises(Exception) as exc_info:
            self.fiber1.get_client().send_payment(
                {
                    "target_pubkey": self.fiber3.get_client().node_info()["node_id"],
                    "amount": hex(0),
                    "keysend": True,
                    "dry_run": True,
                    "udt_type_script": self.get_account_udt_script(
                        self.fiber1.account_private
                    ),
                }
            )
        expected_error_message = "amount must be greater than 0"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

        # send amount ： hex(1)
        payment = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": self.fiber2.get_client().node_info()["node_id"],
                "amount": hex(1),
                "keysend": True,
                "dry_run": True,
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
            }
        )
        assert payment["fee"] == hex(0)
        payment = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": self.fiber3.get_client().node_info()["node_id"],
                "amount": hex(1),
                "keysend": True,
                "dry_run": True,
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
            }
        )
        assert payment["fee"] == hex(1)
        channels = self.fiber1.get_client().list_channels({})
        # send amount ： local_balance
        # todo expected success
        # payment = self.fiber1.get_client().send_payment({
        #     "target_pubkey": self.fiber2.get_client().node_info()['public_key'],
        #     "amount": channels['channels'][0]['local_balance'],
        #     "keysend": True,
        #     "dry_run": True,
        #     "udt_type_script": self.get_account_udt_script(self.fiber1.account_private)
        # })
        # node1 send to node2 current max = local_balance / 1.001
        payment = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": self.fiber2.get_client().node_info()["node_id"],
                "amount": hex(
                    int(int(channels["channels"][0]["local_balance"], 16) / 1.00101)
                ),
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                "keysend": True,
                "dry_run": True,
            }
        )
        assert payment["fee"] == "0x0"
        # node1 send to node3 current max = local_balance / 1.002001
        # todo max = local_balance / 1.001
        payment = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": self.fiber3.get_client().node_info()["node_id"],
                "amount": hex(
                    int(int(channels["channels"][0]["local_balance"], 16) / 1.00101)
                ),
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                "keysend": True,
                "dry_run": True,
            }
        )
        assert payment["fee"] == "0x3627c90ef6acdb22d1"
        # int max
        # with pytest.raises(Exception) as exc_info:
        # payment = self.fiber1.get_client().send_payment({
        #     "target_pubkey": self.fiber3.get_client().node_info()['public_key'],
        #     "amount": "0xfffffffffffffffffffffffffffffff",
        #     "keysend": True,
        #     "dry_run": True,
        #     "udt_type_script": self.get_account_udt_script(self.fiber1.account_private),
        #
        # })
        # expected_error_message = "route"
        # assert expected_error_message in exc_info.value.args[0], (
        #     f"Expected substring '{expected_error_message}' "
        #     f"not found in actual string '{exc_info.value.args[0]}'"
        # )

        # channels = self.fiber1.get_client().list_channels({})
        # payment = self.fiber1.get_client().send_payment({
        #     "target_pubkey": self.fiber3.get_client().node_info()['public_key'],
        #     "amount": "0xfffffffffffffffffffffffffffffff",
        #     "keysend": True,
        #     "dry_run": True,
        # })
        # self.fiber1.get_client().list_channels({})
