import time

import pytest

from framework.basic_fiber import FiberTest
from framework.util import ckb_hash

COMMIT_CELL_SWEEP_ROUNDS = 10


class TestShutdownMidNode(FiberTest):
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 3}

    def test_mutil_to_one_udt_2(self):
        """
        aN->b->c
        Returns:
        """
        for i in range(8):
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
        N = 8
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
        payment_hashes = []
        for i in range(N):
            payment = (
                self.new_fibers[i % len(self.new_fibers)]
                .get_client()
                .send_payment(
                    {
                        "invoice": fiber2_invoices[i]["invoice_address"],
                    }
                )
            )
            payment_hashes.append(payment["payment_hash"])
            time.sleep(1)
        for i, payment_hash in enumerate(payment_hashes):
            self.wait_payment_state(
                self.new_fibers[i % len(self.new_fibers)],
                payment_hash,
                "Inflight",
                timeout=60,
            )

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
        for _ in range(COMMIT_CELL_SWEEP_ROUNDS):
            commit_cells = self.get_commit_cells()
            if not commit_cells:
                break
            # self.add_time_and_generate_block(1, 450)
            for i in range(600):
                self.Miner.miner_with_version(self.node, "0x0")
            time.sleep(20)
        else:
            commit_cells = self.get_commit_cells()
            if commit_cells:
                raise TimeoutError(
                    "Commit cells were not consumed after "
                    f"{COMMIT_CELL_SWEEP_ROUNDS} mining rounds: "
                    f"tip={self.node.getClient().get_tip_block_number()}, "
                    f"remaining_commit_cells={commit_cells}"
                )

        # commitment cells 清空后，中继仍需识别链上关闭并向上游传递结算结果。
        # 预留关闭扫描（最长 300 秒）、结算检查和消息传播时间；不重发付款。
        deadline = time.monotonic() + 420
        while True:
            payments = [
                sender.get_client().get_payment({"payment_hash": payment_hash})
                for sender, payment_hash in zip(self.new_fibers, payment_hashes)
            ]
            upstream_channels = [
                sender.get_client().list_channels({"pubkey": self.fiber1.get_pubkey()})[
                    "channels"
                ]
                for sender in self.new_fibers
            ]
            assert all(payment["status"] != "Failed" for payment in payments), payments
            if all(payment["status"] == "Success" for payment in payments) and all(
                len(channels) == 1
                and int(channels[0]["offered_tlc_balance"], 16) == 0
                and int(channels[0]["received_tlc_balance"], 16) == 0
                for channels in upstream_channels
            ):
                break
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Upstream UDT payments did not settle: {payments}; "
                    f"channels={upstream_channels}"
                )
            time.sleep(1)

        for fiber in self.new_fibers:
            channels = fiber.get_client().list_channels(
                {"pubkey": self.fiber1.get_pubkey()}
            )["channels"]
            for channel in channels:
                if channel["state"]["state_name"] == "Closed":
                    continue
                fiber.get_client().shutdown_channel(
                    {
                        "channel_id": channel["channel_id"],
                        "close_script": self.get_account_script(fiber.account_private),
                        "fee_rate": "0x3FC",
                    }
                )
                # 后台矿工持续出块，按通道等待关闭，不捕捉全局 pending 瞬时状态。
                self.wait_for_channel_state(
                    fiber.get_client(),
                    self.fiber1.get_pubkey(),
                    "Closed",
                    120,
                    True,
                    channel["channel_id"],
                )
        after_balance = self.get_fibers_balance()
        result = self.get_balance_change(before_balance, after_balance)
        assert result[0]["udt"] == -800000
        assert result[1]["udt"] == -800000000
        for i in range(2, 10):
            assert result[i]["udt"] == 100100000
