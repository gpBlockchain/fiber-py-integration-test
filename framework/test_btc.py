from framework.util import run_command, get_project_root
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

    def miner(self, number):
        run_command(
            f'{self.cli} -conf="{self.config}" -datadir="{self.tmp_path}" -generate {number} >/dev/null'
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
