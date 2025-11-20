"""
standalone_watchtower_rpc_url
disable_built_in_watchtower
"""

import shutil
import time

import pytest

from framework.basic_fiber import FiberTest


class TestWatchTower(FiberTest):
    # debug = True
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 5}

    def test_old_force_shutdown_channel(self):
        """
        之前有channel，然后监听
            channel ready 的channel，瞭望塔无法工作
        Returns:

        """
        self.fiber3 = self.start_new_fiber(self.generate_account(1000))
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1)
        self.send_payment(self.fiber1, self.fiber2, 1)
        self.fiber1.get_client().shutdown_channel(
            {
                "channel_id": self.fiber1.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "force": True,
            }
        )
        tx = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, tx)
        self.fiber1.stop()
        self.fiber2.stop()
        self.fiber2.prepare(
            {
                "fiber_standalone_watchtower_rpc_url": self.fiber3.get_client().url,
                "fiber_disable_built_in_watchtower": True,
            }
        )
        self.fiber2.start()
        time.sleep(5)
        self.node.getClient().generate_epochs("0x1", 0)
        with pytest.raises(Exception) as exc_info:
            tx = self.wait_and_check_tx_pool_fee(1000, False, 20 * 5)
        expected_error_message = "expected_state"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )
        self.fiber1.start()
        tx = self.wait_and_check_tx_pool_fee(1000, False, 20 * 5)

    def test_old_channel(self):
        """
        之前有channel，然后监听
        channel ready 的channel，瞭望塔无法工作
        Returns:

        """
        self.fiber3 = self.start_new_fiber(self.generate_account(1000))
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1)
        self.fiber2.stop()
        self.fiber2.prepare(
            {
                "fiber_standalone_watchtower_rpc_url": self.fiber3.get_client().url,
                "fiber_disable_built_in_watchtower": True,
            }
        )
        self.fiber2.start()
        time.sleep(5)
        self.send_payment(self.fiber1, self.fiber2, 1)
        self.fiber1.get_client().shutdown_channel(
            {
                "channel_id": self.fiber1.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "force": True,
            }
        )
        tx = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, tx)
        self.fiber1.stop()
        self.node.getClient().generate_epochs("0x1", 0)
        with pytest.raises(Exception) as exc_info:
            tx = self.wait_and_check_tx_pool_fee(1000, False, 20 * 5)
        expected_error_message = "expected_state"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )
        self.fiber1.start()
        tx = self.wait_and_check_tx_pool_fee(1000, False, 20 * 5)

    @pytest.mark.skip(reason="<UNK>")
    def test_no_balance(self):
        """
        standalone_watchtower_rpc 没有ckb
            监听到watchtower 交易，报：Failed to build settlement tx: Other(Not enough capacity)，充值后正常发送shutdown交易
        Returns:
        """
        self.fiber3 = self.start_new_fiber(self.generate_random_preimage())
        self.fiber2.stop()
        self.fiber2.prepare(
            {
                "fiber_standalone_watchtower_rpc_url": self.fiber3.get_client().url,
                "fiber_disable_built_in_watchtower": "true",
            }
        )
        self.fiber2.start()
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1)
        self.fiber1.get_client().shutdown_channel(
            {
                "channel_id": self.fiber1.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "force": True,
            }
        )
        tx = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, tx)
        self.fiber1.stop()
        self.node.getClient().generate_epochs("0x1", 0)
        with pytest.raises(Exception) as exc_info:
            tx = self.wait_and_check_tx_pool_fee(1000, False, 20 * 5)
        expected_error_message = "expected_state"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )
        self.faucet(self.fiber3.account_private, 1000)
        tx = self.wait_and_check_tx_pool_fee(1000, False, 120 * 5)
        self.Miner.miner_until_tx_committed(self.node, tx)
        msg = self.get_tx_message(tx)
        print("force shutdown tx:", msg)
        assert {
            "args": self.fiber2.get_account()["lock_arg"],
            "capacity": 6199999546,
        } in msg["output_cells"]
        assert {
            "args": self.fiber1.get_account()["lock_arg"],
            "capacity": 106199999545,
        } in msg["output_cells"]
        assert {
            "args": self.fiber3.get_account()["lock_arg"],
            "capacity": 100000000000,
        } in msg["input_cells"]

    def test_standalone_watchtower_rpc_url(self):
        """
        没有pending tlc 的 channel
        Returns:
        """
        self.fiber3 = self.start_new_fiber(self.generate_account(1000))
        before_balance = self.get_fibers_balance()
        self.fiber2.stop()
        self.fiber2.prepare(
            {
                "fiber_standalone_watchtower_rpc_url": self.fiber3.get_client().url,
                "fiber_disable_built_in_watchtower": True,
            }
        )
        self.fiber2.start()
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1)
        self.fiber1.get_client().shutdown_channel(
            {
                "channel_id": self.fiber1.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "force": True,
            }
        )
        tx = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, tx)
        while len(self.get_commit_cells()) > 0:
            self.node.getClient().generate_epochs("0x1", 0)
            time.sleep(10)
        after_balance = self.get_fibers_balance()
        result = self.get_balance_change(before_balance, after_balance)
        assert result[0]["ckb"] < 2000
        assert 9800000000 < result[1]["ckb"] < 9800002000
        assert abs(result[2]["ckb"] + 9800000000) < 10000

    def test_2_node(self):
        """
        2个节点挂同一个channel 给瞭望塔
        Returns:
        """
        self.fiber3 = self.start_new_fiber(self.generate_account(1000))
        before_balance = self.get_fibers_balance()
        self.fiber2.stop()
        self.fiber2.prepare(
            {
                "fiber_standalone_watchtower_rpc_url": self.fiber3.get_client().url,
                "fiber_disable_built_in_watchtower": "true",
            }
        )
        self.fiber2.start()
        self.fiber1.stop()
        self.fiber1.prepare(
            {
                "fiber_standalone_watchtower_rpc_url": self.fiber3.get_client().url,
                "fiber_disable_built_in_watchtower": "true",
            }
        )
        self.fiber1.start()
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1)
        self.fiber2.get_client().shutdown_channel(
            {
                "channel_id": self.fiber1.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "force": True,
            }
        )
        tx = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, tx)
        self.node.getClient().generate_epochs("0x1", 0)

        tx = self.wait_and_check_tx_pool_fee(1000, False, 120 * 5)
        self.Miner.miner_until_tx_committed(self.node, tx)

        msg = self.get_tx_message(tx)
        print("force shutdown tx:", msg)
        after_balance = self.get_fibers_balance()
        result = self.get_balance_change(before_balance, after_balance)

    def test_watch_node_offline(self):
        """
        瞭望塔节点不在线时，节点启动成功，创建channel 会panic
        我方节点发送交易时，standalone_watchtower不在线, 节点panic: watchtower client call should be ok: Transport(error trying to connect: tcp connect error: Connection refused (os error 61)
        Returns:
        """
        self.fiber3 = self.start_new_fiber(self.generate_account(1000))
        self.fiber2.stop()
        self.fiber2.prepare(
            {
                "fiber_standalone_watchtower_rpc_url": self.fiber3.get_client().url,
                "fiber_disable_built_in_watchtower": "true",
            }
        )
        self.fiber3.stop()
        self.fiber2.start()
        self.fiber2.get_client().node_info()
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1)
        self.fiber2.get_client().node_info()

    def test_revert_tx(self):
        self.fiber3 = self.start_new_fiber(self.generate_account(1000))
        self.fiber2.stop()
        self.fiber2.prepare(
            {
                "fiber_standalone_watchtower_rpc_url": self.fiber3.get_client().url,
                "fiber_disable_built_in_watchtower": "true",
            }
        )
        self.fiber2.start()
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
        revocation_tx = self.wait_and_check_tx_pool_fee(1000, False, 100000000)
        tx = self.get_tx_message(revocation_tx)
        print("revocation tx:", tx)
        assert tx["input_cells"][1]["args"] == self.fiber3.get_account()["lock_arg"]
        assert tx["output_cells"][0]["args"] == self.fiber2.get_account()["lock_arg"]
        assert tx["output_cells"][1]["args"] == self.fiber3.get_account()["lock_arg"]
        # assert tx["input_cells"][0]["capacity"] == tx["output_cells"][0]["capacity"]
