import time
from concurrent.futures import ThreadPoolExecutor

import requests

from framework.basic_fiber_with_cch import FiberCchTest
from test_cases.fiber.devnet.settle_invoice.test_settle_invoice import sha256_hex


class TestPr1512OnchainRecovery(FiberCchTest):
    """PR #1512 regressions for durable CCH recovery and on-chain TLC relay."""

    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 5}

    def _rpc_call(self, fiber, method, params, timeout=5):
        """Use a bounded request so a wedged NetworkActor fails the test promptly."""
        try:
            response = requests.post(
                fiber.get_client().url,
                json={
                    "id": 42,
                    "jsonrpc": "2.0",
                    "method": method,
                    "params": params,
                },
                timeout=timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise AssertionError(
                f"Fiber RPC {method} became unresponsive: {exc}"
            ) from exc

        payload = response.json()
        if "error" in payload:
            raise AssertionError(f"Fiber RPC {method} failed: {payload['error']}")
        return payload["result"]

    def _start_receive_btc_hold_order(self):
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
        channel = self.fiber1.get_client().list_channels(
            {"pubkey": self.fiber2.get_pubkey()}
        )["channels"][0]

        preimage = self.generate_random_preimage()
        payment_hash = sha256_hex(preimage)
        outgoing_invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(100000),
                "currency": "Fibd",
                "description": "PR #1512 CCH recovery hold invoice",
                "payment_hash": payment_hash,
                "hash_algorithm": "sha256",
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
            }
        )
        order = self.fiber1.get_client().receive_btc(
            {"fiber_pay_req": outgoing_invoice["invoice_address"]}
        )
        assert order["payment_hash"] == payment_hash

        self.LNDs[1].ln_cli_with_cmd_without_json(
            f"payinvoice {order['incoming_invoice']['Lightning']} --force &"
        )
        self.wait_invoice_state(self.fiber2, payment_hash, "Received", timeout=120)
        self.wait_cch_order_state(
            self.fiber1, payment_hash, "OutgoingInFlight", timeout=120
        )
        return channel["channel_id"], payment_hash, preimage

    def _restart_fiber1(self, cch_enabled):
        self.fiber1.stop()
        if cch_enabled:
            update_config = {
                "cch": True,
                "cch_lnd_cert_path": f"{self.LNDs[0].tmp_path}/tls.cert",
                "cch_lnd_rpc_url": f"https://localhost:{self.LNDs[0].rpc_port}",
            }
        else:
            # Keep Fiber and its durable store running while CCH is absent. This
            # deterministically drops the live payment-session notification.
            self.fiber1.fiber_config.pop("cch", None)
            update_config = {}
        self.fiber1.prepare(update_config=update_config)
        self.fiber1.start()

    def test_cch_recovers_payment_committed_while_cch_was_stopped(self):
        """A durable Fiber success must reconcile after its live event was missed."""
        channel_id, payment_hash, preimage = self._start_receive_btc_hold_order()

        self._restart_fiber1(cch_enabled=False)
        self.fiber1.connect_peer(self.fiber2)
        self.wait_for_channel_state(
            self.fiber1.get_client(),
            self.fiber2.get_pubkey(),
            "ChannelReady",
            channel_id=channel_id,
        )
        self.fiber2.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )

        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            self._rpc_call(self.fiber1, "node_info", [{}])
            payment = self._rpc_call(
                self.fiber1,
                "get_payment",
                [{"payment_hash": payment_hash}],
            )
            if payment["status"] == "Success":
                break
            assert payment["status"] != "Failed", payment
            time.sleep(1)
        else:
            raise TimeoutError(f"payment did not succeed: {payment}")
        assert payment["payment_preimage"] == preimage

        self._restart_fiber1(cch_enabled=True)
        self.wait_cch_order_state(self.fiber1, payment_hash, "Success", timeout=180)
        invoice = self.LNDs[0].ln_cli_with_cmd(
            f"lookupinvoice {payment_hash.removeprefix('0x')}"
        )
        assert invoice["state"] == "SETTLED", invoice

    def test_onchain_remove_tlc_keeps_network_rpc_responsive(self):
        """On-chain fulfill relay must not block NetworkActor RPC handling."""
        channel_id, payment_hash, preimage = self._start_receive_btc_hold_order()
        self.fiber1.get_client().shutdown_channel(
            {"channel_id": channel_id, "force": True}
        )
        shutdown_tx = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, shutdown_tx)
        time.sleep(10)

        self.fiber2.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            advance_chain = executor.submit(
                self.node.getClient().generate_epochs, "0x1", 0
            )
            while not advance_chain.done():
                self._rpc_call(self.fiber1, "node_info", [{}])
                time.sleep(0.2)
            advance_chain.result(timeout=120)

        deadline = time.monotonic() + 720
        while time.monotonic() < deadline:
            payment = self._rpc_call(
                self.fiber1,
                "get_payment",
                [{"payment_hash": payment_hash}],
            )
            if payment["status"] == "Success":
                break
            assert payment["status"] != "Failed", payment
            time.sleep(1)
        else:
            raise TimeoutError(f"payment did not succeed: {payment}")
        assert payment["payment_preimage"] == preimage

        self.wait_cch_order_state(self.fiber1, payment_hash, "Success", timeout=720)
        invoice = self.LNDs[0].ln_cli_with_cmd(
            f"lookupinvoice {payment_hash.removeprefix('0x')}"
        )
        assert invoice["state"] == "SETTLED", invoice
