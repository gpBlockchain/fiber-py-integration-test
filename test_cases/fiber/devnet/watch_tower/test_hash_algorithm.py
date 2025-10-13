from framework.basic_fiber import FiberTest


class TestHashAlgorithm(FiberTest):
    debug = True
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 5}

    def test_hash_algorithm(self):
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0)
        self.send_invoice_payment(self.fiber1, self.fiber2, 1 * 100000000)
        # self.fiber1.get_client().shutdown_channel({
        #     "channel_id": self.fiber1.get_client().list_channels({})["channels"][0]["channel_id"],
        #     "force":True
        # })
        # tx_hash = self.wait_and_check_tx_pool_fee(1000,False)
        # self.Miner.miner_until_tx_committed(self.node, tx_hash)
        # self.fiber1.stop()
        # self.node.getClient().generate_epochs("0x6",wait_time=0)
        # tx_hash = self.wait_and_check_tx_pool_fee(1000,False,try_size=120*5)
        # msg = self.get_tx_message(tx_hash)
        # print(msg)

    def test_0000(self):
        self.fiber1.get_client().list_channels({})
        self.fiber2.get_client().list_channels({})
