import time
from enum import Enum
from framework.util import (
    create_config_file,
    get_project_root,
    run_command,
    get_ckb_configs,
)
from framework.config import get_tmp_path, CKB_DEFAULT_CONFIG, CKB_MINER_CONFIG
from framework.rpc import RPCClient
import shutil
from websocket import create_connection, WebSocket
import toml


class CkbNodeConfigPath(Enum):
    CURRENT_TEST = (
        "source/template/ckb/v118/ckb.toml.j2",
        "source/template/ckb/v118/ckb-miner.toml.j2",
        "source/template/ckb/v118/specs/dev.toml",
        "download/0.203.0",
    )

    CURRENT_FIBER = (
        "source/template/ckb/v118/ckb.toml.j2",
        "source/template/ckb/v118/ckb-miner.toml.j2",
        "source/template/ckb/fiber/dev.toml",
        "download/0.201.0",
    )
    TESTNET = (
        "source/template/ckb/v118/ckb.toml.j2",
        "source/template/ckb/v118/ckb-miner.toml.j2",
        "source/template/specs/testnet.toml.j2",
        "download/0.119.0",
    )

    def __init__(
        self, ckb_config_path, ckb_miner_config_path, ckb_spec_path, ckb_bin_path
    ):
        self.ckb_config_path = ckb_config_path
        self.ckb_miner_config_path = ckb_miner_config_path
        self.ckb_spec_path = ckb_spec_path
        self.ckb_bin_path = ckb_bin_path

    def __str__(self):
        return self.ckb_bin_path.split("/")[-1]


class CkbNode:
    @classmethod
    def init_dev_by_port(
        cls, ckb_node_path_enum: CkbNodeConfigPath, dec_dir, rpc_port, p2p_port
    ):
        ckb_config, ckb_miner_config, ckb_specs_config = get_ckb_configs(
            p2p_port, rpc_port
        )
        return CkbNode(
            ckb_node_path_enum, dec_dir, ckb_config, ckb_miner_config, ckb_specs_config
        )

    def __init__(
        self,
        ckb_node_path_enum: CkbNodeConfigPath,
        dec_dir,
        ckb_config=CKB_DEFAULT_CONFIG,
        ckb_miner_config=CKB_MINER_CONFIG,
        ckb_specs_config={},
    ):
        self.ckb_config_path = ckb_node_path_enum
        self.dec_path = ckb_config
        self.ckb_config = ckb_config.copy()
        self.ckb_miner_config = ckb_miner_config
        self.ckb_specs_config = ckb_specs_config
        self.ckb_dir = "{tmp}/{ckb_dir}".format(tmp=get_tmp_path(), ckb_dir=dec_dir)
        self.ckb_bin_path = f"{self.ckb_dir}/ckb"
        self.ckb_toml_path = f"{self.ckb_dir}/ckb.toml"
        self.ckb_miner_toml_path = f"{self.ckb_dir}/ckb-miner.toml"
        self.ckb_specs_config_path = f"{self.ckb_dir}/dev.toml"
        self.ckb_pid = -1
        self.ckb_miner_pid = -1
        self.rpcUrl = "http://{url}".format(
            url=self.ckb_config.get("ckb_rpc_listen_address", "127.0.0.1:8114")
        )
        self.client = RPCClient(self.rpcUrl)

    def __str__(self):
        return self.ckb_config_path

    def get_peer_id(self):
        return self.client.local_node_info()["node_id"]

    def get_peer_address(self):
        info = self.client.local_node_info()
        return info["addresses"][0]["address"].replace("0.0.0.0", "127.0.0.1")

    def get_connected_count(self):
        return int(self.getClient().local_node_info()["connections"], 16)

    def connected(self, node):
        peer_id = node.get_peer_id()
        peer_address = node.get_peer_address()
        print("add node response:", self.getClient().add_node(peer_id, peer_address))

    def connected_ws(self, node):
        peer_id = node.get_peer_id()
        peer_address = node.get_peer_address()
        if "ws" not in peer_address:
            peer_address = peer_address + "/ws"
        print("add node response:", self.getClient().add_node(peer_id, peer_address))

    def connected_all_address(self, node):
        peer_id = node.get_peer_id()
        node_info = node.client.local_node_info()
        for address in node_info["addresses"]:
            peer_address = address["address"].replace("0.0.0.0", "127.0.0.1")
            print(
                "add node response:", self.getClient().add_node(peer_id, peer_address)
            )

    def getClient(self) -> RPCClient:
        return self.client

    def restart(self, config={}, clean_data=False):
        self.stop()
        self.stop_miner()

        if clean_data:
            # rm -rf indexer
            run_command(f"cd {self.ckb_dir} && rm -rf data/indexer")
            # rm -rf network
            run_command(f"cd {self.ckb_dir} && rm -rf data/network")
            # rm -rf tx_pool
            run_command(f"cd {self.ckb_dir} && rm -rf data/tx_pool")
            # rm -rf tmp
            run_command(f"cd {self.ckb_dir} && rm -rf data/tmp")

        self.start()

    def start(self):
        self.ckb_pid = run_command(
            "cd {ckb_dir} && ./ckb run --indexer  --skip-spec-check > node.log 2>&1 &".format(
                ckb_dir=self.ckb_dir
            )
        )
        # //todo replace by rpc
        time.sleep(3)

    def startWithRichIndexer(self):
        """
        support richIndexer
        Returns:

        """
        self.ckb_pid = run_command(
            "cd {ckb_dir} && ./ckb run --rich-indexer  --skip-spec-check > node.log 2>&1 &".format(
                ckb_dir=self.ckb_dir
            )
        )
        time.sleep(3)

    def stop(self):
        self.stop_miner()
        # run_command("kill {pid}".format(pid=self.ckb_pid))
        # self.ckb_pid = -1
        port = self.rpcUrl.split(":")[-1]
        run_command(
            f"kill $(lsof -i:{port} | grep LISTEN | awk '{{print $2}}')",
            check_exit_code=False,
        )
        self.ckb_pid = -1
        time.sleep(3)

    def prepare(
        self,
        other_ckb_config={},
        other_ckb_miner_config={},
        other_ckb_spec_config={},
        check_file=False,
    ):
        self.ckb_config.update(other_ckb_config)
        self.ckb_miner_config.update(other_ckb_miner_config)
        self.ckb_specs_config.update(other_ckb_spec_config)
        # check file exist
        create_config_file(
            self.ckb_config, self.ckb_config_path.ckb_config_path, self.ckb_toml_path
        )

        create_config_file(
            self.ckb_miner_config,
            self.ckb_config_path.ckb_miner_config_path,
            self.ckb_miner_toml_path,
        )
        if ".j2" in self.ckb_config_path.ckb_spec_path:
            create_config_file(
                self.ckb_specs_config,
                self.ckb_config_path.ckb_spec_path,
                self.ckb_specs_config_path,
            )
        else:
            shutil.copy(
                "{root_path}/{spec_path}".format(
                    root_path=get_project_root(),
                    spec_path=self.ckb_config_path.ckb_spec_path,
                ),
                "{ckb_dir}/dev.toml".format(ckb_dir=self.ckb_dir),
            )

        shutil.copy(
            "{root_path}/{ckb_bin_path}/ckb".format(
                root_path=get_project_root(),
                ckb_bin_path=self.ckb_config_path.ckb_bin_path,
            ),
            self.ckb_dir,
        )

        shutil.copy(
            "{root_path}/{ckb_bin_path}/ckb-cli".format(
                root_path=get_project_root(),
                ckb_bin_path=self.ckb_config_path.ckb_bin_path,
            ),
            self.ckb_dir,
        )

        shutil.copy(
            "{root_path}/source/template/ckb/default.db-options".format(
                root_path=get_project_root()
            ),
            self.ckb_dir,
        )

    def clean(self):
        run_command("rm -rf {ckb_dir}".format(ckb_dir=self.ckb_dir))

    def start_miner(self):
        if self.ckb_miner_pid != -1:
            return
        self.ckb_miner_pid = run_command(
            "cd {ckb_dir} && ./ckb miner > ckb.miner.log 2>&1  &".format(
                ckb_dir=self.ckb_dir
            )
        )
        # replace check height upper
        time.sleep(3)

    def stop_miner(self):
        if self.ckb_miner_pid == -1:
            return
        try:
            run_command("kill {pid}".format(pid=self.ckb_miner_pid))
            self.ckb_miner_pid = -1
        except:
            self.ckb_miner_pid = -1

    def version(self):
        pass

    def list_hashes(self):
        ret = run_command(
            "cd {ckb_dir} && ./ckb list-hashes".format(ckb_dir=self.ckb_dir)
        )
        data = toml.loads(ret)
        return data

    def subscribe_telnet(self, topic, other_url=None):
        pass
        # new_tip_header | new_tip_block | new_transaction | proposed_transaction | rejected_transaction
        # if "ckb_tcp_listen_address" not in self.ckb_config.keys():
        #     raise Exception("not set ckb_ws_listen_address")
        # ckb_tcp_listen_address = self.ckb_config["ckb_tcp_listen_address"]
        # if other_url is not None:
        #     ckb_tcp_listen_address = other_url
        # # get host
        # host = ckb_tcp_listen_address.split(":")[0]
        # # get port
        # port = ckb_tcp_listen_address.split(":")[1]
        # #  new telnet
        # tn = telnetlib.Telnet(host, int(port))
        # print("----")
        # topic_str = (
        #     '{"id": 2, "jsonrpc": "2.0", "method": "subscribe", "params": ["'
        #     + topic
        #     + '"]}'
        # )
        # tn.write(topic_str.encode("utf-8") + b"\n")
        # data = tn.read_until(b"}\n")
        # if data:
        #     output = data.decode("utf-8")
        #     print("telnet read:", output)
        # return tn

    def subscribe_websocket(self, topic, other_url=None) -> WebSocket:
        if other_url is None and "ckb_ws_listen_address" not in self.ckb_config.keys():
            raise Exception("not set ckb_ws_listen_address")
        print("subscribe_websocket")
        if other_url is not None:
            ckb_ws_listen_address = other_url
        else:
            ckb_ws_listen_address = self.ckb_config["ckb_ws_listen_address"]
        print(ckb_ws_listen_address)
        ws = create_connection(f"ws://{ckb_ws_listen_address}")
        topic_str = (
            '{"id": 2, "jsonrpc": "2.0", "method": "subscribe", "params": ["'
            + topic
            + '"]}'
        )
        ws.send(topic_str)
        print("Sent")
        print("Receiving...")
        result = ws.recv()
        print(result)
        # ws.settimeout(1)
        return ws
