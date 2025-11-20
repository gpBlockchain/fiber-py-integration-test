import time

import pytest

from framework.basic_fiber import FiberTest


class TestTlcExpiryLimit(FiberTest):

    # @pytest.mark.skip("https://github.com/nervosnetwork/fiber/issues/367")
    def test_tlc_expiry_limit(self):
        """
        - 默认值
        - tlc_expiry_limit 为0
        - 为1
        - 0xffffffffffffffff
        Returns:

        """
        self.fiber3 = self.start_new_fiber(self.generate_account(1000))
        self.fiber3.connect_peer(self.fiber2)
        # open channel between fiber1 and fiber2
        self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(500 * 100000000),
                "public": True,
            }
        )
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY"
        )
        # open channel between fiber2 and fiber3
        self.fiber2.get_client().open_channel(
            {
                "peer_id": self.fiber3.get_peer_id(),
                "funding_amount": hex(500 * 100000000),
                "public": True,
            }
        )
        self.wait_for_channel_state(
            self.fiber2.get_client(), self.fiber3.get_peer_id(), "CHANNEL_READY"
        )
        time.sleep(1)
        # send to fiber3 tlc_expiry_limit: 0x0
        with pytest.raises(Exception) as exc_info:
            self.fiber1.get_client().send_payment(
                {
                    "target_pubkey": self.fiber3.get_client().node_info()["node_id"],
                    "amount": hex(10 * 100000000),
                    "keysend": True,
                    "dry_run": True,
                    "tlc_expiry_limit": "0x0",
                }
            )
        expected_error_message = "tlc_expiry_limit is too small"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

        # tlc_expiry_limit : 172800001
        with pytest.raises(Exception) as exc_info:
            self.fiber1.get_client().send_payment(
                {
                    "target_pubkey": self.fiber2.get_client().node_info()["node_id"],
                    "amount": hex(10 * 100000000),
                    "keysend": True,
                    "dry_run": True,
                    "tlc_expiry_limit": hex(1209600001),
                }
            )
        expected_error_message = "tlc_expiry_limit is too large"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )
        payment = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": self.fiber3.get_client().node_info()["node_id"],
                "amount": hex(10 * 100000000),
                "keysend": True,
                "tlc_expiry_limit": hex(86400000 * 2),
            }
        )
        self.wait_payment_state(self.fiber1, payment["payment_hash"], "Success")

        payment = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": self.fiber3.get_client().node_info()["node_id"],
                "amount": hex(10 * 100000000),
                "keysend": True,
                "tlc_expiry_limit": hex(1209600000),
            }
        )
        self.wait_payment_state(self.fiber1, payment["payment_hash"], "Success")
        # todo how to test final_tlc_expiry_delta time out

        # node1
        channels = self.fiber1.get_client().list_channels({})
        assert channels["channels"][0]["local_balance"] == hex(int(380.98 * 10**8))
        # node3
        channels = self.fiber3.get_client().list_channels({})
        assert channels["channels"][0]["local_balance"] == hex(20 * 10**8)
