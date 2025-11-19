import time

from framework.basic_fiber import FiberTest
from framework.util import ckb_hash


class TestShutdownMidNode(FiberTest):
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 5}

    def test_mutil_to_one_udt_2(self):
        """
        aN->b->c
        Returns:
        """
        for i in range(10):
            self.start_new_fiber(
                self.generate_account(
                    10000, self.fiber1.account_private, 10000 * 100000000
                )
            )
        self.faucet(
            self.fiber1.account_private,
            0,
            self.fiber1.account_private,
            10000 * 100000000,
        )
        before_balance = self.get_fibers_balance()
        self.open_channel(
            self.fiber1,
            self.fiber2,
            1000 * 100000000,
            0,
            udt=self.get_account_udt_script(self.fiber1.account_private),
        )
        for i in range(len(self.new_fibers)):
            self.open_channel(
                self.new_fibers[i],
                self.fiber1,
                1000 * 100000000,
                0,
                udt=self.get_account_udt_script(self.fiber1.account_private),
            )

        fiber2_preimages = []
        fiber2_invoices = []
        N = 30
        for i in range(N):
            fiber2_preimage = self.generate_random_preimage()
            fiber2_preimages.append(fiber2_preimage)
            fiber2_invoice = self.fiber2.get_client().new_invoice(
                {
                    "amount": hex(100000000),
                    "currency": "Fibd",
                    "description": "test invoice",
                    "payment_hash": ckb_hash(fiber2_preimage),
                    "udt_type_script": self.get_account_udt_script(
                        self.fiber1.account_private
                    ),
                }
            )
            fiber2_invoices.append(fiber2_invoice)
        for i in range(N):
            self.new_fibers[i % len(self.new_fibers)].get_client().send_payment(
                {
                    "invoice": fiber2_invoices[i]["invoice_address"],
                }
            )
            time.sleep(1)

        self.fiber2.get_client().shutdown_channel(
            {
                "channel_id": self.fiber2.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "force": True,
            }
        )
        time.sleep(10)
        for i in range(N):
            preimage = fiber2_preimages[i]
            self.fiber2.get_client().settle_invoice(
                {"payment_hash": ckb_hash(preimage), "payment_preimage": preimage}
            )
        while len(self.get_commit_cells()) > 0:
            # self.add_time_and_generate_block(1, 450)
            for i in range(600):
                self.Miner.miner_with_version(self.node, "0x0")
            time.sleep(10)

        for channels in self.fiber1.get_client().list_channels({})["channels"]:
            try:
                self.fiber1.get_client().shutdown_channel(
                    {
                        "channel_id": channels["channel_id"],
                        "close_script": self.get_account_script(
                            self.Config.ACCOUNT_PRIVATE_1
                        ),
                        "fee_rate": "0x3FC",
                    }
                )
                tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
                self.Miner.miner_until_tx_committed(self.node, tx_hash)
            except Exception as e:
                pass
        after_balance = self.get_fibers_balance()
        result = self.get_balance_change(before_balance, after_balance)
        assert result[0]["udt"] == -800000
        assert result[1]["udt"] == -800000000
        for i in range(2, 10):
            assert result[i]["udt"] == 100100000
