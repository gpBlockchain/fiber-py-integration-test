from framework.basic_fiber import FiberTest


class CkbStopTest(FiberTest):
    def test_stop(self):
        for i in range(10):
            # self.start_new_fiber(self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000))
            self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0)
        self.node.stop()
        self.fiber1.get_client().node_info()
        self.fiber2.get_client().node_info()

    def test_balala(self):
        # self.node.start()

        # self.node.getClient().get_tip_block_number()
        self.fiber1.get_client().node_info()
        # self.fiber2.get_client().node_info()