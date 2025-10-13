import time

from framework.basic_fiber import FiberTest


class TestStop(FiberTest):

    debug = True
    def test_stop(self):
        self.open_channel(self.fiber1,self.fiber2,1000*100000000,0,0,0)

    def test_list_peer(self):
        # self.fiber1.get_client().list_peers()
        # self.fiber2.get_client().list_peers()
        # self.fiber1.start()
        # time.sleep(5)
        self.fiber1.get_client().list_peers()
