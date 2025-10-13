import time

import pytest

from framework.basic_fiber import FiberTest


class TestIssue675(FiberTest):

    def test_shutdown_in_tlc(self):
        """"""
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1000 * 100000000)
        payments = []
        for i in range(30):
            payment = self.send_payment(self.fiber1, self.fiber2, 1 * 100000000, False)
            payments.append(payment)
        for i in range(10):
            try:
                self.fiber1.get_client().shutdown_channel(
                    {
                        "channel_id": self.fiber1.get_client().list_channels({})[
                            "channels"
                        ][0]["channel_id"],
                        "force": False,
                        "close_script": self.get_account_script(
                            self.fiber1.account_private
                        ),
                        "fee_rate": "0x3FC",
                    }
                )
                break
            except Exception:
                time.sleep(1)

        for payment_hash in payments:
            self.wait_payment_finished(self.fiber1, payment_hash)

        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_peer_id(), "CLOSED", 30, True
        )
