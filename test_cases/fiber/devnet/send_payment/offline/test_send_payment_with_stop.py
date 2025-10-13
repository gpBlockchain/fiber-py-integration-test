from framework.basic_fiber import FiberTest


class TestSendPaymentWithStop(FiberTest):
    pass
    # debug = True
    # def test_0000(self):
    #     self.start_new_fiber(self.generate_account(10000))
    #     self.start_new_fiber(self.generate_account(10000))
    #     for i in range(len(self.fibers) - 1):
    #         self.open_channel(self.fibers[i], self.fibers[i + 1], 1000 * 100000000, 1)
    #
    #     for i in range(100):
    #         self.send_payment(self.fibers[0], self.fibers[-1], 1, False)
    #     self.fibers[0].stop()
    #     self.fibers[-1].stop()

    # def test_cccc(self):
    #     self.start_new_mock_fiber("")
    #     self.start_new_mock_fiber("")
    #     # for i in range(1000):
    #         # self.send_payment(self.fibers[0], self.fibers[-1], 1, False)
    #         # self.send_payment(self.fibers[-1], self.fibers[0], 1, False)
    #     # self.fiber1.stop()
    #     self.fibers[1].get_client().list_channels({
    #         "peer_id":self.fibers[2].get_peer_id()
    #     })
