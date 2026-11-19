from framework.util import run_command, get_project_root
import json
import shutil
import os


class BtcNode:

    # bitcoind -conf="$bitcoind_conf" -datadir="$bitcoind_dir" -daemonwait -pid="$bitcoind_pid"
    # bitcoin-cli -conf="$bitcoind_conf" -datadir="$bitcoind_dir" -rpcwait createwallet dev >/dev/null
    # echo "bitcoind wallet created"
    # bitcoin-cli -conf="$bitcoind_conf" -generate 101 >/dev/null
    # echo "bitcoind blocks generated"

    def __init__(self):
        self.tmp_path = f"{get_project_root()}/tmp/btc"
        self.bin = f"{get_project_root()}/download/btc/current/bitcoin/bin/bitcoind"
        self.cli = f"{get_project_root()}/download/btc/current/bitcoin/bin/bitcoin-cli"
        self.config = f"{self.tmp_path}/bitcoin.conf"

    def prepare(self, other_config={}):
        os.makedirs(self.tmp_path, exist_ok=True)  # 创建文件夹，如果已存在则不报错
        shutil.copy(
            "{root_path}/source/lnd-init/bitcoind/bitcoin.conf".format(
                root_path=get_project_root()
            ),
            self.tmp_path,
        )

    def start(self):
        run_command(
            f'{self.bin} -conf="{self.config}" -datadir="{self.tmp_path}" -daemonwait -pid="{self.tmp_path}/bitcoind.pid"'
        )
        #   bitcoin-cli -conf="$bitcoind_conf" -datadir="$bitcoind_dir" -rpcwait createwallet dev >/dev/null
        run_command(
            f'{self.cli} -conf="{self.config}" -datadir="{self.tmp_path}" -rpcwait createwallet dev >/dev/null'
        )
        print("bitcoind wallet created")
        #   bitcoin-cli -conf="$bitcoind_conf" -generate 101 >/dev/null
        self.miner(101)
        clock = getattr(self, "cluster_clock", None)
        if clock is not None and clock.offset_seconds > 0:
            # setmocktime does not survive bitcoind restart.
            clock.propagate()

    def miner(self, number):
        run_command(
            f'{self.cli} -conf="{self.config}" -datadir="{self.tmp_path}" -generate {number} >/dev/null'
        )

    def rpc(self, method):
        return run_command(
            f'{self.cli} -conf="{self.config}" -datadir="{self.tmp_path}" {method}'
        )

    def connected(self, btcNode):
        pass

    def stop(self):
        run_command(f"kill `cat {self.tmp_path}/bitcoind.pid`")

    def clean(self):
        shutil.rmtree(self.tmp_path)

    def sendtoaddress(self, address, amount, fee_rate):
        return run_command(
            f'{self.cli} -conf="{self.config}" -datadir="{self.tmp_path}" -named sendtoaddress address="{address}" amount={amount} fee_rate={fee_rate}'
        )

    def _cli(self, *args, input_text=None):
        import subprocess

        cmd = [
            self.cli,
            f"-conf={self.config}",
            f"-datadir={self.tmp_path}",
            *[str(a) for a in args],
        ]
        result = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            input=input_text,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"{' '.join(cmd)} failed ({result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        return result.stdout

    def set_mocktime(self, unix_seconds):
        """Freeze bitcoind's wall clock at unix_seconds (regtest setmocktime)."""
        unix = int(unix_seconds)
        if unix <= 0:
            raise ValueError("setmocktime requires a positive unix timestamp")
        return self._cli("setmocktime", unix)

    def get_mocktime_seconds(self):
        """Read the mock clock via getblocktemplate.curtime when possible."""
        raw = self._cli("getblocktemplate", '{"rules":["segwit"]}')
        payload = json.loads(raw)
        if "curtime" in payload:
            return int(payload["curtime"])
        raise RuntimeError(f"getblocktemplate missing curtime: {payload}")
