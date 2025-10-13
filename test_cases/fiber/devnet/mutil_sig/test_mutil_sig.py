import time

from framework.basic_fiber import FiberTest


class MutilSigTestCase(FiberTest):
    debug = True

    def test_00000(self):
        self.faucet(self.fiber1.account_private, 0, self.fiber1.account_private, 10000 * 100000000)
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0,
                          udt=self.get_account_udt_script(self.fiber1.account_private))
        for i in range(10):
            self.send_payment(self.fiber1, self.fiber2, 1 * 100000000, wait=False,
                              udt=self.get_account_udt_script(self.fiber1.account_private))
        # self.fiber1.stop()
        # self.fiber1.start()

    def test_000001(self):
        udt = self.get_account_udt_script(self.fiber1.account_private)
        fiber2_peer_id = self.fiber2.get_peer_id()
        for i in range(10):
            for i in range(10):
                self.send_payment(self.fiber1, self.fiber2, 1 * 100000000, wait=False, udt=udt)
            self.fiber1.get_client().disconnect_peer({
                "peer_id": fiber2_peer_id,
            })
            time.sleep(5)
            self.fiber1.connect_peer(self.fiber2)
            time.sleep(5)
        # self.fiber1.stop()
        # self.fiber1.start()


    def test_00022221(self):
        self.get_tx_message("0x48e2479b46a2a8f0e817af92e26dcaa0975d103c5e750d62dfb8ccc7a26475da")
        # 0x999d1b0181a157416c6a94af47ea8683b18c07e0e98e06bc05b6aa00cfc6dc42
        # 0x85dd85357973264d6fb287ba63b5d8600f813c707d50be4778e825118a43003e
        self.get_tx_message("0x85dd85357973264d6fb287ba63b5d8600f813c707d50be4778e825118a43003e")
    def test_generate_epoch(self):
        self.node.getClient().generate_epochs("0x2")

    def test0basad(self):
        # fiber1_balance = self.get_fiber_balance(self.fiber2)
        # print("fiber1 balance", fiber1_balance)
        channel_id = self.fiber2.get_client().shutdown_channel({
            "channel_id": self.fiber1.get_client().list_channels({})["channels"][1][
                    "channel_id"
                ],
                "force": True,
        })
        # self.fiber1.stop()
        # self.fiber1.start()
        # self.fiber2.stop()
        # self.fiber2.start()
        # self.fiber1.get_client().disconnect_peer({
        #     "peer_id": self.fiber2.get_peer_id()
        #     ,
        # })
        # self.fiber1.connect_peer(self.fiber2)
        # self.get_fiber_graph_balance()
        # self.fiber1.connect_peer(self.fiber2)
        # self.fiber2.connect_peer(self.fiber1)

        # self.fiber1.connect_peer(self.fiber2)
        # self.fiber1.get_client().list_peers()
        # self.fiber2.get_client().list_peers()
        # udt = self.get_account_udt_script(self.fiber1.account_private)
        # self.send_payment(self.fiber2,self.fiber1, 1 * 100000000,wait=False,udt=udt)
        #
        # self.get_fiber_graph_balance()
        # self.fiber1.stop()
        # self.fiber1.start()
        # self.fiber2.stop()
        # self.fiber2.start()
#         self.fiber1.get_client().list_peers()
