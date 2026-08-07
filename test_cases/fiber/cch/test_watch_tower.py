import time

import pytest

from framework.basic_fiber_with_cch import FiberCchTest
from test_cases.fiber.devnet.settle_invoice.test_settle_invoice import sha256_hex


class TestWatchTower(FiberCchTest):
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 5}

    # @pytest.mark.skip("https://github.com/nervosnetwork/fiber/issues/1222")
    def test_shutdown_fiber(self):
        payment_hash = (
            "0xb00ce15c1a83174e2ea4576a4d7cfbcbe79ce1a6c364501c2910403ff635a116"
        )
        preimage = "0xcf7869059f92e4d87df7fa0994e3526e59a4a9faee5f9d7edbeb1da7e6d75952"

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

        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(1 * 100000),
                "currency": "Fibd",
                "description": "already settled invoice",
                "payment_hash": payment_hash,
                "hash_algorithm": "sha256",
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
            }
        )

        # receive
        receive_btc_response = self.fiber1.get_client().receive_btc(
            {"fiber_pay_req": invoice["invoice_address"]}
        )

        self.LNDs[1].ln_cli_with_cmd_without_json(
            f"payinvoice {receive_btc_response['incoming_invoice']['Lightning']} --force &"
        )
        time.sleep(20)
        self.fiber1.get_client().shutdown_channel(
            {
                "channel_id": self.fiber1.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "force": True,
            }
        )
        shutdown_tx = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, shutdown_tx)
        time.sleep(10)
        self.fiber2.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )
        self.node.getClient().generate_epochs("0x1")
        self.wait_cch_order_state(self.fiber1, payment_hash, "Success", timeout=720)
        payment = self.fiber1.get_client().get_payment({"payment_hash": payment_hash})
        assert payment["status"] == "Success"
        self.wait_invoice_state(self.fiber2, payment_hash, "Paid", timeout=120)
        self.fiber1.get_client().list_channels({"include_closed": True})
        self.fiber2.get_client().list_channels({"include_closed": True})
        invoice = self.LNDs[0].ln_cli_with_cmd(
            f"lookupinvoice {payment_hash.replace('0x', '')}"
        )
        assert invoice["state"] == "SETTLED"
        payment = self.LNDs[1].ln_cli_with_cmd(f"listpayments")
        assert payment["payments"][0]["status"] == "SUCCEEDED"
        assert payment["payments"][0]["payment_hash"] == payment_hash.replace("0x", "")

    def test_shutdown_lnd(self):
        # lnd force shutdown

        preimage = self.generate_random_preimage()
        payment_hash = sha256_hex(preimage)
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
        lndInvoice = self.LNDs[1].addholdinvoice(
            payment_hash.replace("0x", ""), "100000"
        )
        send_payment_response = self.fiber1.get_client().send_btc(
            {
                "btc_pay_req": lndInvoice["payment_request"],
                "currency": "Fibd",
            }
        )
        payment = self.fiber2.get_client().send_payment(
            {"invoice": send_payment_response["incoming_invoice"]["Fiber"]}
        )
        # wait tx xxx
        #  todo  wait lnd contains pending tlc
        time.sleep(10)
        # channels = self.LNDs[0].ln_cli_with_cmd("listpayments")
        channels = self.LNDs[0].ln_cli_with_cmd("listchannels")
        for channel in channels["channels"]:
            if len(channel["pending_htlcs"]) == 1:
                print("channel_point:", channel["channel_point"])
                self.LNDs[1].ln_cli_with_cmd(
                    f"closechannel --chan_point {channel['channel_point']} --force"
                )
        time.sleep(5)
        self.btcNode.miner(5)
        time.sleep(5)
        self.LNDs[1].ln_cli_with_cmd("pendingchannels")
        print("preimage:", preimage)
        self.LNDs[1].ln_cli_with_cmd(f"settleinvoice {preimage.replace('0x', '')}")
        time.sleep(1)
        self.LNDs[1].ln_cli_with_cmd("listinvoices")
        self.LNDs[1].ln_cli_with_cmd("pendingchannels")

        self.btcNode.miner(50)
        self.wait_payment_state(self.fiber2, payment["payment_hash"])
        # print("time out ")
        # pass
