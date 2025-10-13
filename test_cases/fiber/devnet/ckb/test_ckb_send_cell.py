from framework.basic_fiber import FiberTest


class TestCkbSendCell(FiberTest):

    def test_send_cell(self):
        self.open_channel(self.fiber1,self.fiber2,1000 * 100000000,0)
        self.fiber1.get_client().shutdown_channel({
            'channel_id': self.fiber1.get_client().list_channels({})['channels'][0]['channel_id'],
            "force":True
        })
        self.wait_and_check_tx_pool_fee(1000,False)