"""
framework config file

"""

from framework.util import get_project_root

TMP_PATH = "tmp"

ACCOUNT_PRIVATE_1 = "0xd00c06bfd800d27397002dca6fb0993d5ba6399b4238b2f29ee9deb97593d2bc"
ACCOUNT_PRIVATE_2 = "0x63d86723e08f0f813a36ce6aa123bb2289d90680ae1e99d4de8cdb334553f24d"
MINER_PRIVATE_1 = "0x98400f6a67af07025f5959af35ed653d649f745b8f54bf3f07bef9bd605ee946"
DEFAULT_MIN_DEPOSIT_CKB = 98 * 100000000
DEFAULT_MIN_DEPOSIT_UDT = 179 * 100000000
# private key 98400f6a67af07025f5959af35ed653d649f745b8f54bf3f07bef9bd605ee946
#   key: "98400f6a67af07025f5959af35ed653d649f745b8f54bf3f07bef9bd605ee946"
#   code_hash: "0x9bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8"
#   args: "0x8883a512ee2383c01574a328f60eeccbb4d78240"
#   hash_type: "type"
#   message: "0x"

CKB_DEFAULT_CONFIG = {
    # 'ckb_chain_spec': '{ bundled = "specs/mainnet.toml" }',
    "ckb_chain_spec": '{ file = "dev.toml" }',
    "ckb_network_listen_addresses": ["/ip4/0.0.0.0/tcp/8115"],
    "ckb_rpc_listen_address": "127.0.0.1:8114",
    "ckb_rpc_modules": [
        "Net",
        "Pool",
        "Miner",
        "Chain",
        "Stats",
        "Subscription",
        "Experiment",
        "Debug",
        "IntegrationTest",
    ],
    "ckb_block_assembler_code_hash": "0x9bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8",
    "ckb_block_assembler_args": "0x8883a512ee2383c01574a328f60eeccbb4d78240",
    "ckb_block_assembler_hash_type": "type",
    "ckb_block_assembler_message": "0x",
}

CKB_MINER_CONFIG = {
    "ckb_miner_rpc_url": "127.0.0.1:8114",
    "ckb_chain_spec": '{ file = "dev.toml" }',
}

# contract
ALWAYS_SUCCESS_CONTRACT_PATH = f"{get_project_root()}/source/contract/always_success"
SPAWN_CONTRACT_PATH = f"{get_project_root()}/source/contract/test_cases/spawn_demo"
UDT_CONTRACT_PATH = f"{get_project_root()}/source/contract/XUDTType"


def get_tmp_path():
    """
    tmp file
    :return:
    """
    return "{path}/{tmp}".format(path=get_project_root(), tmp=TMP_PATH)
