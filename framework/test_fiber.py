import shutil
from enum import Enum
import time
import framework.helper.ckb_cli
from framework.util import (
    create_config_file,
    get_project_root,
    run_command,
)
from framework.helper.contract import get_ckb_contract_codehash

import os
from framework.fiber_rpc import FiberRPCClient
from framework.config import get_tmp_path


class FiberConfigPath(Enum):
    CURRENT_DEV = (
        "/source/fiber/dev_config.yml.j2",
        # "download/fiber/0.5.0/fnn",
        "download/fiber/0.6.0/fnn",
    )

    CURRENT_DEV_DEBUG = (
        "/source/fiber/dev_config.yml.j2",
        "download/fiber/current/fnn.debug",
    )

    CURRENT_TESTNET = (
        "/source/template/fiber/testnet_config_2.yml.j2",
        "download/fiber/quake4/fnn",
    )

    V050_DEV = (
        "/source/template/fiber/dev_config_2.yml.j2",
        "download/fiber/0.5.0/fnn",
    )

    def __init__(self, fiber_config_path, fiber_bin_path):
        self.fiber_config_path = fiber_config_path
        self.fiber_bin_path = fiber_bin_path

    def __str__(self):
        return self.fiber_bin_path.split("/")[-2]


class Fiber:

    @classmethod
    def init_by_port(
        cls,
        fiber_config_path: FiberConfigPath,
        account_private,
        tmp_path,
        rpc_port,
        p2p_port,
    ):
        config = {
            "fiber_listening_addr": f"/ip4/127.0.0.1/tcp/{p2p_port}",
            "rpc_listening_addr": f"127.0.0.1:{rpc_port}",
        }
        return Fiber(fiber_config_path, account_private, tmp_path, config)

    def __init__(
        self,
        fiber_config_path: FiberConfigPath,
        account_private,
        tmp_path,
        config=None,
    ):
        if config is None:
            config = {
                "fiber_listening_addr": "/ip4/127.0.0.1/tcp/8228",
                "rpc_listening_addr": "127.0.0.1:8227",
            }
        self.fiber_config_enum = fiber_config_path
        self.fiber_config = {
            "fiber_listening_addr": config["fiber_listening_addr"],
            "rpc_listening_addr": config["rpc_listening_addr"],
        }
        self.account_private = account_private
        self.tmp_path = f"{get_tmp_path()}/{tmp_path}"
        self.fiber_config_path = f"{self.tmp_path}/config.yml"
        self.client = FiberRPCClient(f"http://{config['rpc_listening_addr']}")
        self.rpc_port = config["rpc_listening_addr"].split(":")[-1]

    def prepare(self, update_config=None):
        if update_config is None:
            update_config = {}
        self.fiber_config.update(update_config)
        # check file exist
        create_config_file(
            self.fiber_config,
            self.fiber_config_enum.fiber_config_path,
            self.fiber_config_path,
        )
        shutil.copy(
            "{root_path}/source/fiber/dev.toml".format(root_path=get_project_root()),
            self.tmp_path,
        )
        target_dir = os.path.join(self.tmp_path, "ckb")
        os.makedirs(target_dir, exist_ok=True)  # 创建文件夹，如果已存在则不报错
        # if os.path.exists(
        #     f"{get_project_root()}/source/fiber/keys/{self.account_private.replace('0x', '')}"
        # ):
        #     shutil.copy(
        #         f"{get_project_root()}/source/fiber/keys/{self.account_private.replace('0x', '')}",
        #         f"{self.tmp_path}/ckb/key",
        #     )
        # else:
        #     with open(f"{self.tmp_path}/ckb/key", "w") as f:
        #         f.write(self.account_private.replace("0x", ""))
        with open(f"{self.tmp_path}/ckb/key", "w") as f:
            f.write(self.account_private.replace("0x", ""))

        with open(f"{self.tmp_path}/ckb/key.bak", "w") as f:
            f.write(self.account_private.replace("0x", ""))
        # node

    def get_contract_env_map(self, node):
        hashs = node.list_hashes()
        contract_map = {
            "NEXT_PUBLIC_CKB": "DEV",
            "NEXT_PUBLIC_CKB_GENESIS_TX_0": hashs["ckb_dev"]["system_cells"][0][
                "tx_hash"
            ],
            "NEXT_PUBLIC_CKB_GENESIS_TX_1": hashs["ckb_dev"]["dep_groups"][0][
                "tx_hash"
            ],
        }

        for i in range(4, len(hashs["ckb_dev"]["system_cells"])):
            cell = hashs["ckb_dev"]["system_cells"][i]
            contract_name = (
                cell["path"].split("/")[-1].replace(")", "").replace("-", "_").upper()
            )
            contract_map[f"NEXT_PUBLIC_{contract_name}_CODE_HASH"] = cell["data_hash"]
            contract_map[f"NEXT_PUBLIC_{contract_name}_TYPE_HASH"] = cell["type_hash"]
            contract_map[f"NEXT_PUBLIC_{contract_name}_TX_HASH"] = cell["tx_hash"]
            contract_map[f"NEXT_PUBLIC_{contract_name}_TX_INDEX"] = str(cell["index"])
        for i in range(2, len(hashs["ckb_dev"]["dep_groups"])):
            cell = hashs["ckb_dev"]["dep_groups"][i]
            contract_name = (
                cell["included_cells"][0]
                .split("/")[-1]
                .replace(")", "")
                .replace("-", "_")
                .upper()
            )
            code_hash = get_ckb_contract_codehash(
                cell["tx_hash"], int(cell["index"]), False, node.rpcUrl
            )
            contract_map[f"NEXT_PUBLIC_{contract_name}_DEP_GROUP_CODE_HASH"] = code_hash
            contract_map[f"NEXT_PUBLIC_{contract_name}_DEP_GROUP_TX_HASH"] = cell[
                "tx_hash"
            ]
            contract_map[f"NEXT_PUBLIC_{contract_name}_DEP_GROUP_TX_INDEX"] = str(
                cell["index"]
            )
        return contract_map

    def read_ckb_key(self):
        with open(f"{self.tmp_path}/ckb/key.bak") as f:
            key = f.read()
        self.account_private = f"0x{key}"
        return self.account_private

    def migration(self):
        run_command(
            f"echo YES | RUST_LOG=info,fnn=debug {get_project_root()}/{self.fiber_config_enum.fiber_bin_path}-migrate -p {self.tmp_path}/fiber/store"
        )

    def start(
        self,
        password="password0",
        fnn_log_level="debug",
        rpc_biscuit_public_key=None,
    ):
        # env_map = dict(os.environ)  # Make a copy of the current environment
        # if node:,
        #     contract_map = self.get_contract_env_map(node)
        #     env_map.update(contract_map)
        # for key in env_map:
        #     print(f"{key}={env_map[key]}")
        rpc_biscuit_public_key_option = ""
        if rpc_biscuit_public_key != None:
            rpc_biscuit_public_key_option = (
                f" --rpc-biscuit-public-key {rpc_biscuit_public_key}"
            )
        run_command(
            f" FIBER_SECRET_KEY_PASSWORD='{password}' RUST_LOG=info,fnn={fnn_log_level} {get_project_root()}/{self.fiber_config_enum.fiber_bin_path} -c {self.tmp_path}/config.yml -d {self.tmp_path} {rpc_biscuit_public_key_option}  >> {self.tmp_path}/node.log 2>&1 &"
            # env=env_map,
        )
        # wait rpc start
        time.sleep(0.1)
        print("start fiber client ")

    def stop(self):
        run_command(
            "kill $(lsof -i:" + self.rpc_port + " | grep LISTEN | awk '{print $2}')",
            False,
        )
        time.sleep(1)

    def force_stop(self):
        # run_command(f"kill -9 $(lsof -t -i:{self.rpc_port} | head -1)", False)
        run_command(
            "kill -9 $(lsof -i:" + self.rpc_port + " | grep LISTEN | awk '{print $2}')",
            False,
        )
        time.sleep(3)

    def clean(self):
        run_command("rm -rf {tmp_path}".format(tmp_path=self.tmp_path))

    def get_client(self) -> FiberRPCClient:
        return self.client

    def get_log_file(self):
        pass

    def get_peer_id(self):
        return self.get_client().node_info()["addresses"][0].split("/")[-1]

    def get_account(self):
        return framework.helper.ckb_cli.util_key_info_by_private_key(
            self.account_private
        )

    def connect_peer(self, node):
        address = (
            node.get_client()
            .node_info()["addresses"][0]
            .replace("0。0.0.0", "127.0.0.1")
        )
        return self.client.connect_peer({"address": address})
