from framework.basic import CkbTest
import socket

from framework.config import DEFAULT_MIN_DEPOSIT_CKB
from framework.helper.udt_contract import UdtContract, issue_udt_tx
from framework.test_fiber import Fiber, FiberConfigPath
from framework.util import generate_account_privakey, get_project_root
from framework.util import run_command
import time
import random
from datetime import datetime
from framework.util import to_int_from_big_uint128_le, change_time
import logging
import os
import subprocess

# FIBER_TAR_GZ = "ckb-py-integration-test/source/fiber/data.fiber.tar.gz"
XUDT_TX_HASH = "0x03c4475655a46dc4984c49fce03316f80bf666236bd95118112731082758d686"
XUDT_CODE_HASH = "0x102583443ba6cfe5a3ac268bbb4475fb63eb497dce077f126ad3b148d4f4f8f8"


class FiberTest(CkbTest):
    # deploy
    new_fibers: [Fiber] = []
    fibers: [Fiber] = []
    fiber1: Fiber
    fiber2: Fiber
    debug = False
    first_debug = False
    fiber_version = FiberConfigPath.CURRENT_DEV
    logger = logging.getLogger(__name__)
    start_fiber_config = {}
    fnn_log_level = "debug"
    beginNum = "0x0"

    @classmethod
    def setup_class(cls):
        """
        部署一个ckb 节点
        启动 ckb 节点

        Returns:

        """
        global ACCOUNT_PRIVATE_KEY_INDEX
        ACCOUNT_PRIVATE_KEY_INDEX = 0
        cls.account1_private_key = cls.Config.ACCOUNT_PRIVATE_1
        cls.account2_private_key = cls.Config.ACCOUNT_PRIVATE_2
        cls.account1 = cls.Ckb_cli.util_key_info_by_private_key(
            cls.account1_private_key
        )
        cls.account2 = cls.Ckb_cli.util_key_info_by_private_key(
            cls.account2_private_key
        )
        cls.node = cls.CkbNode.init_dev_by_port(
            cls.CkbNodeConfigPath.CURRENT_TEST, "contract/node", 8114, 8125
        )

        if cls.debug:
            if check_port(8114):
                cls.logger.debug("=====不是第一次启动=====")
                return
            cls.debug = False
            cls.logger.debug("====debug====第一次启动=")
            cls.first_debug = True

        cls.node.prepare()
        tar_file(
            f"{get_project_root()}/source/fiber/data.1214.tar.gz", cls.node.ckb_dir
        )
        cls.node.start()
        cls.node.getClient().get_consensus()

    def setup_method(cls, method):
        """
        启动2个fiber
        给 fiber1 充值udt 金额
        连接2个fiber
        Args:
            method:

        Returns:

        """
        cls.did_pass = None
        cls.beginNum = hex(cls.node.getClient().get_tip_block_number())
        cls.fibers = []
        cls.new_fibers = []
        cls.fiber1 = Fiber.init_by_port(
            cls.fiber_version,
            cls.account1_private_key,
            "fiber/node1",
            "8228",
            "8227",
        )
        cls.fiber2 = Fiber.init_by_port(
            cls.fiber_version,
            cls.account2_private_key,
            "fiber/node2",
            "8229",
            "8230",
        )
        cls.fibers.append(cls.fiber1)
        cls.fibers.append(cls.fiber2)
        #
        cls.udtContract = UdtContract(XUDT_TX_HASH, 0)
        #
        deploy_hash, deploy_index = cls.udtContract.get_deploy_hash_and_index()

        if cls.debug:
            return
            # # issue
        cls.node.getClient().clear_tx_pool()
        for i in range(5):
            cls.Miner.miner_with_version(cls.node, "0x0")
        tx_hash = issue_udt_tx(
            cls.udtContract,
            cls.node.rpcUrl,
            cls.fiber1.account_private,
            cls.fiber1.account_private,
            1000 * 100000000,
        )
        cls.Miner.miner_until_tx_committed(cls.node, tx_hash)
        cls.node.start_miner()
        # deploy fiber
        # start 2 fiber with xudt
        update_config = {
            "ckb_rpc_url": cls.node.rpcUrl,
            "ckb_udt_whitelist": True,
            "xudt_script_code_hash": cls.Contract.get_ckb_contract_codehash(
                deploy_hash, deploy_index, True, cls.node.rpcUrl
            ),
            "xudt_cell_deps_tx_hash": deploy_hash,
            "xudt_cell_deps_index": deploy_index,
        }
        update_config.update(cls.start_fiber_config)

        cls.fiber1.prepare(update_config=update_config)
        cls.fiber1.start(fnn_log_level=cls.fnn_log_level)

        cls.fiber2.prepare(update_config=update_config)
        cls.fiber2.start(fnn_log_level=cls.fnn_log_level)
        before_balance1 = cls.Ckb_cli.wallet_get_capacity(
            cls.account1["address"]["testnet"], api_url=cls.node.getClient().url
        )
        cls.logger.debug(f"before_balance1:{before_balance1}")
        cls.fiber1.connect_peer(cls.fiber2)
        time.sleep(1)
        cls.logger.debug(f"\nSetting up method:{method.__name__}")

    def teardown_method(self, method):
        if self.debug:
            return
        if self.first_debug:
            return
        super().teardown_method(method)
        for fiber in self.fibers:
            fiber.stop()
            fiber.clean()

    @classmethod
    def teardown_class(cls):
        if cls.debug:
            return
        if cls.first_debug:
            return
        cls.node.stop()
        cls.node.clean()

    def faucet(
        self,
        account_private_key,
        ckb_balance,
        udt_owner_private_key=None,
        udt_balance=1000 * 1000000000,
    ):
        if ckb_balance > 60:
            account = self.Ckb_cli.util_key_info_by_private_key(account_private_key)
            tx_hash = self.Ckb_cli.wallet_transfer_by_private_key(
                self.Config.ACCOUNT_PRIVATE_1,
                account["address"]["testnet"],
                ckb_balance,
                self.node.rpcUrl,
            )
            self.Miner.miner_until_tx_committed(self.node, tx_hash, True)

        if udt_owner_private_key is None:
            return account_private_key
        tx_hash = issue_udt_tx(
            self.udtContract,
            self.node.rpcUrl,
            udt_owner_private_key,
            account_private_key,
            udt_balance,
        )
        self.Miner.miner_until_tx_committed(self.node, tx_hash, True)

    def generate_account(
        self, ckb_balance, udt_owner_private_key=None, udt_balance=1000 * 1000000000
    ):
        # error
        # if self.debug:
        #     raise Exception("debug not support generate_account")
        account_private_key = generate_account_privakey()
        self.faucet(
            account_private_key, ckb_balance, udt_owner_private_key, udt_balance
        )
        return account_private_key

    def start_new_mock_fiber(
        self,
        account_private_key,
        config=None,
        fiber_version=fiber_version,
    ):
        i = len(self.new_fibers)
        fiber = Fiber.init_by_port(
            fiber_version,
            account_private_key,
            f"fiber/node{3 + i}",
            str(8251 + i),
            str(8302 + i),
        )
        fiber.read_ckb_key()
        self.new_fibers.append(fiber)
        self.fibers.append(fiber)
        return fiber

    def start_new_fiber(
        self,
        account_private_key,
        config=None,
        fiber_version=FiberConfigPath.CURRENT_DEV,
    ):
        if self.debug:
            self.logger.debug("=================start  mock fiber ==================")
            return self.start_new_mock_fiber(account_private_key, config)
        update_config = config
        if config is None:
            deploy_hash, deploy_index = self.udtContract.get_deploy_hash_and_index()
            update_config = {
                "ckb_rpc_url": self.node.rpcUrl,
                "ckb_udt_whitelist": True,
                "xudt_script_code_hash": self.Contract.get_ckb_contract_codehash(
                    deploy_hash, deploy_index, True, self.node.rpcUrl
                ),
                "xudt_cell_deps_tx_hash": deploy_hash,
                "xudt_cell_deps_index": deploy_index,
            }
        update_config.update(self.start_fiber_config)

        i = len(self.new_fibers)
        # start fiber3
        fiber = Fiber.init_by_port(
            fiber_version,
            account_private_key,
            f"fiber/node{3 + i}",
            str(8251 + i),
            str(8402 + i),
        )
        self.fibers.append(fiber)
        self.new_fibers.append(fiber)
        fiber.prepare(update_config=update_config)
        fiber.start(fnn_log_level=self.fnn_log_level)
        return fiber

    def wait_for_channel_state(
        self,
        client,
        peer_id,
        expected_state,
        timeout=120,
        include_closed=False,
        channel_id=None,
    ):
        """Wait for a channel to reach a specific state.
        1. NEGOTIATING_FUNDING
        2. CHANNEL_READY
        3. CLOSED

        """
        for _ in range(timeout):
            channels = client.list_channels(
                {"peer_id": peer_id, "include_closed": include_closed}
            )
            if len(channels["channels"]) == 0:
                time.sleep(1)
                continue
            idx = 0
            if channel_id is not None:
                for i in range(len(channels["channels"])):
                    print("channel_id:", channel_id)
                    if channels["channels"][i]["channel_id"] == channel_id:
                        idx = i
            if type(expected_state) == str:
                if channels["channels"][idx]["state"]["state_name"] == expected_state:
                    self.logger.debug(
                        f"Channel reached expected state: {expected_state}"
                    )
                    # todo wait broading
                    time.sleep(1)
                    return channels["channels"][idx]["channel_id"]
            if type(expected_state) == dict:
                if channels["channels"][idx]["state"] == expected_state:
                    self.logger.debug(
                        f"Channel reached expected state: {expected_state}"
                    )
                    # todo wait broading
                    time.sleep(1)
                    return channels["channels"][idx]["channel_id"]
            self.logger.debug(
                f"Waiting for channel state: {expected_state}, current state: {channels['channels'][0]['state']}"
            )
            time.sleep(1)
        raise TimeoutError(
            f"Channel did not reach state {expected_state} within timeout period."
        )

    def get_account_udt_script(self, account_private_key):
        account1 = self.Ckb_cli.util_key_info_by_private_key(account_private_key)
        return {
            "code_hash": self.udtContract.get_code_hash(True, self.node.rpcUrl),
            "hash_type": "type",
            "args": self.udtContract.get_owner_arg_by_lock_arg(account1["lock_arg"]),
        }

    def open_channel(
        self,
        fiber1: Fiber,
        fiber2: Fiber,
        fiber1_balance,
        fiber2_balance,
        fiber1_fee=1000,
        fiber2_fee=1000,
        udt=None,
        other_config={},
    ):
        fiber1.connect_peer(fiber2)
        time.sleep(1)
        if fiber1_balance <= int(
            fiber2.get_client().node_info()[
                "open_channel_auto_accept_min_ckb_funding_amount"
            ],
            16,
        ):
            open_channel_config = {
                "peer_id": fiber2.get_peer_id(),
                "funding_amount": hex(fiber1_balance + DEFAULT_MIN_DEPOSIT_CKB),
                "tlc_fee_proportional_millionths": hex(fiber1_fee),
                "public": True,
            }
            open_channel_config.update(other_config)
            temporary_channel = fiber1.get_client().open_channel(open_channel_config)
            time.sleep(1)
            fiber2.get_client().accept_channel(
                {
                    "temporary_channel_id": temporary_channel["temporary_channel_id"],
                    "funding_amount": hex(fiber2_balance + DEFAULT_MIN_DEPOSIT_CKB),
                    "tlc_fee_proportional_millionths": hex(fiber2_fee),
                }
            )
            time.sleep(1)
            self.wait_for_channel_state(
                fiber1.get_client(), fiber2.get_peer_id(), "CHANNEL_READY"
            )
            return
        open_channel_config = {
            "peer_id": fiber2.get_peer_id(),
            "funding_amount": hex(
                fiber1_balance + fiber2_balance + DEFAULT_MIN_DEPOSIT_CKB
            ),
            "tlc_fee_proportional_millionths": hex(fiber1_fee),
            "public": True,
            "funding_udt_type_script": udt,
        }
        open_channel_config.update(other_config)
        fiber1.get_client().open_channel(open_channel_config)
        self.wait_for_channel_state(
            fiber1.get_client(), fiber2.get_peer_id(), "CHANNEL_READY"
        )
        channels = fiber1.get_client().list_channels({"peer_id": fiber2.get_peer_id()})
        # payment = fiber1.get_client().send_payment(
        #     {
        #         "target_pubkey": fiber2.get_client().node_info()["node_id"],
        #         "amount": hex(fiber2_balance),
        #         "keysend": True,
        #     }
        # )
        if fiber2_balance != 0:
            self.send_payment(fiber1, fiber2, fiber2_balance, True, udt, 10)
        if fiber2_fee != 1000:
            fiber2.get_client().update_channel(
                {
                    "channel_id": channels["channels"][0]["channel_id"],
                    "tlc_fee_proportional_millionths": hex(fiber2_fee),
                }
            )
        # self.wait_payment_state(fiber1, payment["payment_hash"], "Success")
        # channels = fiber1.get_client().list_channels({"peer_id": fiber2.get_peer_id()})
        # assert channels["channels"][0]["local_balance"] == hex(fiber1_balance)
        # assert channels["channels"][0]["remote_balance"] == hex(fiber2_balance)

    def send_invoice_payment(
        self,
        fiber1,
        fiber2,
        amount,
        wait=True,
        udt=None,
        try_count=5,
        other_options={"allow_mpp": True},
    ):
        # "allow_atomic_mpp":True
        invoice_params = {
            "amount": hex(amount),
            "currency": "Fibd",
            "description": "test invoice generated by node2",
            "payment_preimage": self.generate_random_preimage(),
            "hash_algorithm": "sha256",
            "udt_type_script": udt,
        }
        invoice_params.update(other_options)
        if "allow_atomic_mpp" in invoice_params:
            del invoice_params["payment_preimage"]
        invoice = fiber2.get_client().new_invoice(invoice_params)
        for i in range(try_count):
            # payment = fiber1.get_client().send_payment(
            #     {
            #         "invoice": invoice["invoice_address"],
            #         "allow_self_payment": True,
            #         "dry_run": True,
            #         "max_parts":"0x40",
            #     }
            # )
            try:
                send_payment_params = {
                    "invoice": invoice["invoice_address"],
                    "allow_self_payment": True,
                    "max_parts": "0x40",
                }
                if "allow_atomic_mpp" in invoice_params:
                    send_payment_params["amp"] = True
                payment = fiber1.get_client().send_payment(send_payment_params)
                if wait:
                    self.wait_payment_state(fiber1, payment["payment_hash"], "Success")
                return payment["payment_hash"]
            except Exception as e:
                time.sleep(1)
                continue
        send_payment_params = {
            "invoice": invoice["invoice_address"],
            "allow_self_payment": True,
            "max_parts": "0x40",
        }
        if "allow_atomic_mpp" in invoice_params:
            send_payment_params["amp"] = True
        payment = fiber1.get_client().send_payment(send_payment_params)
        if wait:
            self.wait_payment_state(fiber1, payment["payment_hash"], "Success")
        return payment["payment_hash"]

    def send_payment(self, fiber1, fiber2, amount, wait=True, udt=None, try_count=5):
        for i in range(try_count):
            try:
                payment = fiber1.get_client().send_payment(
                    {
                        "target_pubkey": fiber2.get_client().node_info()["node_id"],
                        "amount": hex(amount),
                        "keysend": True,
                        "allow_self_payment": True,
                        "udt_type_script": udt,
                        # "final_tlc_expiry_delta": hex(120960000),
                    }
                )
                if wait:
                    self.wait_payment_state(
                        fiber1, payment["payment_hash"], "Success", 600, 0.1
                    )
                return payment["payment_hash"]
            except Exception as e:
                time.sleep(1)
                continue
        payment = fiber1.get_client().send_payment(
            {
                "target_pubkey": fiber2.get_client().node_info()["node_id"],
                "amount": hex(amount),
                "keysend": True,
                "allow_self_payment": True,
                "udt_type_script": udt,
            }
        )
        if wait:
            self.wait_payment_state(
                fiber1, payment["payment_hash"], "Success", 600, 0.1
            )
        return payment["payment_hash"]

    def get_account_script(self, account_private_key):
        account1 = self.Ckb_cli.util_key_info_by_private_key(account_private_key)
        return {
            "code_hash": "0x9bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8",
            "hash_type": "type",
            "args": account1["lock_arg"],
        }

    def wait_payment_state(
        self, client, payment_hash, status="Success", timeout=360, interval=1
    ):
        for i in range(timeout):
            result = client.get_client().get_payment({"payment_hash": payment_hash})
            if result["status"] == status:
                return
            if result["status"] == "Failed" or result["status"] == "Success":
                raise Exception(f"payment failed, reason:{result['status']}")
            time.sleep(interval)
        raise TimeoutError(
            f"payment:{payment_hash} status did not reach state: {result['status']}, expected:{status} , within timeout period."
        )

    def wait_payment_finished(self, client, payment_hash, timeout=120):
        for i in range(timeout):
            result = client.get_client().get_payment({"payment_hash": payment_hash})
            if result["status"] == "Success" or result["status"] == "Failed":
                return result
            time.sleep(1)
        raise TimeoutError(
            f"status did not reach state {expected_state} within timeout period."
        )

    def get_ln_tx_trace(self, open_channel_tx_hash):
        tx_trace = []
        tx_trace.append(
            {
                "tx_hash": open_channel_tx_hash,
                "msg": self.get_tx_message(open_channel_tx_hash),
            }
        )
        print("open_channel_tx_hash:", open_channel_tx_hash)
        tx, code_hash = self.get_ln_cell_death_hash(open_channel_tx_hash)
        if tx is None:
            return tx_trace
        tx_trace.append({"tx_hash": tx, "msg": self.get_tx_message(tx)})
        while tx is not None:
            tx, new_code_hash = self.get_ln_cell_death_hash(tx)
            print("tx,new_code_hash :", tx, new_code_hash)
            if tx is None:
                continue
            tx_trace.append({"tx_hash": tx, "msg": self.get_tx_message(tx)})
            if (
                new_code_hash
                != "0x3ec6f6b1aa204ef33114476419746476e12dc46182d18a31589ea4f9fee862a9"
            ):
                # print("code_hash changed, stop trace")
                # print("old code_hash:", code_hash, "new code_hash:", new_code_hash)
                tx = None
        # for i in range(len(tx_trace)):
        # print(tx_trace[i])
        return tx_trace

    def get_ln_cell_death_hash(self, tx_hash):
        ckbClient = self.node.getClient()
        tx = ckbClient.get_transaction(tx_hash)
        cellLock = tx["transaction"]["outputs"][0]["lock"]

        txs = ckbClient.get_transactions(
            {
                "script": cellLock,
                "script_type": "lock",
                "script_search_mode": "exact",
            },
            "asc",
            "0xff",
            None,
        )
        if len(txs["objects"]) == 2:
            return txs["objects"][1]["tx_hash"], cellLock["code_hash"]
        return None, None

    def get_fibers_balance_message(self):
        messages = []
        for fiber in self.fibers:
            messages.append(self.get_fiber_balance(fiber))
        for i in range(len(messages)):
            self.logger.info(f"fiber{i} balance:{messages[i]}")

    def get_fiber_graph_balance(self):
        """显示fiber网络中所有通道的余额信息"""
        # 初始化节点映射表
        peer_id_map = {}
        idx = 0
        for fiber in self.fibers:
            peer_id = fiber.get_peer_id()
            peer_id_map[peer_id] = idx
            idx += 1

        # 初始化通道映射表
        channel_maps = {}
        for i in range(len(self.fibers)):
            channel_maps[i] = {}
            for j in range(len(self.fibers)):
                channel_maps[i][j] = {}

        datas = {}
        # 遍历所有fiber节点获取通道信息
        for fiber in self.fibers:
            peer_id = fiber.get_peer_id()
            channels = fiber.get_client().list_channels({})

            # 遍历该节点的所有通道
            for channel in channels["channels"]:
                remote_peer_id = channel["peer_id"]
                # 记录通道信息到映射表
                # channel_maps[peer_id_map[peer_id]][peer_id_map[remote_peer_id]][channel['channel_id']] = {
                #     'local_balance': int(channel["local_balance"], 16),
                #     'remote_balance': int(channel["remote_balance"], 16),
                #     'offered_balance': int(channel["offered_tlc_balance"], 16),
                #     'received_balance': int(channel["received_tlc_balance"], 16),
                #     'udt_type_script': channel.get("funding_udt_type_script", None),
                #     'state': channel["state"]["state_name"]
                # }
                # self.logger.debug(
                #     f"{channel['channel_id']}-{peer_id_map[peer_id]}({int(channel["local_balance"], 16)},{int(channel["offered_tlc_balance"], 16)})-{peer_id_map[remote_peer_id]}({int(channel["remote_balance"], 16)},{int(channel["received_tlc_balance"], 16)}),udt_type_script:{channel.get("funding_udt_type_script", None)}")
                # datas.append(f"{channel['channel_id']}-{peer_id_map[peer_id]}({int(channel["local_balance"], 16)},{int(channel["offered_tlc_balance"], 16)})-{peer_id_map[remote_peer_id]}({int(channel["remote_balance"], 16)},{int(channel["received_tlc_balance"], 16)}),udt_type_script:{channel.get("funding_udt_type_script", None)}")
                datas[channel["channel_id"]] = (
                    f"{channel['channel_id']}-{peer_id_map[peer_id]}({int(channel["local_balance"], 16) / 100000000},{int(channel["offered_tlc_balance"], 16) / 100000000})-{peer_id_map[remote_peer_id]}({int(channel["remote_balance"], 16) / 100000000},{int(channel["received_tlc_balance"], 16) / 100000000}),udt_type_script:{channel.get("funding_udt_type_script", None)}"
                )
        for key in datas:
            self.logger.debug(f"{key}:{datas[key]}")
            # self.logger.debug(datas[key])

    def get_balance_change(self, before_balance, after_balance):
        results = []
        for i in range(len(before_balance)):
            print(
                f"[{i}]ckb:{before_balance[i]['chain']['ckb']} - {after_balance[i]['chain']['ckb']} = {before_balance[i]['chain']['ckb'] - after_balance[i]['chain']['ckb']}"
            )
            print(
                f"[{i}]udt:{before_balance[i]['chain']['udt']} - {after_balance[i]['chain']['udt']} = {before_balance[i]['chain']['udt'] - after_balance[i]['chain']['udt']}"
            )
            results.append(
                {
                    "ckb": before_balance[i]["chain"]["ckb"]
                    - after_balance[i]["chain"]["ckb"],
                    "udt": before_balance[i]["chain"]["udt"]
                    - after_balance[i]["chain"]["udt"],
                }
            )
        return results

    def get_fibers_balance(self):
        balances = []
        for fiber in self.fibers:
            balances.append(self.get_fiber_balance(fiber))
        return balances

    def get_fiber_balance(self, fiber):
        channels = fiber.get_client().list_channels({})
        balance_map = {}

        lock_script = self.get_account_script(fiber.account_private)

        chain_udt_balance = self.udtContract.balance(
            self.node.getClient(),
            self.get_account_script(self.fiber1.account_private)["args"],
            lock_script["args"],
        )

        chain_ckb_balance = int(
            self.node.getClient().get_cells_capacity(
                {
                    "script": lock_script,
                    "script_type": "lock",
                    "script_search_mode": "exact",
                }
            )["capacity"],
            16,
        )

        for i in range(len(channels["channels"])):
            channel = channels["channels"][i]
            if channel["state"]["state_name"] == "CHANNEL_READY":
                key = "ckb"
                if channel["funding_udt_type_script"] is not None:
                    key = channel["funding_udt_type_script"]["args"]
                if balance_map.get(key) is None:
                    balance_map[key] = {
                        "local_balance": 0,
                        "offered_tlc_balance": 0,
                        "received_tlc_balance": 0,
                    }
                balance_map[key]["local_balance"] += int(channel["local_balance"], 16)
                balance_map[key]["offered_tlc_balance"] += int(
                    channel["offered_tlc_balance"], 16
                )
                balance_map[key]["received_tlc_balance"] += int(
                    channel["received_tlc_balance"], 16
                )
        balance_map["chain"] = {}
        balance_map["chain"]["ckb"] = chain_ckb_balance
        balance_map["chain"]["udt"] = chain_udt_balance
        return balance_map

    def calculate_tx_fee(self, balance, fee_list):
        """
        A-B-C
        A -> B 1000
        B -> C 2000
        calculate_tx_fee(1* 100000000, [2000])
        Args:
            balance:
            fee_list:

        Returns:

        """
        before_balance = balance
        fee_list.reverse()
        for fee in fee_list:
            balance += balance * (fee / 1000000)
        return int(balance - before_balance)

    def wait_tx_pool(self, pending_size, try_size=100):
        for i in range(try_size):
            tx_pool_info = self.node.getClient().tx_pool_info()
            current_pending_size = int(tx_pool_info["pending"], 16)
            if current_pending_size < pending_size:
                time.sleep(0.2)
                continue
            return
        raise TimeoutError(
            f"status did not reach state {expected_state} within timeout period."
        )

    def wait_and_check_tx_pool_fee(
        self, fee_rate, check=True, try_size=120, up_and_down_rate=0.1
    ):
        self.wait_tx_pool(1, try_size)
        pool = self.node.getClient().get_raw_tx_pool()
        pool_tx_detail_info = self.node.getClient().get_pool_tx_detail_info(
            pool["pending"][0]
        )
        if check:
            assert int(pool_tx_detail_info["score_sortkey"]["fee"], 16) * 1000 / int(
                pool_tx_detail_info["score_sortkey"]["weight"], 16
            ) <= fee_rate * (1 + up_and_down_rate)

            assert int(pool_tx_detail_info["score_sortkey"]["fee"], 16) * 1000 / int(
                pool_tx_detail_info["score_sortkey"]["weight"], 16
            ) >= fee_rate * (1 - up_and_down_rate)
        return pool["pending"][0]

    def wait_invoice_state(
        self, client, payment_hash, status="Paid", timeout=120, interval=1
    ):
        """
        status:
            1. 状态为Open
            2. 状态为Cancelled
            3. 状态为Expired
            4. 状态为Received
            5. 状态为Paid

        """
        for i in range(timeout):
            result = client.get_client().get_invoice({"payment_hash": payment_hash})
            if result["status"] == status:
                return
            time.sleep(interval)
        raise TimeoutError(
            f"status did not reach state {expected_state} within timeout period."
        )

    def get_tx_message(self, tx_hash):
        tx = self.node.getClient().get_transaction(tx_hash)
        input_cells = []
        output_cells = []

        # self.node.getClient().get_transaction(tx['transaction']['inputs'][])
        for i in range(len(tx["transaction"]["inputs"])):
            pre_cell = self.node.getClient().get_transaction(
                tx["transaction"]["inputs"][i]["previous_output"]["tx_hash"]
            )["transaction"]["outputs"][
                int(tx["transaction"]["inputs"][i]["previous_output"]["index"], 16)
            ]
            pre_cell_outputs_data = self.node.getClient().get_transaction(
                tx["transaction"]["inputs"][i]["previous_output"]["tx_hash"]
            )["transaction"]["outputs_data"][
                int(tx["transaction"]["inputs"][i]["previous_output"]["index"], 16)
            ]
            if pre_cell["type"] is None:
                input_cells.append(
                    {
                        "args": pre_cell["lock"]["args"],
                        "capacity": int(pre_cell["capacity"], 16),
                    }
                )
                continue
            input_cells.append(
                {
                    "args": pre_cell["lock"]["args"],
                    "capacity": int(pre_cell["capacity"], 16),
                    "udt_args": pre_cell["type"]["args"],
                    "udt_capacity": to_int_from_big_uint128_le(pre_cell_outputs_data),
                }
            )

        for i in range(len(tx["transaction"]["outputs"])):
            if tx["transaction"]["outputs"][i]["type"] is None:
                output_cells.append(
                    {
                        "args": tx["transaction"]["outputs"][i]["lock"]["args"],
                        "capacity": int(
                            tx["transaction"]["outputs"][i]["capacity"], 16
                        ),
                    }
                )
                continue
            output_cells.append(
                {
                    "args": tx["transaction"]["outputs"][i]["lock"]["args"],
                    "capacity": int(tx["transaction"]["outputs"][i]["capacity"], 16),
                    "udt_args": tx["transaction"]["outputs"][i]["type"]["args"],
                    "udt_capacity": to_int_from_big_uint128_le(
                        tx["transaction"]["outputs_data"][i]
                    ),
                }
            )
        print({"input_cells": input_cells, "output_cells": output_cells})
        input_cap = 0
        for i in range(len(input_cells)):
            input_cap = input_cap + input_cells[i]["capacity"]
        for i in range(len(output_cells)):
            input_cap = input_cap - output_cells[i]["capacity"]
        return {
            "input_cells": input_cells,
            "output_cells": output_cells,
            "fee": input_cap,
            # 'block_number':  int(tx['tx_status']['block_number'], 16)
            "block_number": (
                int(tx.get("tx_status", {}).get("block_number"), 16)
                if tx.get("tx_status", {}).get("block_number") is not None
                else 0
            ),
        }

    def get_fiber_env(self, new_fiber_count=0):
        # self.logger.debug ckb tip number
        for i in range(new_fiber_count):
            self.start_new_mock_fiber("")
        node_tip_number = self.node.getClient().get_tip_block_number()
        # self.logger.debug fiber data
        fibers_data = []

        for i in range(len(self.fibers)):
            account_capacity = self.Ckb_cli.wallet_get_capacity(
                self.fibers[i].get_account()["address"]["testnet"]
            )
            node_info = self.fibers[i].get_client().node_info()
            channels = self.fibers[i].get_client().list_channels({})
            udt_cells = self.udtContract.list_cell(
                self.node.getClient(),
                self.get_account_script(self.fiber1.account_private)["args"],
                self.get_account_script(self.fibers[i].account_private)["args"],
            )

            fibers_data.append(
                {
                    "account_capacity": account_capacity,
                    "udt_cell": udt_cells,
                    "node_info": node_info,
                    "channels": channels["channels"],
                }
            )
        self.logger.debug(
            "============================================================"
        )
        self.logger.debug(
            "======================== Fiber Env ========================="
        )
        self.logger.debug(
            "============================================================"
        )
        self.logger.debug(
            f"ckb node url: {self.node.rpcUrl}, tip number: {node_tip_number}"
        )
        for i in range(len(self.fibers)):
            self.logger.info(f"--- current fiber: {i}----")
            self.logger.debug(f"url:{self.fibers[i].client.url}")
            self.logger.debug(
                f"account private key: {self.fibers[i].account_private}, ckb balance: {fibers_data[i]['account_capacity']} ,udt balance: {fibers_data[i]['udt_cell']}"
            )
            self.logger.debug(f"path:{self.fibers[i].tmp_path}")
            node_info = fibers_data[i]["node_info"]
            self.logger.debug(
                f"commit_hash:{node_info['commit_hash']}",
            )
            self.logger.debug(f"public_key:{node_info['node_id']}")
            self.logger.debug(f"channel_count:{int(node_info['channel_count'], 16)}")
            self.logger.debug(f"peers_count:{int(node_info['peers_count'], 16)}")
            self.logger.debug(
                f"pending_channel_count:{int(node_info['pending_channel_count'], 16)}"
            )
            channels = fibers_data[i]["channels"]
            for channel in channels:
                channel_id = channel["channel_id"]
                state_name = channel["state"]["state_name"]
                local_balance = int(channel["local_balance"], 16) / 100000000
                offered_tlc_balance = (
                    int(channel["offered_tlc_balance"], 16) / 100000000
                )
                remote_balance = int(channel["remote_balance"], 16) / 100000000
                received_tlc_balance = (
                    int(channel["received_tlc_balance"], 16) / 100000000
                )
                created_at_hex = int(channel["created_at"], 16) / 1000
                created_at = datetime.datetime.fromtimestamp(created_at_hex).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )

                # 打印结果
                self.logger.debug(f"-----Channel ID: {channel_id}-------")
                self.logger.debug(f"State: {state_name}")
                self.logger.debug(f"Local Balance: {local_balance}")
                self.logger.debug(f"Offered TLC Balance: {offered_tlc_balance}")
                self.logger.debug(f"Remote Balance: {remote_balance}")
                self.logger.debug(f"Received TLC Balance: {received_tlc_balance}")
                self.logger.debug(f"Created At: {created_at}")
                self.logger.debug("-" * 40)

    def get_node_hops_info(self, fiber1, fiber2, balance, udt=None):
        node2_id = fiber2.get_client().node_info()["node_id"]
        channels = fiber1.get_client().list_channels({"peer_id": fiber2.get_peer_id()})
        hops_info = []
        for channel in channels["channels"]:
            if channel["funding_udt_type_script"] != udt:
                self.logger.debug(
                    f"{channel['channel_outpoint']}:funding_udt_type_script skip"
                )
                continue
            # check balance
            if int(channel["local_balance"], 16) < balance:
                self.logger.debug(f"{channel['channel_outpoint']}:local_balance skip")
                continue
            # check is true
            if channel["state"]["state_name"] != "CHANNEL_READY":
                self.logger.debug(
                    f"{channel['channel_outpoint']}:channel state skip,{channel['state']['state_name']}"
                )
                continue
            if not channel["enabled"]:
                self.logger.debug(
                    f"{channel['channel_outpoint']}:channel state skip,{channel['enabled']}"
                )
                continue

            hops_info.append(
                {"pubkey": node2_id, "channel_outpoint": channel["channel_outpoint"]}
            )
        return hops_info

    def get_fiber_message(self, fiber):
        channels = fiber.get_client().list_channels({})
        channels = channels["channels"]
        node_info = fiber.get_client().node_info()
        graph_channels = fiber.get_client().graph_channels()
        graph_nodes = fiber.get_client().graph_nodes()
        self.logger.debug(
            f"commit_hash:{node_info['commit_hash']}",
        )
        self.logger.debug(f"public_key:{node_info['public_key']}")
        self.logger.debug(f"peer_id:{node_info['peer_id']}")
        self.logger.debug(f"channel_count:{int(node_info['channel_count'], 16)}")
        self.logger.debug(f"peers_count:{int(node_info['peers_count'], 16)}")
        self.logger.debug(
            f"pending_channel_count:{int(node_info['pending_channel_count'], 16)}"
        )
        self.logger.debug("---------channel------")
        # 处理每个通道
        for channel in channels:
            channel_id = channel["channel_id"]
            peer_id = channel["peer_id"]
            state_name = channel["state"]["state_name"]
            local_balance = int(channel["local_balance"], 16) / 100000000
            offered_tlc_balance = int(channel["offered_tlc_balance"], 16) / 100000000
            remote_balance = int(channel["remote_balance"], 16) / 100000000
            received_tlc_balance = int(channel["received_tlc_balance"], 16) / 100000000
            created_at_hex = int(channel["created_at"], 16) / 1000000
            created_at = datetime.datetime.fromtimestamp(created_at_hex).strftime(
                "%Y-%m-%d %H:%M:%S"
            )

            # 打印结果
            self.logger.debug(f"Channel ID: {channel_id}")
            self.logger.debug(f"Peer ID: {peer_id}")
            self.logger.debug(f"State: {state_name}")
            self.logger.debug(f"Local Balance: {local_balance}")
            self.logger.debug(f"Offered TLC Balance: {offered_tlc_balance}")
            self.logger.debug(f"Remote Balance: {remote_balance}")
            self.logger.debug(f"Received TLC Balance: {received_tlc_balance}")
            self.logger.debug(f"Created At: {created_at}")
            self.logger.debug("-" * 40)

    def generate_random_preimage(self):
        hash_str = "0x"
        for _ in range(64):
            hash_str += hex(random.randint(0, 15))[2:]
        return hash_str

    def generate_random_str(self, num):
        hash_str = "0x"
        for _ in range(num):
            hash_str += hex(random.randint(0, 15))[2:]
        return hash_str

    def wait_fibers_pending_tlc_eq0(self, fiber, wait_times=30):
        for i in range(wait_times):
            fiber_balance = self.get_fiber_balance(fiber)
            if fiber_balance["ckb"]["offered_tlc_balance"] != 0:
                time.sleep(1)
                continue
            if fiber_balance["ckb"]["received_tlc_balance"] != 0:
                time.sleep(1)
                continue
            return
        raise TimeoutError(f"{fiber_balance}")

    def wait_graph_channels_sync(self, fiber, channels_count, timeout=120):
        """
        等待图形通道同步
        :param fiber: Fiber实例
        :param channels_count: 期望的通道数量
        :param timeout: 超时时间，单位为秒
        """
        for i in range(timeout):
            graph_channels = fiber.get_client().graph_channels()["channels"]
            if len(graph_channels) == channels_count:
                print(f"Graph channels synced successfully,cost time:{i}")
                return True
            time.sleep(1)
        raise TimeoutError(
            f"Graph channels did not sync to {channels_count} within {timeout} seconds."
        )

    def add_time_and_generate_epoch(self, hour, epoch):
        change_time(hour)
        client = self.node.getClient()
        header = client.get_tip_header()
        before_median_time = client.get_block_median_time(header["hash"])
        self.node.getClient().generate_epochs(hex(epoch), 0)
        header = client.get_tip_header()
        median_time = client.get_block_median_time(header["hash"])
        print(
            "before_median_time datetime:",
            datetime.fromtimestamp(int(before_median_time, 16) / 1000),
        )
        print(
            "median_time datetime:", datetime.fromtimestamp(int(median_time, 16) / 1000)
        )

    def add_time_and_generate_block(self, hour, block_num):
        change_time(hour)
        client = self.node.getClient()
        header = client.get_tip_header()
        before_median_time = client.get_block_median_time(header["hash"])
        for i in range(block_num):
            self.Miner.miner_with_version(self.node, "0x0")
        header = client.get_tip_header()
        median_time = client.get_block_median_time(header["hash"])
        print(
            "before_median_time datetime:",
            datetime.fromtimestamp(int(before_median_time, 16) / 1000),
        )
        print(
            "median_time datetime:", datetime.fromtimestamp(int(median_time, 16) / 1000)
        )

    def get_commit_cells(self):
        #         code_hash: 0x4d937548b31beb7e6919e05e3f5c8d6f46b13a7db49254e6867bfb0d4bc7c748
        #         hash_type: type
        #         args: 0x
        tip_number = hex(self.node.getClient().get_tip_block_number())
        return self.node.getClient().get_cells(
            {
                "script": {
                    "code_hash": "0x3ec6f6b1aa204ef33114476419746476e12dc46182d18a31589ea4f9fee862a9",
                    "hash_type": "type",
                    "args": "0x",
                },
                "script_type": "lock",
                "script_search_mode": "prefix",
                "filter": {"block_range": [self.beginNum, "0xffffffffff"]},
            },
            "asc",
            "0xfff",
            None,
        )["objects"]

    @classmethod
    def restore_time(cls):
        """恢复系统时间"""
        print("开始恢复系统时间...")
        print("current time:", time.time())
        print("current datetime:", datetime.now())

        try:
            # 检测是否在Docker容器中运行
            is_docker = os.path.exists("/.dockerenv")

            if is_docker:
                # 在Docker容器中，尝试从网络同步时间
                cmd = "ntpdate -s time.nist.gov"
                print(f"Docker环境 - 执行命令: {cmd}")
            else:
                # 在宿主机上，使用sntp同步网络时间
                cmd = f"echo hyperchain | sudo -S sntp -sS time.apple.com"
                print(f"宿主机环境 - 执行命令: sudo sntp -sS time.apple.com")

            # 执行系统命令
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

            if result.returncode == 0:
                print("系统时间恢复成功")
            else:
                print(f"系统时间恢复失败: {result.stderr}")
                # 如果网络同步失败，尝试手动减去1小时
                print("尝试手动恢复时间（减去1小时）...")
                current_time = datetime.now()
                restore_time_dt = current_time - timedelta(hours=1)
                time_str = restore_time_dt.strftime("%m%d%H%M%Y")

                if is_docker:
                    cmd = f"date {time_str}"
                else:
                    cmd = f"echo '{password}' | sudo -S date {time_str}"

                result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
                if result.returncode == 0:
                    print("手动时间恢复成功")
                else:
                    print(f"手动时间恢复失败: {result.stderr}")

        except Exception as e:
            print(f"恢复系统时间时发生错误: {e}")

        print("restored time:", time.time())
        print("restored datetime:", datetime.now())


def check_port(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)  # 设置超时时间为1秒
        try:
            s.connect(("127.0.0.1", port))
            return True  # 端口开放
        except (socket.timeout, socket.error):
            return False  # 端口未开放


def tar_file(src_tar, dec_data):
    run_command(f"tar -xvf {src_tar} -C {dec_data}")
