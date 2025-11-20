import time

from framework.basic_fiber import FiberTest
from framework.util import ckb_hash


class TestOneToOne(FiberTest):
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 2}

    def test_one_to_one(self):
        self.start_new_fiber(self.generate_account(10000))
        before_balance = self.get_fibers_balance()
        self.open_channel(self.fibers[0], self.fibers[1], 1000 * 100000000, 0)
        self.open_channel(self.fibers[1], self.fibers[2], 1000 * 100000000, 0)
        N = 8
        fiber3_preimages = []
        fiber3_invoices = []
        for i in range(N):
            fiber3_preimage = self.generate_random_preimage()
            fiber3_preimages.append(fiber3_preimage)
            fiber2_invoice = (
                self.fibers[2]
                .get_client()
                .new_invoice(
                    {
                        "amount": hex(100000000),
                        "currency": "Fibd",
                        "description": "test invoice",
                        "payment_hash": ckb_hash(fiber3_preimage),
                    }
                )
            )
            fiber3_invoices.append(fiber2_invoice)
        for i in range(N):
            self.fiber1.get_client().send_payment(
                {
                    "invoice": fiber3_invoices[i]["invoice_address"],
                }
            )
            time.sleep(1)

        self.fibers[0].get_client().shutdown_channel(
            {
                "channel_id": self.fiber1.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "force": True,
            }
        )
        self.fibers[2].get_client().shutdown_channel(
            {
                "channel_id": self.fibers[2]
                .get_client()
                .list_channels({})["channels"][0]["channel_id"],
                "force": True,
            }
        )
        time.sleep(10)
        for i in range(N):
            preimage = fiber3_preimages[i]
            self.fibers[2].get_client().settle_invoice(
                {"payment_hash": ckb_hash(preimage), "payment_preimage": preimage}
            )
        while len(self.get_commit_cells()) > 0:
            for i in range(600):
                self.Miner.miner_with_version(self.node, "0x0")
            time.sleep(10)
        after_balance = self.get_fibers_balance()
        result = self.get_balance_change(before_balance, after_balance)
        assert abs(result[0]["ckb"] - 800800000) < 20000
        assert abs(result[1]["ckb"] + 800000) < 20000
        assert abs(result[2]["ckb"] + 800000000) < 20000
