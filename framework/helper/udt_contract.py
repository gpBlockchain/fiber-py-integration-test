from framework.config import UDT_CONTRACT_PATH
from framework.helper.contract import deploy_ckb_contract, CkbContract
from framework.helper.miner import miner_until_tx_committed
from framework.helper.ckb_cli import util_key_info_by_private_key
from framework.helper.contract import invoke_ckb_contract

from framework.test_node import CkbNode
from framework.util import (
    ckb_hash_script,
    to_big_uint128_le_compatible,
    to_int_from_big_uint128_le,
)
from framework.helper.contract import get_ckb_contract_codehash


class UdtContract(CkbContract):

    def __init__(self, contract_hash=None, contract_tx_index=None):
        self.contract_hash = contract_hash
        self.contract_tx_index = contract_tx_index
        if contract_hash is None:
            self.deployed = False
        else:
            self.deployed = True
        self.contract_path = UDT_CONTRACT_PATH
        self.method = {"demo": {"args": "0x", "data": "0x"}}

    def deploy(self, account_private, node: CkbNode):
        if self.deployed:
            return
        self.contract_hash = deploy_ckb_contract(
            account_private, self.contract_path, api_url=node.getClient().url
        )
        self.contract_tx_index = 0
        miner_until_tx_committed(node, self.contract_hash)
        self.deployed = True

    def get_deploy_hash_and_index(self) -> (str, int):
        if not self.deployed:
            raise Exception("pls deploy first")
        return self.contract_hash, self.contract_tx_index

    def get_code_hash(self, type_id, api):
        return get_ckb_contract_codehash(
            self.contract_hash, self.contract_tx_index, type_id, api
        )

    def get_owner_arg_by_lock_arg(self, lock_arg):
        return ckb_hash_script(lock_arg)

    @classmethod
    def issue(cls, own_arg, amount) -> (str, str):
        return ckb_hash_script(own_arg), to_big_uint128_le_compatible(amount)

    @classmethod
    def transfer(cls, own_arg, amount) -> (str, str):
        return ckb_hash_script(own_arg), to_big_uint128_le_compatible(amount)

    def balance(self, client, own_arg, query_arg):
        cells = self.list_cell(client, own_arg, query_arg)
        balance = 0
        for cell in cells:
            balance += cell["balance"]
        return balance

    def list_cell(self, client, own_arg, query_arg):
        code_hash = get_ckb_contract_codehash(
            self.contract_hash,
            self.contract_tx_index,
            enable_type_id=True,
            api_url=client.url,
        )
        cells = client.get_cells(
            {
                "script": {
                    "code_hash": "0x9bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8",
                    "hash_type": "type",
                    "args": query_arg,
                },
                "script_type": "lock",
                "filter": {
                    "script": {
                        "code_hash": code_hash,
                        "hash_type": "type",
                        "args": ckb_hash_script(own_arg),
                    }
                },
            },
            "asc",
            "0x64",
            None,
        )
        info = []
        for cell in cells["objects"]:
            info.append(
                {
                    "input_cell": {
                        "tx_hash": cell["out_point"]["tx_hash"],
                        "index": int(cell["out_point"]["index"], 16),
                    },
                    "balance": to_int_from_big_uint128_le(cell["output_data"]),
                    "ckb": int(cell["output"]["capacity"], 16),
                }
            )
        return info

    def get_arg_and_data(self, key) -> (str, str):
        if key not in self.method.keys():
            # return "0x0","0x0"
            raise Exception("key not exist in method list")
        return self.method[key]["args"], self.method[key]["data"]


def issue_udt_tx(udt_contract, rpc_url, owner_private, account_private, amount):
    account = util_key_info_by_private_key(owner_private)
    account1 = util_key_info_by_private_key(account_private)
    invoke_arg, invoke_data = udt_contract.issue(account["lock_arg"], amount)
    tx_hash = invoke_ckb_contract(
        account_private=owner_private,
        contract_out_point_tx_hash=udt_contract.contract_hash,
        contract_out_point_tx_index=udt_contract.contract_tx_index,
        type_script_arg=invoke_arg,
        hash_type="type",
        data=invoke_data,
        fee=1000,
        api_url=rpc_url,
        cell_deps=[],
        input_cells=[],
        output_lock_arg=account1["lock_arg"],
    )
    return tx_hash
