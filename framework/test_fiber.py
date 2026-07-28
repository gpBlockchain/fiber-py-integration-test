import fcntl
import shutil
import socket
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


def wait_for_port(port, timeout=30, open=True):
    """Wait for a port to open or close.

    Args:
        port: Port number to check.
        timeout: Max seconds to wait.
        open: If True, wait until port is open; if False, wait until port is closed.

    Raises:
        TimeoutError: If the port does not reach the expected state within timeout.
    """
    port = int(port)
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            result = s.connect_ex(("127.0.0.1", port))
            is_open = result == 0
        if is_open == open:
            return
        time.sleep(0.3)
    state = "open" if open else "closed"
    raise TimeoutError(f"Port {port} did not become {state} within {timeout}s")


class FiberConfigPath(Enum):
    CURRENT_DEV = (
        "/source/fiber/dev_config_3.yml.j2",
        "download/fiber/current/fnn",
    )

    CURRENT_CCH = (
        "/source/fiber/dev_config_cch.yml.j2",
        "download/fiber/current/fnn",
    )

    CURRENT_DEV_DEBUG = (
        "/source/fiber/dev_config_3.yml.j2",
        "download/fiber/current/fnn.debug",
    )

    CURRENT_MAINNET = (
        "/source/template/fiber/mainnet_config_3.yml.j2",
        "download/fiber/0.9.0/fnn",
    )

    CURRENT_TESTNET = (
        "/source/template/fiber/testnet_config_3.yml.j2",
        "download/fiber/0.9.0/fnn",
    )

    V070_DEV = (
        "/source/fiber/dev_config_3.yml.j2",
        "download/fiber/0.7.0/fnn",
    )

    V081_DEV = (
        "/source/fiber/dev_config_3.yml.j2",
        "download/fiber/0.8.1/fnn",
    )

    V061_DEV = (
        "/source/fiber/dev_config_3.yml.j2",
        "download/fiber/0.6.1/fnn",
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
        os.makedirs(target_dir, exist_ok=True)
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
            f"echo YES | RUST_LOG=info,fnn=debug {get_project_root()}/{self.fiber_config_enum.fiber_bin_path}-migrate -d {self.tmp_path}/fiber"
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
        # wait rpc port open
        wait_for_port(self.rpc_port, timeout=300, open=True)
        print("start fiber client ")

    def stop(self):
        run_command(
            "kill $(lsof -i:" + self.rpc_port + " | grep LISTEN | awk '{print $2}')",
            False,
        )
        wait_for_port(self.rpc_port, timeout=30, open=False)
        self._wait_store_lock_released()

    def _wait_store_lock_released(self, timeout=30):
        """After the RPC port closes the fnn process may still be flushing
        RocksDB and holding ``<tmp_path>/fiber/store/LOCK``. If start() is
        called too soon the new process loses the race for the lock and exits
        with "Resource temporarily unavailable". Poll the lock until it is
        actually released so subsequent start() is safe."""
        lock_path = f"{self.tmp_path}/fiber/store/LOCK"
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                fd = open(lock_path, "r+")
            except FileNotFoundError:
                return
            try:
                fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.lockf(fd, fcntl.LOCK_UN)
                return
            except (BlockingIOError, OSError):
                time.sleep(0.5)
            finally:
                fd.close()
        raise TimeoutError(f"RocksDB LOCK still held at {lock_path} after {timeout}s")

    def force_stop(self):
        # run_command(f"kill -9 $(lsof -t -i:{self.rpc_port} | head -1)", False)
        run_command(
            "kill -9 $(lsof -i:" + self.rpc_port + " | grep LISTEN | awk '{print $2}')",
            False,
        )
        wait_for_port(self.rpc_port, timeout=30, open=False)

    def clean(self):
        run_command("rm -rf {tmp_path}".format(tmp_path=self.tmp_path))

    def get_client(self) -> FiberRPCClient:
        return self.client

    def get_log_file(self):
        pass

    def get_pubkey(self):
        return self.get_client().node_info()["pubkey"]

    def get_pubkey(self):
        return self.get_client().node_info()["pubkey"]

    def get_account(self):
        return framework.helper.ckb_cli.util_key_info_by_private_key(
            self.account_private
        )

    def connect_peer(self, node):
        node_info = node.get_client().node_info()
        address = node_info["addresses"][0].replace("0。0.0.0", "127.0.0.1")
        result = self.client.connect_peer({"address": address})
        remote_pubkey = node_info.get("pubkey")
        if remote_pubkey is None:
            return result
        deadline = time.time() + 30
        while time.time() < deadline:
            peers = self.client.list_peers().get("peers") or []
            if any(peer.get("pubkey") == remote_pubkey for peer in peers):
                return result
            time.sleep(0.5)
        return result
