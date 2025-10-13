import time

from framework.test_btc import BtcNode
from framework.test_lnd import LndNode
from lndgrpc import LNDClient


class TestDemo:

    def test_01(self):
        btcNode = BtcNode()
        btcNode.prepare()
        btcNode.start()

    def test_02(self):
        """
        start lnd node
        listen=0.0.0.0:9835
        rpclisten=localhost:11009
        restlisten=localhost:8180
        Returns:
        """
        lndNode = LndNode("tmp/lnd/node1", 9735, 11009, 8180)
        lndNode.prepare()
        lndNode.start()

    def test_03(self):
        lndNode = LndNode("tmp/lnd/node2", 9736, 11010, 8181)
        # lndNode.prepare()
        # lndNode.start()
        # info = lndNode.ln_cli_with_cmd("help")
        # print(info)

    def test_04(self):
        # setup-channels() {
        #   echo "=> open channel from ingrid to bob"
        #   local bob_dir="$script_dir/lnd-bob"
        #   local ingrid_dir="$script_dir/lnd-ingrid"
        #   local ingrid_p2tr_address="$(lncli -n regtest --lnddir="$ingrid_dir" --no-macaroons --rpcserver "localhost:$ingrid_port" newaddress p2tr | jq -r .address)"
        linNode = LndNode("tmp/lnd/node2", 9736, 11010, 8181)
        bobNode = LndNode("tmp/lnd/node1", 9735, 11009, 8180)
        btcNode = BtcNode()

        ingrid_p2tr_address = linNode.ln_cli_with_cmd("newaddress p2tr")["address"]

        #   local bob_node_key="$(lncli -n regtest --lnddir="$bob_dir" --no-macaroons --rpcserver "localhost:$bob_port" getinfo | jq -r .identity_pubkey)"
        bob_node_key = bobNode.ln_cli_with_cmd("getinfo")["identity_pubkey"]
        #   echo "ingrid_p2tr_address=$ingrid_p2tr_address"
        print("ingrid_p2tr_address:", ingrid_p2tr_address)
        #   echo "bob_node_key=$bob_node_key"
        print("bob_node_key:", bob_node_key)
        #   echo "deposit btc"
        print("deposit btc")
        #   local bitcoind_dir="$script_dir/bitcoind"
        #   local bitcoind_conf="$bitcoind_dir/bitcoin.conf"
        #   bitcoin-cli -conf="$bitcoind_conf" -rpcwait -named sendtoaddress address="$ingrid_p2tr_address" amount=5 fee_rate=25
        btcNode.sendtoaddress(ingrid_p2tr_address, 5, 25)
        #   bitcoin-cli -conf="$bitcoind_conf" -generate 1 >/dev/null
        btcNode.miner(1)
        for i in range(5):
            linNode.open_channel(bobNode, 1000000, 1, 0)
            time.sleep(1)
        btcNode.miner(3)
        #   echo "openchannel"
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
        #
        #   echo "generate blocks"
        #   bitcoin-cli -conf="$bitcoind_conf" -generate 3 >/dev/null
        # }

    def test_06(self):
        linNode = LndNode("tmp/lnd/node2", 9736, 11010, 8181)
        bobNode = LndNode("tmp/lnd/node1", 9735, 11009, 8180)
        btcNode = BtcNode()

        btcNode.miner(3)

    def test_07(self):
        linNode = LndNode("tmp/lnd/node2", 9736, 11010, 8181)
        bobNode = LndNode("tmp/lnd/node1", 9735, 11009, 8180)
        linNode.ln_cli_with_cmd("listchannels")
        bobNode.ln_cli_with_cmd("listchannels")

    def test_08(self):
        linNode = LndNode("tmp/lnd/node2", 9736, 11010, 8181)
        bobNode = LndNode("tmp/lnd/node1", 9735, 11009, 8180)
        invoice = bobNode.addinvoice(1000)
        print("invoice:", invoice)
        # linNode.ln_cli_with_cmd(f"payinvoice {invoice['payment_request']} --force")

    def test_09(self):
        linNode = LndNode("tmp/lnd/node2", 9736, 11010, 8181)
        bobNode = LndNode("tmp/lnd/node1", 9735, 11009, 8180)

        btcNode = BtcNode()
        # btcNode.miner(10)
        linNode.ln_cli_with_cmd("listchannels")
        bobNode.ln_cli_with_cmd("listchannels")

    def test_000(self):
        import os
        from jinja2 import FileSystemLoader, Environment, TemplateNotFound

        template_path = "/Users/guopenglin/PycharmProjects/ckb-py-integration-test/source/lnd-init/lnd/lnd.conf.j2"
        project_root = os.path.dirname(
            os.path.dirname(os.path.dirname(template_path))
        )  # 调整到根目录

        try:
            env = Environment(loader=FileSystemLoader(project_root))
            template = env.get_template(os.path.relpath(template_path, project_root))
            print("模板加载成功！")
            print(template.render())  # 如果模板为空，会输出空字符串
        except TemplateNotFound as e:
            print(f"错误: {e}")
            print("检查文件和路径。")
