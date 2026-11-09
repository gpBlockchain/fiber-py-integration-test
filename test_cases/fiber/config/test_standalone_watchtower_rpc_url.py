"""
standalone_watchtower_rpc_url
disable_built_in_watchtower
"""

import shutil
import time

import pytest

from framework.basic_fiber import FiberTest
from framework.config import DEFAULT_MIN_DEPOSIT_CKB


class TestWatchTower(FiberTest):
    # debug = True
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 5}

    def test_old_force_shutdown_channel(self):
        """
        强制关闭并确认后再配置独立瞭望塔；原通道仍可由瞭望塔结算。
        """
        self.fiber3 = self.start_new_fiber(self.generate_account(1000))
        channel_id = self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1)
        self.send_payment(self.fiber1, self.fiber2, 1)
        self.fiber1.get_client().shutdown_channel(
            {"channel_id": channel_id, "force": True}
        )
        commitment_tx = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, commitment_tx)
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
        settlement_tx = self.wait_and_check_tx_pool_fee(1000, False, 20 * 5)
        settlement = self.node.getClient().get_transaction(settlement_tx)["transaction"]
        assert any(
            tx_input["previous_output"]["tx_hash"] == commitment_tx
            for tx_input in settlement["inputs"]
        )
        tx_message = self.get_tx_message(settlement_tx)
        assert any(
            cell["args"] == self.fiber3.get_account()["lock_arg"]
            for cell in tx_message["input_cells"]
        )
        self.Miner.miner_until_tx_committed(self.node, settlement_tx)

    def test_old_channel(self):
        """
        通道进入 ChannelReady 后再配置独立瞭望塔；更新通道状态后，瞭望塔可以结算。
        """
        self.fiber3 = self.start_new_fiber(self.generate_account(1000))
        channel_id = self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1)
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
            {"channel_id": channel_id, "force": True}
        )
        commitment_tx = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, commitment_tx)
        self.fiber1.stop()
        self.node.getClient().generate_epochs("0x1", 0)
        settlement_tx = self.wait_and_check_tx_pool_fee(1000, False, 20 * 5)
        settlement = self.node.getClient().get_transaction(settlement_tx)["transaction"]
        assert any(
            tx_input["previous_output"]["tx_hash"] == commitment_tx
            for tx_input in settlement["inputs"]
        )
        tx_message = self.get_tx_message(settlement_tx)
        assert any(
            cell["args"] == self.fiber3.get_account()["lock_arg"]
            for cell in tx_message["input_cells"]
        )
        self.Miner.miner_until_tx_committed(self.node, settlement_tx)

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
        expected_error_message = "tx pool pending size did not reach"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )
        self.faucet(self.fiber3.account_private, 1000)
        before_balance = self.get_fiber_balance(self.fiber3)
        tx = self.wait_and_check_tx_pool_fee(1000, False, 120 * 5)
        self.Miner.miner_until_tx_committed(self.node, tx)
        after_balance = self.get_fiber_balance(self.fiber3)
        result = self.get_balance_change([before_balance], [after_balance])
        assert abs(result[0]["ckb"] + DEFAULT_MIN_DEPOSIT_CKB) < 10000

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
        tx_response = self.Miner.miner_until_tx_committed(self.node, tx)
        commitment_lock = tx_response["transaction"]["outputs"][0]["lock"]
        # Follow confirmed spends: an empty live-cell query can precede settlement.
        # Even without TLCs, the two parties can receive funds in separate spends.
        for _ in range(3):
            if not any(
                output["lock"]["code_hash"] == commitment_lock["code_hash"]
                and output["lock"]["hash_type"] == commitment_lock["hash_type"]
                for output in tx_response["transaction"]["outputs"]
            ):
                break
            self.node.getClient().generate_epochs("0x1", 0)
            for _ in range(60):
                settlement_tx, _ = self.get_ln_cell_death_hash(tx)
                if settlement_tx is not None:
                    break
                time.sleep(1)
            else:
                raise TimeoutError(f"Commitment output {tx}:0 was not settled")
            tx_response = self.Miner.miner_until_tx_committed(self.node, settlement_tx)
            assert any(
                tx_input["previous_output"] == {"tx_hash": tx, "index": "0x0"}
                for tx_input in tx_response["transaction"]["inputs"]
            )
            tx = settlement_tx
        else:
            raise TimeoutError(f"Commitment outputs remain after settlement: {tx}")
        # Balance queries use the indexer, which can lag behind transaction confirmation.
        self.Miner.miner_with_version(self.node, "0x0")
        after_balance = self.get_fibers_balance()
        result = self.get_balance_change(before_balance, after_balance)
        assert result[0]["ckb"] < 2000
        assert abs(result[1]["ckb"] - DEFAULT_MIN_DEPOSIT_CKB) < 2000
        assert abs(result[2]["ckb"] + DEFAULT_MIN_DEPOSIT_CKB) < 10000

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
                "target_pubkey": self.fiber2.get_client().node_info()["pubkey"],
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
