import time

from framework.basic_fiber_with_cch import FiberCchTest
from framework.test_fiber import FiberConfigPath

import hashlib


class TestCch(FiberCchTest):
    fiber_version = FiberConfigPath.CURRENT_DEV_DEBUG

    def test_demo(self):
        self.LNDs[0].open_channel(self.LNDs[1], 1000000, 1, 0)
        lndInvoice = self.LNDs[1].addinvoice(100)
        send_payment_response = self.fiber1.get_client().send_btc(
            {
                "btc_pay_req": lndInvoice["payment_request"],
                "currency": "Fibd",
            }
        )

        self.faucet(
            self.fiber2.account_private,
            0,
            self.fiber1.account_private,
            10000 * 100000000,
        )
        self.open_channel(
            self.fiber2,
            self.fiber1,
            1000 * 100000000,
            1000 * 100000000,
            udt=self.get_account_udt_script(self.fiber1.account_private),
        )
        self.fiber2.get_client().add_tlc(
            {
                "channel_id": self.fiber2.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "amount": "0x64",
                # "payment_hash": invoice_list[i]['invoice']['data']['payment_hash'],
                "payment_hash": send_payment_response["payment_hash"],
                "expiry": hex((int(time.time()) + 60 * 60 * 24) * 1000),
                "hash_algorithm": "sha256",
            }
        )
        time.sleep(10)
        fiber1_balance = self.get_fiber_balance(self.fiber1)
        assert (
            fiber1_balance[
                self.get_account_udt_script(self.fiber1.account_private)["args"]
            ]["local_balance"]
            == 100000000100
        )
        lnd1Balance = self.LNDs[1].ln_cli_with_cmd("channelbalance")
        assert lnd1Balance["balance"] == "100"
        invoiceResponse = self.LNDs[1].ln_cli_with_cmd(
            f"lookupinvoice {lndInvoice['r_hash']}"
        )
        assert invoiceResponse["state"] == "SETTLED"

    #
    #
    # def test_Cch(self):
    #     for lnd in self.LNDs:
    #         info = lnd.ln_cli_with_cmd("getinfo")
    #
    # def test_Cch2(self):
    #     self.LNDs[0].open_channel(self.LNDs[1], 1000000, 1, 0)
    #
    # def test_bb(self):
    #     b = self.get_fiber_balance(self.fiber1)
    #     print(b)
    #
    #
    # def test_Cch3(self):
    #     self.LNDs[1].addinvoice(100)
    #     # {
    #     #     "r_hash":  "8c3e331736fea130e52bebe64af6cf5565ff4044ecbff60a2dde4ae2d7f4d544",
    #     #     "payment_request":  "lnbcrt1u1p5w7kmlpp53slrx9ekl6snpefta0ny4ak024jl7szyajllvz3dme9w94l564zqdq8w3jhxaqcqzzsxqyz5vqsp5f4wl8789me8sz95gzvcnu505aerefazzzn896zz6yqpsnuscayuq9qxpqysgq0fe5cl6pxhvcjtqcmg28pnfw7cz7fxql6pay7ttwpkf6esg0zle8u3chny2jmdz8q4y5tkq5n68j2y2amcveuyt80p70k0x34nckfccpxfv5yq",
    #     #     "add_index":  "1",
    #     #     "payment_addr":  "4d5df3f8e5de4f01168813313e51f4ee4794f44214ce5d085a200309f218e938"
    #     # }
    #
    # def test_Cch4(self):
    #     "lnbcrt1u1p5dnatqpp5qkydx3navxq3yk9f8kv0ww4awkfxy7rdm2fg5m0yzlj80lwrav8qdq8w3jhxaqcqzzsxqyz5vqsp528tuegxkuq5mczg6tju9amuqj7tyqllxx9v36l8tdkzktpyyxn8q9qxpqysgqrya8jhs2qsa33g0scqtp8hp288ju422v5dhdghffvlhse827clfksxdvnlk4mjvle0qcdw7jwdp2lz32tlr2enamhz95rpfsj543pmcptfa5a0"
    #     self.fiber1.get_client().send_btc(
    #         {
    #             "btc_pay_req": "lnbcrt1u1p5dnlampp5ytr0nad4mdm6a55f3uu6wyee6t4tr7wn8z52ukh9ygvh0zdyv0fqdq8w3jhxaqcqzzsxqyz5vqsp5v8ekxjlj6egh2sga88uhaxs6y72cegjsdqzy7pnutt828nfqjxvs9qxpqysgqf5wfgzguz5jl2vw7ylsdyjk26m4dcjsrsqg55q6g8lm9lg820q0xa8wgd0mdwdar30ze46gc098xwxjg2zcd38j9utf7rfc2ynhszsgqg5wpju",
    #             "currency": "Fibd",
    #         }
    #     )
    #     # {"jsonrpc": "2.0", "id": 42, "result": {
    #     # "timestamp": "0x68d9ffea", "expiry": "0x15150",
    #     # "ckb_final_tlc_expiry_delta": "0x5265c00",
    #     # "currency": "Fibd", "wrapped_btc_type_script":
    #     # {"code_hash": "0x102583443ba6cfe5a3ac268bbb4475fb63eb497dce077f126ad3b148d4f4f8f8", "hash_type": "type", "args": "0x32e555f3ff8e135cece1351a6a2971518392c1e30375c1e006ad0ce8eac07947"},
    #     # "btc_pay_req": "lnbcrt1u1p5dnlampp5ytr0nad4mdm6a55f3uu6wyee6t4tr7wn8z52ukh9ygvh0zdyv0fqdq8w3jhxaqcqzzsxqyz5vqsp5v8ekxjlj6egh2sga88uhaxs6y72cegjsdqzy7pnutt828nfqjxvs9qxpqysgqf5wfgzguz5jl2vw7ylsdyjk26m4dcjsrsqg55q6g8lm9lg820q0xa8wgd0mdwdar30ze46gc098xwxjg2zcd38j9utf7rfc2ynhszsgqg5wpju",
    #     # "ckb_pay_req": "fibd1001qeqmuswpnckgfw0pnm6vkv5pxhcylu46mnefs0qdawwm4lwp2mrwxex7nnrcp89yjcnep78gz7nq4yel68vk22wgg0e0ryvh7y8un7ug4sxyynkyn79w5ygdduvxwjznqy4evtdt49hexkcwr9gn9pwqy96pdpkfz29csdgfzfwd8r8pnc33vctx87ayvv7ztduga75xvt2levg5j2rynckghcg7gvnvxjm4yfx3gwtr3a0gyxw3v4spn3zqh4verd4f4wcqz7lj6k",
    #     # "payment_hash": "0x22c6f9f5b5db77aed2898f39a71339d2eab1f9d338a8ae5ae522197789a463d2", "amount_sats": "0x64", "fee_sats": "0x0", "status": "pending"}}
    #
    # def test_Cch5(self):
    #     self.faucet(
    #         self.fiber2.account_private,
    #         0,
    #         self.fiber1.account_private,
    #         10000 * 100000000,
    #     )
    #     self.open_channel(
    #         self.fiber2,
    #         self.fiber1,
    #         1000 * 100000000,
    #         1000 * 100000000,
    #         udt=self.get_account_udt_script(self.fiber1.account_private),
    #     )
    #
    # def test_002(self):
    #     # self.fiber2.get_client().send_payment({
    #     # "invoice":"fibd1001qeqmuswpnckgfw0pnm6vkudpd055j9csyjs6ms56gw9v59tvfg9c7p290n6a6xuk0p0mqxaah87nsj5lkn60cxnextjhvgg704g3n9hvytnd3qrymsz37ehquu9w0zlwza930m38zj9vadqkadf2ls6ckht27q2mxpsdyxhfl7u20l9pmc6w53ngxkxfhu9p3258q0wlz3jmktff3ayrsehr0y3pecw6rgevlrcfjlfle6v3msc9nnpkwmmkazmanz4pvz3qjnzpka"
    #     # })
    #     self.fiber1.get_client().parse_invoice(
    #         {
    #             "invoice": "fibd1001qeqmuswpnckgfw0pnm6vkv5pxhcylu46mnefs0qdawwm4lwp2mrwxex7nnrcp89yjcnep78gz7nq4yel68vk22wgg0e0ryvh7y8un7ug4sxyynkyn79w5ygdduvxwjznqy4evtdt49hexkcwr9gn9pwqy96pdpkfz29csdgfzfwd8r8pnc33vctx87ayvv7ztduga75xvt2levg5j2rynckghcg7gvnvxjm4yfx3gwtr3a0gyxw3v4spn3zqh4verd4f4wcqz7lj6k"
    #         }
    #     )
    #     # {"jsonrpc": "2.0", "id": 42, "result": {
    #     # "invoice": {"currency": "Fibd",
    #     # "amount": "0x64", "signature": null,
    #     # "data": {"timestamp": "0x199938fab3b", "payment_hash": "0x22c6f9f5b5db77aed2898f39a71339d2eab1f9d338a8ae5ae522197789a463d2", "attrs": [{"expiry_time": "0x15150"}, {"final_htlc_minimum_expiry_delta": "0x5265c00"}, {"udt_script": "0x55000000100000003000000031000000102583443ba6cfe5a3ac268bbb4475fb63eb497dce077f126ad3b148d4f4f8f8012000000032e555f3ff8e135cece1351a6a2971518392c1e30375c1e006ad0ce8eac07947"}]}}}}
    #
    # def test_003(self):
    #     self.fiber2.get_client().add_tlc(
    #         {
    #             "channel_id": self.fiber2.get_client().list_channels({})["channels"][0][
    #                 "channel_id"
    #             ],
    #             "amount": "0x64",
    #             # "payment_hash": invoice_list[i]['invoice']['data']['payment_hash'],
    #             "payment_hash": "0x22c6f9f5b5db77aed2898f39a71339d2eab1f9d338a8ae5ae522197789a463d2",
    #             "expiry": hex((int(time.time()) + 60 * 60 * 24) * 1000),
    #             "hash_algorithm": "sha256",
    #         }
    #     )
    #
    # def test_004(self):
    #     self.fiber1.get_client().get_receive_btc_order({""})
    #
    # def test_005(self):
    #     self.LNDs[1].ln_cli_with_cmd("help")
    #
    # def test_006(self):
    #     # self.fiber1.get_client().get_receive_btc_order({
    #     #     "payment_hash":"0x22c6f9f5b5db77aed2898f39a71339d2eab1f9d338a8ae5ae522197789a463d2"
    #     # })
    #     self.LNDs[1].ln_cli_with_cmd("channelbalance")
    #     # self.fiber2.get_client().get_receive_btc_order({
    #     #     "payment_hash":"0x22c6f9f5b5db77aed2898f39a71339d2eab1f9d338a8ae5ae522197789a463d2"
    #     # })
    #     # self.LNDs[1].ln_cli_with_cmd("listinvoices")
    #
    # def test_007(self):
    #     preimage = self.generate_random_preimage()
    #     hash_object = hashlib.sha256(bytes.fromhex(preimage.replace("0x", "")))
    #     hash = hash_object.hexdigest()
    #     print("preimage:", preimage)
    #     print("hash:", hash)
    #     self.fiber1.get_client().receive_btc(
    #         {
    #             "payment_hash": f"0x{hash}",
    #             "channel_id": self.fiber1.get_client().list_channels({})["channels"][0][
    #                 "channel_id"
    #             ],
    #             "amount_sats": "0x1",
    #             "final_tlc_expiry": "0x3c",
    #         }
    #     )
    #     # preimage: 0xed47defc4f3842065f161826c3c4296f0d46ed8a94df21d74b061ca0d0bf0bfb
    #     # hash: e4c787f4c43594158572355e4ba4fc75b78e049b9ca406df71b4f20881cdd2c8
    #     # {"jsonrpc": "2.0", "id": 42, "result": {"timestamp": "0x68da3580", "expiry": "0x15180", "ckb_final_tlc_expiry_delta": "0x3c", "wrapped_btc_type_script":
    #     # {"code_hash": "0x102583443ba6cfe5a3ac268bbb4475fb63eb497dce077f126ad3b148d4f4f8f8", "hash_type": "type", "args": "0x32e555f3ff8e135cece1351a6a2971518392c1e30375c1e006ad0ce8eac07947"},
    #     # "btc_pay_req": "lnbcrt10n1p5d5dvqpp5unrc0axyxk2ptptjx40yhf8uwkmcupymnjjqdhm3kneq3qwd6tyqdqqcqzrqxqyz5vqsp5966qferqk73s6hvm5084uczyjjvjjp9ec9fnzj8az7h7vqa8509q9qxpqysgq0q8yy8nw6gl32zgt28c8tvae6vra5ufssx5xza357m97thc96upzy0g2ff906734gvvekeyctv0laey023npqhx2m945ga0y9hknqucqh9dxww", "payment_hash": "0xe4c787f4c43594158572355e4ba4fc75b78e049b9ca406df71b4f20881cdd2c8", "channel_id": "0x3fb57f2ce1546e2b888679f877296ae9021b153ea81c15922f17528b8289a76a", "tlc_id": null, "amount_sats": "0x1", "fee_sats": "0x0", "status": "pending"}}
    #
    # def test_008(self):
    #     invoice = self.LNDs[0].addinvoice(100)
    #     self.LNDs[1].payinvoice(invoice["payment_request"])
    #
    # def test_010(self):
    #     self.LNDs[1].ln_cli_with_cmd("channelbalance")
    #
    # def test_011(self):
    #     self.fiber1.get_client().get_receive_btc_order(
    #         {
    #             "payment_hash": "0xe4c787f4c43594158572355e4ba4fc75b78e049b9ca406df71b4f20881cdd2c8",
    #         }
    #     )
    #     # {"jsonrpc": "2.0", "id": 42, "result": {"timestamp": "0x68da3580", "expiry": "0x15180", "ckb_final_tlc_expiry_delta": "0x3c", "wrapped_btc_type_script": {"code_hash": "0x102583443ba6cfe5a3ac268bbb4475fb63eb497dce077f126ad3b148d4f4f8f8", "hash_type": "type", "args": "0x32e555f3ff8e135cece1351a6a2971518392c1e30375c1e006ad0ce8eac07947"}, "btc_pay_req": "lnbcrt10n1p5d5dvqpp5unrc0axyxk2ptptjx40yhf8uwkmcupymnjjqdhm3kneq3qwd6tyqdqqcqzrqxqyz5vqsp5966qferqk73s6hvm5084uczyjjvjjp9ec9fnzj8az7h7vqa8509q9qxpqysgq0q8yy8nw6gl32zgt28c8tvae6vra5ufssx5xza357m97thc96upzy0g2ff906734gvvekeyctv0laey023npqhx2m945ga0y9hknqucqh9dxww", "payment_hash": "0xe4c787f4c43594158572355e4ba4fc75b78e049b9ca406df71b4f20881cdd2c8", "channel_id": "0x3fb57f2ce1546e2b888679f877296ae9021b153ea81c15922f17528b8289a76a", "tlc_id": "0x3", "amount_sats": "0x1", "fee_sats": "0x0", "status": "accepted"}}
    #
    # def test_012(self):
    #     self.fiber2.get_client().remove_tlc(
    #         {
    #             "channel_id": self.fiber1.get_client().list_channels({})["channels"][0][
    #                 "channel_id"
    #             ],
    #             "tlc_id": "0x3",
    #             "reason": {
    #                 "payment_preimage": "0xed47defc4f3842065f161826c3c4296f0d46ed8a94df21d74b061ca0d0bf0bfb"
    #             },
    #         }
    #     )
    #
    # def test_013(self):
    #     preimage = "5e6065d8bca9c3deb0ca05d8b05b83db6b858764b30f7fe0ff5a936d589a5be0"
    #     hash_object = hashlib.sha256(bytes.fromhex(preimage))
    #     hash = hash_object.hexdigest()
    #     print("preimage:", preimage)
    #     print("hash:", hash)
    #
    # def test_002220(self):
    #     self.fiber2.get_client().new_invoice(
    #         {
    #             "amount": hex(12),
    #             "currency": "Fibd",
    #             "description": "test invoice generated by node2",
    #             "expiry": "0xe10",
    #             "final_cltv": "0x28",
    #             "payment_preimage": "0x5e6065d8bca9c3deb0ca05d8b05b83db6b858764b30f7fe0ff5a936d589a5be0",
    #             "hash_algorithm": "sha256",
    #         }
    #     )
    #     # {"jsonrpc": "2.0", "id": 42, "result": {"invoice_address": "fibd11pcsaug0p0exgfw0pnm6vhpmrqg7ryuxga5fes0p6efqjg7mtnd3y88fe8htszpt4fg6znncmmcmxqc9zcr5flnd6h20le6qpxtcu96wsdnmxppmerdxw07r9w84xs20ymw4496ey8sz9km8wk2ujk9t7v06vps7qvpzhmqwtj7ttrvd8vg2aafz8nmxgfvzktfmz9csqt5m3k67l28zda6mcrgu8k22te74hhguf8yckex40rmrmwpqa9ktg2qdp9d855s56tgupumetkw0df6lr9sgc60h0h6j972adtvy78xpazyxqvq3cejcw8pz6l4fdv4uen86s8385uks7vzh588k7ppn8t6pnqqxkjayl", "invoice": {"currency": "Fibd", "amount": "0x1", "signature": "0d01050d07141410141a0b081c011c1b190b160e0f0d091a1f03051008181a0f170f171a12051e0a1d0d0b0c041e0706011d020406000c0011181912180e0701021a1f15090d0c151c1913071a10071107141c16101e0c0217140707161e010113070b1a01130000", "data": {"timestamp": "0x199945855e4",
    #     # "payment_hash": "0xdd8040ae403029da4ffada679bcaac734512f417c454d3ea69c25d5540db7b27", "attrs": [{"description": "test invoice generated by node2"}, {"expiry_time": "0xe10"}, {"hash_algorithm": "sha256"}, {"payee_public_key": "02c3fcc10e26cddd63758da05a2dcf967e9e5b18687df6f9e854003c0efd709afb"}]}}}}
    #
    #     # {"jsonrpc": "2.0", "id": 42, "result": {"invoice_address": "fibd121pcsaug0p0exgfw0pnm6vkhuy8w4zcpf8me6jq09ta9dnf4za74l8rmy4afp70qgmfa0n79nsuzxkguhv2uw0xuth53wmsvwcvfv08t7mnerrjrtu8hsmt4csvym0pzqqwsyj6wjmwj9580mc7mwlfxuxkjp5qulw8xtneacaplljhpay9u63a69nd8mnfw5xxvrm6svp85p3rsch49pk6cn53gsvaclqw07vcqptlwjvdcu79p7gz2wuh73qq8my28x55v7kd5jsm0e4vmg90d97pwm53wvgjrcxtfxxhfxg06fzq06qy2rfsyvn6n0f8fvv44p2kgjvulc28g4fkqc87jra4jcp78hcpuxrduy", "invoice": {"currency": "Fibd", "amount": "0xc", "signature": "071b040a070614140c1e160d1412101b0f19150c1b08050f0d051e010e1b14110e0c08120318060b090606170906080f1a0902000f1a00040a030910040c131a130f0907090c0c1515010a1608120c1c1f180a07081509160018071e12031d151218011e07171801", "data": {"timestamp": "0x199945b2795",
    #     # "payment_hash": "0x9d97bd1f40012d1c4f3ed75d484a6cd829780800b9702afb999c2de856725511", "attrs": [{"description": "test invoice generated by node2"}, {"expiry_time": "0xe10"}, {"hash_algorithm": "sha256"}, {"payee_public_key": "02c3fcc10e26cddd63758da05a2dcf967e9e5b18687df6f9e854003c0efd709afb"}]}}}}
