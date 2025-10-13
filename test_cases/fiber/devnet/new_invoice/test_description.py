import time

import pytest

from framework.basic_fiber import FiberTest


class TestDescription(FiberTest):
    """
    - none
    - 随机值 英文字母 特殊符号 表情包🤔️
    - 特别长1mb
    """

    # FiberTest.debug = True

    # def test_find_max_description(self):
    #
    #     # rd_str = self.generate_random_str(5918577)
    #     low, high = 5018577, 5918577
    #     while low <= high:
    #         mid = (low + high) // 2
    #         try:
    #             rd_str = self.generate_random_str(mid)
    #             invoice = self.fiber1.get_client().new_invoice({
    #                 "amount": hex(1),
    #                 "currency": "Fibd",
    #                 "description": rd_str,
    #                 "expiry": "0xe10",
    #                 "final_cltv": "0x28",
    #                 "payment_preimage": self.generate_random_preimage(),
    #                 "hash_algorithm": "sha256",
    #             })
    #             get_invoice = self.fiber1.get_client().get_invoice({
    #                 "payment_hash": invoice["invoice"]["data"]["payment_hash"]
    #             })
    #             assert get_invoice["invoice"]["data"]["attrs"][0]["Description"] == rd_str
    #             low = mid + 1
    #         except Exception:
    #             high = mid - 1
    #     print("Maximum length:", high)

    # @pytest.mark.skip("https://github.com/nervosnetwork/fiber/issues/312")
    def test_description_max_data(self):
        # 1. open channel
        temporary_channel = self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
            }
        )

        # 2. check channel state
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY"
        )
        rand_str_length = 639

        # 3. new invoice large description
        amount = 1
        rand_str = self.generate_random_str(rand_str_length)
        with pytest.raises(Exception) as exc_info:
            invoice = self.fiber2.get_client().new_invoice(
                {
                    "amount": hex(amount),
                    "currency": "Fibd",
                    "description": rand_str,
                    "expiry": "0xe10",
                    "final_cltv": "0x28",
                    "payment_preimage": self.generate_random_preimage(),
                    "hash_algorithm": "sha256",
                }
            )
        expected_error_message = (
            "Description with length of 641 is too long, max length is 639"
        )
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

    def test_description_rd_str(self):
        """
        1. empty
        2. " "
        3. "😊"

        Returns:

        """

        # 1. open channel
        temporary_channel = self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
            }
        )

        # 2. check channel state
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY"
        )
        rand_str_length = 1024
        test_description_str = ["", " ", "sa😧"]
        for i in range(len(test_description_str)):
            # 3. new invoice amount == 1
            amount = 1
            rand_str_length = rand_str_length * 2
            rand_str = self.generate_random_str(rand_str_length)
            invoice = self.fiber2.get_client().new_invoice(
                {
                    "amount": hex(amount),
                    "currency": "Fibd",
                    "description": test_description_str[i],
                    "expiry": "0xe10",
                    "final_cltv": "0x28",
                    "payment_preimage": self.generate_random_preimage(),
                    "hash_algorithm": "sha256",
                }
            )
            time.sleep(1)

            # 4. List channels before sending payment
            before_channel = self.fiber1.get_client().list_channels({})

            # 5. Send payment using the created invoice
            payment = self.fiber1.get_client().send_payment(
                {
                    "invoice": invoice["invoice_address"],
                }
            )

            # 6. Verify the payment and invoice states
            self.wait_payment_state(self.fiber1, payment["payment_hash"], "Success")
            self.wait_invoice_state(self.fiber2, payment["payment_hash"], "Paid")
            invoice = self.fiber2.get_client().get_invoice(
                {"payment_hash": payment["payment_hash"]}
            )
            assert (
                invoice["invoice"]["data"]["attrs"][0]["description"]
                == test_description_str[i]
            )
            # 7. List channels after sending payment
            after_channel = self.fiber1.get_client().list_channels({})

            # 8. Assert the local balance is correctly updated
            assert (
                int(before_channel["channels"][0]["local_balance"], 16)
                == int(after_channel["channels"][0]["local_balance"], 16) + amount
            )
