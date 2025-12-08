import time

from framework.basic_fiber import FiberTest
from framework.util import ckb_hash


class TestMutilToOneHandle(FiberTest):
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 2}

    def teardown_class(cls):
        cls.restore_time()
        super().teardown_class()

    def test_mutil_to_only_c_shutdown(self):
        """
        aN->b->c
        Returns:
        """
        for i in range(10):
            self.start_new_fiber(self.generate_account(10000))
        before_balance = self.get_fibers_balance()

        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0)
        for i in range(len(self.new_fibers)):
            self.open_channel(self.new_fibers[i], self.fiber1, 1000 * 100000000, 0)

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
        self.fiber2.get_client().disconnect_peer(
            {
                "peer_id": self.fiber1.get_peer_id(),
            }
        )
        self.add_time_and_generate_block(22, 100)
        while len(self.get_commit_cells()) == 0:
            self.add_time_and_generate_block(1, 100)
            time.sleep(20)
        for i in range(N):
            preimage = fiber2_preimages[i]
            self.fiber2.get_client().settle_invoice(
                {"payment_hash": ckb_hash(preimage), "payment_preimage": preimage}
            )
        self.add_time_and_generate_block(1, 600)
        time.sleep(10)
        while (
            self.node.getClient().get_tip_block_number()
            - self.get_latest_commit_tx_number()
            < 20
        ):
            time.sleep(5)
        while len(self.get_commit_cells()) > 0:
            self.add_time_and_generate_block(24, 450)
            time.sleep(60)

        after_balance = self.get_fibers_balance()
        result = self.get_balance_change(before_balance, after_balance)
        assert abs(result[1]["ckb"] + 8 * 100000000) < 20000
        for fiber in self.new_fibers:
            list_channel = fiber.get_client().list_channels({})
            assert list_channel["channels"][0]["offered_tlc_balance"] == "0x0"

    def test_mutil_to_one(self):
        """
        aN->b->c
        Returns:
        """
        for i in range(10):
            self.start_new_fiber(self.generate_account(10000))
        before_balance = self.get_fibers_balance()

        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0)
        for i in range(len(self.new_fibers)):
            self.open_channel(self.new_fibers[i], self.fiber1, 1000 * 100000000, 0)

        fiber2_preimages = []
        fiber2_invoices = []
        N = 130
        for i in range(N):
            fiber2_preimage = self.generate_random_preimage()
            fiber2_preimages.append(fiber2_preimage)
            fiber2_invoice = self.fiber2.get_client().new_invoice(
                {
                    "amount": hex(100000000),
                    "currency": "Fibd",
                    "description": "test invoice",
                    "payment_hash": ckb_hash(fiber2_preimage),
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
        self.fiber2.get_client().disconnect_peer(
            {
                "peer_id": self.fiber1.get_peer_id(),
            }
        )
        self.add_time_and_generate_block(22, 100)

        while len(self.get_commit_cells()) == 0:
            self.add_time_and_generate_block(1, 100)
            time.sleep(20)
        for channels in self.fiber1.get_client().list_channels({})["channels"]:
            try:
                self.fiber1.get_client().shutdown_channel(
                    {"channel_id": channels["channel_id"], "force": True}
                )
            except Exception as e:
                pass
        time.sleep(10)
        for i in range(N):
            preimage = fiber2_preimages[i]
            self.fiber2.get_client().settle_invoice(
                {"payment_hash": ckb_hash(preimage), "payment_preimage": preimage}
            )
        self.node.getClient().generate_epochs("0x1")
        time.sleep(10)
        while (
            self.node.getClient().get_tip_block_number()
            - self.get_latest_commit_tx_number()
            < 20
        ):
            time.sleep(5)
        while len(self.get_commit_cells()) > 0:
            self.add_time_and_generate_block(24, 600)
            time.sleep(5)
            current_get_latest_commit_tx_number = self.get_latest_commit_tx_number()
            current_tip_block_number = self.node.getClient().get_tip_block_number()
            while current_tip_block_number - current_get_latest_commit_tx_number < 10:
                print(
                    f"current_get_latest_commit_tx_number:{current_get_latest_commit_tx_number},current_tip_block_number:{current_tip_block_number}"
                )
                time.sleep(5)
                current_get_latest_commit_tx_number = self.get_latest_commit_tx_number()
                current_tip_block_number = self.node.getClient().get_tip_block_number()
        after_balance = self.get_fibers_balance()
        result = self.get_balance_change(before_balance, after_balance)
        assert result[0]["ckb"] < 0
        # assert abs(result[1]["ckb"] + 125 * 100000000) < 1 * 100000000
        ckb_fee = 0
        for rt in result:
            ckb_fee += rt["ckb"]
        assert ckb_fee < 1 * 100000000

    def test_mutil_to_one_after_23h_settle(self):
        for i in range(10):
            self.start_new_fiber(self.generate_account(10000))
        before_balance = self.get_fibers_balance()

        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0)
        for i in range(len(self.new_fibers)):
            self.open_channel(self.new_fibers[i], self.fiber1, 1000 * 100000000, 0)

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
        self.fiber2.get_client().disconnect_peer(
            {
                "peer_id": self.fiber1.get_peer_id(),
            }
        )
        add_time = 0
        # 等待强制触发 强制shutdown
        while len(self.get_commit_cells()) == 0:
            self.add_time_and_generate_block(1, 100)
            add_time += 1
            time.sleep(20)
        for channels in self.fiber1.get_client().list_channels({})["channels"]:
            try:
                self.fiber1.get_client().shutdown_channel(
                    {"channel_id": channels["channel_id"], "force": True}
                )
            except Exception as e:
                pass
        # 等到23h的时候再 settle_invoice
        while add_time < 23:
            self.add_time_and_generate_block(1, 600)
            time.sleep(10)
            add_time += 1
        time.sleep(10)
        for i in range(N):
            preimage = fiber2_preimages[i]
            self.fiber2.get_client().settle_invoice(
                {"payment_hash": ckb_hash(preimage), "payment_preimage": preimage}
            )
        while len(self.get_commit_cells()) > 0:
            self.add_time_and_generate_block(24, 600)
            time.sleep(10)

        after_balance = self.get_fibers_balance()
        result = self.get_balance_change(before_balance, after_balance)

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
        # account = self.Ckb_cli.util_key_info_by_private_key(self.fiber1.account_private)
        # self.Ckb_cli.wallet_get_live_cells(account['address']['testnet'])
        # tx_hash = self.Ckb_cli.wallet_transfer_by_private_key(
        #     self.fiber1.account_private,
        #     account["address"]["testnet"],
        #     1000000,
        #     self.node.rpcUrl,
        # )
        # self.Miner.miner_until_tx_committed(self.node, tx_hash)
        # tx_hash = self.Tx.send_transfer_self_tx_with_input([tx_hash], ["0x0"], self.fiber1.account_private,
        #                                                    output_count=100, fee=12000)
        # self.Miner.miner_until_tx_committed(self.node, tx_hash)
        # self.Ckb_cli.wallet_get_live_cells(account['address']['testnet'])

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
        self.add_time_and_generate_block(24, 100)
        for channels in self.fiber1.get_client().list_channels({})["channels"]:
            try:
                self.fiber1.get_client().shutdown_channel(
                    {"channel_id": channels["channel_id"], "force": True}
                )
            except Exception as e:
                pass
        time.sleep(10)
        # account = self.Ckb_cli.util_key_info_by_private_key(self.fiber1.account_private)
        # self.Ckb_cli.wallet_get_live_cells(account['address']['testnet'])
        # tx_hash = self.Ckb_cli.wallet_transfer_by_private_key(
        #     self.fiber1.account_private,
        #     account["address"]["testnet"],
        #     1000000,
        #     self.node.rpcUrl,
        # )
        # # self.Miner.miner_until_tx_committed(self.node, tx_hash)
        # tx_hash = self.Tx.send_transfer_self_tx_with_input([tx_hash], ["0x0"], self.fiber1.account_private,
        #                                                    output_count=100, fee=12000)
        # self.Miner.miner_until_tx_committed(self.node, tx_hash)
        # self.Ckb_cli.wallet_get_live_cells(account['address']['testnet'])
        for i in range(N):
            preimage = fiber2_preimages[i]
            self.fiber2.get_client().settle_invoice(
                {"payment_hash": ckb_hash(preimage), "payment_preimage": preimage}
            )
        while len(self.get_commit_cells()) > 0:
            self.add_time_and_generate_block(24, 600)
            time.sleep(20)

        after_balance = self.get_fibers_balance()
        result = self.get_balance_change(before_balance, after_balance)

    def test_mutil_to_one_udt(self):
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
        for i in range(20):
            self.faucet(self.fiber1.account_private, 10000)
        self.faucet(
            self.fiber1.account_private,
            0,
            self.fiber1.account_private,
            10000 * 100000000,
        )

        fibers_balance = []
        for i in range(len(self.fibers)):
            balance = self.get_fiber_balance(self.fibers[i])
            fibers_balance.append(balance)

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
        udt = self.get_account_udt_script(self.fiber1.account_private)
        for i in range(20):
            for j in range(len(self.new_fibers)):
                # self.send_invoice_payment(self.new_fibers[i], self.fiber2, 1 * 100000000, False,udt=self.get_account_udt_script(self.fiber1.account_private))
                self.send_payment(
                    self.new_fibers[j],
                    self.fiber2,
                    1 * 100000000,
                    False,
                    udt=udt,
                    try_count=0,
                )

        self.fiber1.get_client().disconnect_peer({"peer_id": self.fiber2.get_peer_id()})
        self.add_time_and_generate_block(23, 20)
        while len(self.get_commit_cells()) == 0:
            self.add_time_and_generate_block(1, 20)
            time.sleep(15)
        self.node.getClient().generate_epochs("0x1")
        time.sleep(10)
        while (
            self.node.getClient().get_tip_block_number()
            - self.get_latest_commit_tx_number()
            < 20
        ):
            time.sleep(5)
        while len(self.get_commit_cells()) > 0:
            # cells = self.get_commit_cells()
            self.add_time_and_generate_block(24, 600)
            time.sleep(10)
        channels = self.fiber1.get_client().list_channels({})
        for channel in channels["channels"]:
            try:
                self.fiber1.get_client().shutdown_channel(
                    {
                        "channel_id": channel["channel_id"],
                        "close_script": self.get_account_script(
                            self.fiber1.account_private
                        ),
                        "fee_rate": "0x3FC",
                    }
                )
                shutdown_tx = self.wait_and_check_tx_pool_fee(1000, False, 200)
                self.Miner.miner_until_tx_committed(self.node, shutdown_tx)
            except Exception as e:
                pass

        time.sleep(10)
        after_fibers_balance = []
        for i in range(len(self.fibers)):
            balance = self.get_fiber_balance(self.fibers[i])
            after_fibers_balance.append(balance)
        print("---before-----")
        for i in range(len(fibers_balance)):
            print(fibers_balance[i])
        print("-----after-----")
        for i in range(len(after_fibers_balance)):
            print(after_fibers_balance[i])

        discard_ckb_balance = 0
        for i in range(len(after_fibers_balance)):
            print(
                f"fiber:{i}: before:{fibers_balance[i]['chain']['udt']} after:{after_fibers_balance[i]['chain']['udt']},result:{after_fibers_balance[i]['chain']['udt'] - fibers_balance[i]['chain']['udt']}"
            )
            discard_ckb_balance = discard_ckb_balance + (
                fibers_balance[i]["chain"]["udt"]
                - after_fibers_balance[i]["chain"]["udt"]
            )
        print("discard_ckb_balance:", discard_ckb_balance)
        assert discard_ckb_balance == 0
