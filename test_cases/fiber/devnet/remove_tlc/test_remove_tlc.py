import time

from framework.basic_fiber import FiberTest
from framework.test_fiber import FiberConfigPath


class TestRemoveTlc(FiberTest):

    fiber_version = FiberConfigPath.CURRENT_DEV_DEBUG

    def test_removetlcfail(self):
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1)

        # add tlc node1
        CHANNEL_ID1 = self.fiber1.get_client().list_channels({})["channels"][0][
            "channel_id"
        ]
        tlc = self.fiber1.get_client().add_tlc(
            {
                "channel_id": CHANNEL_ID1,
                "amount": hex(300 * 100000000),
                # "payment_hash": invoice_list[i]["invoice"]["data"]["payment_hash"],
                "payment_hash": "0x266cec97cbede2cfbce73666f08deed9560bdf7841a7a5a51b3a3f09da249e21",
                "expiry": hex((int(time.time()) + 3600) * 1000),
            }
        )

        time.sleep(2)
        self.fiber2.get_client().remove_tlc(
            {
                "channel_id": CHANNEL_ID1,
                "tlc_id": tlc["tlc_id"],
                "reason": {"error_code": "IncorrectOrUnknownPaymentDetails"},
            }
        )
        time.sleep(2)
        channels = self.fiber1.get_client().list_channels({})
        assert channels["channels"][0]["offered_tlc_balance"] == "0x0"
        assert channels["channels"][0]["received_tlc_balance"] == "0x0"
