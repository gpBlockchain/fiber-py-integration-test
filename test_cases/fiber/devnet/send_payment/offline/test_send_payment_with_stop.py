from framework.basic_fiber import FiberTest
import time


class TestSendPaymentWithStop(FiberTest):

    def test_send_with_disconnect(self):

        # self.fiber3 = self.start_new_mock_fiber("")
        # self.fiber4 = self.start_new_mock_fiber("")
        #
        # self.fiber5 = self.start_new_mock_fiber("")
        # self.fiber6 = self.start_new_mock_fiber("")

        # 1 -2 -3
        # |  |  |
        # 4- 5- 6
        self.fiber3 = self.start_new_fiber(self.generate_account(10000))
        self.fiber4 = self.start_new_fiber(self.generate_account(10000))
        self.fiber5 = self.start_new_fiber(self.generate_account(10000))
        self.fiber6 = self.start_new_fiber(self.generate_account(10000))

        # for i in  range(2):
        self.open_channel(
            self.fiber1, self.fiber2, 1000 * 100000000, 1000 * 100000000, 0, 0
        )
        self.open_channel(
            self.fiber2, self.fiber3, 1000 * 100000000, 1000 * 100000000, 0, 0
        )

        self.open_channel(
            self.fiber1, self.fiber4, 1000 * 100000000, 1000 * 100000000, 0, 0
        )
        self.open_channel(
            self.fiber2, self.fiber5, 1000 * 100000000, 1000 * 100000000, 0, 0
        )
        self.open_channel(
            self.fiber3, self.fiber6, 1000 * 100000000, 1000 * 100000000, 0, 0
        )
        #
        self.open_channel(
            self.fiber4, self.fiber5, 1000 * 100000000, 1000 * 100000000, 0, 0
        )
        self.open_channel(
            self.fiber5, self.fiber6, 1000 * 100000000, 1000 * 100000000, 0, 0
        )

        time.sleep(10)
        for j in range(5):
            for i in range(14):
                for fiber in self.fibers:
                    try:
                        self.send_payment(fiber, fiber, 1, False, try_count=1)
                        self.send_invoice_payment(fiber, fiber, 1, False, try_count=1)
                    except:
                        pass

            self.fiber1.stop()
            self.fiber2.get_client().disconnect_peer(
                {"pubkey": self.fiber3.get_pubkey()}
            )
            self.fiber4.stop()
            self.fiber4.start()
            self.fiber1.start()
            time.sleep(2)
            self.fiber1.connect_peer(self.fiber2)
            self.fiber2.connect_peer(self.fiber3)
            self.fiber3.connect_peer(self.fiber4)
            self.fiber4.connect_peer(self.fiber1)
            self.wait_channel_peer_connect(60)
            time.sleep(5)
            for fiber in self.fibers:
                self.send_payment(fiber, fiber, 1, True)
                self.send_invoice_payment(fiber, fiber, 1, True)
            time.sleep(10)
            balances = self.get_fibers_balance()
            for balance in balances:
                # assert balance['ckb']['local_balance'] == {'local_balance': 200000000000,
                #                           'offered_tlc_balance': 0,
                #                           'received_tlc_balance': 0
                #                           }
                assert (
                    balance["ckb"]["local_balance"] == 200000000000
                    or balance["ckb"]["local_balance"] == 300000000000
                )

    # def test_cccc(self):
    #     self.start_new_mock_fiber("")
    #     self.start_new_mock_fiber("")
    #     balance = self.get_fibers_balance()
    #     for b in balance:
    #         print(b)
    #
    #
    #     # for fiber in self.fibers:
    #     #     self.send_payment(fiber, fiber, 1, True)
    #
    # # def test_cccc(self):
    # #     self.start_new_mock_fiber("")
    # #     self.start_new_mock_fiber("")
    # #     # for i in range(1000):
    # #         # self.send_payment(self.fibers[0], self.fibers[-1], 1, False)
    # #         # self.send_payment(self.fibers[-1], self.fibers[0], 1, False)
    # #     # self.fiber1.stop()
    # #     self.fibers[1].get_client().list_channels({
    # #         "pubkey":self.fibers[2].get_pubkey()
    # #     })
