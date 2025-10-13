import json

from framework.util import get_project_root
import os
from framework.util import create_config_file, get_project_root, run_command
import time


class LndNode:
    """

    setup-lnd() {
      local lnd_name="$1"
      local lnd_port="$2"
      local lnd_dir="$script_dir/$lnd_name"
      echo "=> setting up lnd $lnd_name"
      nohup lnd --lnddir="$lnd_dir" &>/dev/null &
      echo "$!" > "$lnd_dir/lnd.pid"
      local retries=30
      echo "waiting for ready"
      while [[ $retries -gt 0 ]] && ! lncli -n regtest --lnddir="$lnd_dir" --no-macaroons --rpcserver "localhost:$lnd_port" getinfo &>/dev/null; do
        sleep 1
        retries=$((retries - 1))
      done
      echo "remaining retries=$retries"
    }
    """

    def __init__(self, tmp_path, listen_port, rpc_port, rest_port):
        self.tmp_path = f"{get_project_root()}/{tmp_path}"
        self.listen_port = listen_port
        self.rpc_port = rpc_port
        self.rest_port = rest_port
        self.lnd = (
            f"{get_project_root()}/download/lnd/lnd-darwin-arm64-v0.19.3-beta/lnd"
        )
        self.lnd_cli = (
            f"{get_project_root()}/download/lnd/lnd-darwin-arm64-v0.19.3-beta/lncli"
        )
        """
            listen=0.0.0.0:{{ lnd_listen_port }}
            rpclisten=localhost:{{ lnd_rpc_listen_port }}
            restlisten=localhost:{{ lnd_rest_listen_port}}
        """
        self.config = {
            "lnd_listen_port": self.listen_port,
            "lnd_rpc_listen_port": self.rpc_port,
            "lnd_rest_listen_port": self.rest_port,
        }
        self.lnd_config_path = "source/lnd-init/lnd/lnd.conf.j2"

    def prepare(self, other_config={}):
        os.makedirs(self.tmp_path, exist_ok=True)  # 创建文件夹，如果已存在则不报错
        if ".j2" in self.lnd_config_path:
            create_config_file(
                self.config,
                self.lnd_config_path,
                f"{self.tmp_path}/lnd.conf",
            )

    def start(self):
        run_command(
            f'{self.lnd} --lnddir="{self.tmp_path}" &>/dev/null & echo $! > {self.tmp_path}/lnd.pid'
        )
        retries = 30
        print("waiting for ready")
        for i in range(retries):
            try:
                self.ln_cli_with_cmd("getinfo")
                print(f"remaining retries={retries}")
                return
            except Exception as e:
                time.sleep(1)
                continue

    def getinfo(self):
        return self.ln_cli_with_cmd("getinfo")

    def addinvoice(self, amt=1000, memo="test"):
        return self.ln_cli_with_cmd(f"addinvoice --amt {amt} --memo {memo}")

    def payinvoice(self, payment_request):
        self.ln_cli_with_cmd_without_json(f"payinvoice {payment_request} --force")

    def ln_cli_with_cmd_without_json(self, cmd):
        return run_command(
            f'{self.lnd_cli} -n regtest --lnddir="{self.tmp_path}" --no-macaroons --rpcserver "localhost:{self.rpc_port}" {cmd}'
        )

    def ln_cli_with_cmd(self, cmd):
        return json.loads(
            run_command(
                f'{self.lnd_cli} -n regtest --lnddir="{self.tmp_path}" --no-macaroons --rpcserver "localhost:{self.rpc_port}" {cmd}'
            )
        )

    def connected(self, btcNode):
        pass

    def stop(self):
        run_command(f"kill `cat {self.tmp_path}/lnd.pid`")

    def clean(self):
        shutil.rmtree(self.tmp_path)

    def open_channel(self, peerNode, local_amt, sat_per_vbyte, min_confs):
        node_key = peerNode.getinfo()["identity_pubkey"]
        peer_address = f"localhost:{peerNode.listen_port}"
        self.ln_cli_with_cmd(
            f"openchannel --node_key {node_key} --connect {peer_address} --local_amt {local_amt} --sat_per_vbyte {sat_per_vbyte} --min_confs {min_confs}"
        )
        ##   echo "openchannel"
        #   local retries=5
        #   while [[ $retries -gt 0 ]] && ! lncli -n regtest --lnddir="$ingrid_dir" --no-macaroons --rpcserver "localhost:$ingrid_port" \
        #       openchannel \
        #       --node_key "$bob_node_key" \
        #       --connect localhost:9835 \
        #       --local_amt 1000000 \
        #       --sat_per_vbyte 1 \
        #       --min_confs 0; do
        #     sleep 3
        #     retries=$((retries - 1))
        #   done
