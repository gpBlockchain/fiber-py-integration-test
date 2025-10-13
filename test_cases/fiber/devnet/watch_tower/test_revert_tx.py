import shutil
import time

from framework.basic_fiber import FiberTest


class RevertTx(FiberTest):

    def test_01(self):
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1000 * 100000000)

        self.fiber1.stop()
        # back up node1 data
        shutil.copytree(
            f"{self.fiber1.tmp_path}/fiber", f"{self.fiber1.tmp_path}/fiber.bak"
        )
        self.fiber1.start()
        time.sleep(5)
        # # restart fiber 1
        payment = self.fiber1.get_client().send_payment(
            {
                "amount": hex(1000 * 100000000),
                "target_pubkey": self.fiber2.get_client().node_info()["node_id"],
                "keysend": True,
            }
        )
        self.wait_payment_state(self.fiber1, payment["payment_hash"], "Success")
        # # stop fiber1 and back data
        self.fiber1.stop()
        self.fiber2.stop()
        shutil.rmtree(f"{self.fiber1.tmp_path}/fiber")
        shutil.copytree(
            f"{self.fiber1.tmp_path}/fiber.bak", f"{self.fiber1.tmp_path}/fiber"
        )
        self.fiber1.start()
        self.fiber1.get_client().shutdown_channel(
            {
                "channel_id": self.fiber1.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "force": True,
            }
        )
        tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        self.fiber2.start()
        revocation_tx = self.wait_and_check_tx_pool_fee(1000, False, 100000000)
        tx = self.get_tx_message(revocation_tx)
        assert tx["input_cells"][1]["args"] == self.fiber2.get_account()["lock_arg"]
        assert tx["output_cells"][0]["args"] == self.fiber2.get_account()["lock_arg"]
        assert tx["output_cells"][1]["args"] == self.fiber2.get_account()["lock_arg"]
        assert tx["input_cells"][0]["capacity"] == tx["output_cells"][0]["capacity"]

    def test_02(self):
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1000 * 100000000)

        self.fiber1.stop()
        # back up node1 data
        shutil.copytree(
            f"{self.fiber1.tmp_path}/fiber", f"{self.fiber1.tmp_path}/fiber.bak"
        )
        self.fiber1.start()
        time.sleep(5)
        # # restart fiber 1
        payment = self.fiber2.get_client().send_payment(
            {
                "amount": hex(1000 * 100000000),
                "target_pubkey": self.fiber1.get_client().node_info()["node_id"],
                "keysend": True,
            }
        )
        self.wait_payment_state(self.fiber2, payment["payment_hash"], "Success")
        # # stop fiber1 and back data
        self.fiber1.stop()
        self.fiber2.stop()
        shutil.rmtree(f"{self.fiber1.tmp_path}/fiber")
        shutil.copytree(
            f"{self.fiber1.tmp_path}/fiber.bak", f"{self.fiber1.tmp_path}/fiber"
        )
        self.fiber1.start()
        self.fiber1.get_client().shutdown_channel(
            {
                "channel_id": self.fiber1.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "force": True,
            }
        )
        tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        self.fiber2.start()
        revocation_tx = self.wait_and_check_tx_pool_fee(1000, False, 100000000)
        tx = self.get_tx_message(revocation_tx)
        assert tx["input_cells"][1]["args"] == self.fiber2.get_account()["lock_arg"]
        assert tx["output_cells"][0]["args"] == self.fiber2.get_account()["lock_arg"]
        assert tx["output_cells"][1]["args"] == self.fiber2.get_account()["lock_arg"]
        assert tx["input_cells"][0]["capacity"] == tx["output_cells"][0]["capacity"]
