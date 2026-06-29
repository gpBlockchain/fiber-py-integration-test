import time

from framework.basic_fiber_with_cch import FiberCchTest


def _wait_cch_order_status(fiber, payment_hash, expected, timeout=180):
    last = None
    for _ in range(timeout):
        last = fiber.get_client().get_cch_order({"payment_hash": payment_hash})
        if last["status"] == expected:
            return last
        if last["status"] in ("Success", "Failed"):
            raise AssertionError(
                f"expected CCH order {expected}, got {last['status']}: {last}"
            )
        time.sleep(1)
    raise TimeoutError(
        f"CCH order {payment_hash} did not reach {expected}, last={last}"
    )


def _wait_fiber_payment_status(fiber, payment_hash, expected, timeout=180):
    last = None
    for _ in range(timeout):
        last = fiber.get_client().get_payment({"payment_hash": payment_hash})
        if last["status"] == expected:
            return last
        if last["status"] in ("Success", "Failed"):
            raise AssertionError(
                f"expected Fiber payment {expected}, got {last['status']}: {last}"
            )
        time.sleep(1)
    raise TimeoutError(
        f"Fiber payment {payment_hash} did not reach {expected}, last={last}"
    )


def _wait_lnd_invoice_state(lnd, payment_hash, expected_states, timeout=120):
    expected = (
        expected_states if isinstance(expected_states, tuple) else (expected_states,)
    )
    rhash = payment_hash[2:] if payment_hash.startswith("0x") else payment_hash
    last = None
    for _ in range(timeout):
        last = lnd.ln_cli_with_cmd(f"lookupinvoice {rhash}")
        if last["state"] in expected:
            return last
        time.sleep(1)
    raise TimeoutError(
        f"LND invoice {payment_hash} did not reach {expected}, last={last}"
    )


class TestCchOutgoingFeeBudget(FiberCchTest):
    amount_sats = 1_000
    fiber_channel_balance = 1_000 * 100000000
    lnd_route_base_fee_msat = 200_000
    fiber_route_fee_rate = 200_000
    # todo 目前回滚没做
    verify_rollback = False

    def _restart_cch(self, base_fee_sats, max_outgoing_fee_percentage):
        self.fiber1.stop()
        self.fiber1.prepare(
            {
                "cch": True,
                "cch_lnd_cert_path": f"{self.LNDs[0].tmp_path}/tls.cert",
                "cch_lnd_rpc_url": f"https://localhost:{self.LNDs[0].rpc_port}",
                "cch_base_fee_sats": base_fee_sats,
                "cch_fee_rate_per_million_sats": 0,
                "cch_max_outgoing_fee_percentage": max_outgoing_fee_percentage,
            }
        )
        with open(self.fiber1.fiber_config_path, "r") as f:
            config_text = f.read()
        assert (
            f"max_outgoing_fee_percentage: {max_outgoing_fee_percentage}" in config_text
        ), config_text
        self.fiber1.start()

    def _open_udt_channel_fiber2_to_cch(self):
        self.faucet(
            self.fiber2.account_private,
            0,
            self.fiber1.account_private,
            10_000 * 100000000,
        )
        self.open_channel(
            self.fiber2,
            self.fiber1,
            1_000 * 100000000,
            1_000 * 100000000,
            udt=self.get_account_udt_script(self.fiber1.account_private),
        )

    def _open_routed_lnd_receiver(self):
        receiver = self.start_new_lnd()
        self.LNDs[1].open_channel(receiver, 1_000_000, 1, 0)
        self.btcNode.miner(6)
        time.sleep(3)

        receiver_pubkey = receiver.getinfo()["identity_pubkey"]
        channels = self.LNDs[1].ln_cli_with_cmd("listchannels")["channels"]
        channel = next(c for c in channels if c["remote_pubkey"] == receiver_pubkey)
        self.LNDs[1].ln_cli_with_cmd(
            "updatechanpolicy "
            f"--base_fee_msat {self.lnd_route_base_fee_msat} "
            "--fee_rate 0 "
            "--time_lock_delta 40 "
            f"--chan_point {channel['channel_point']}"
        )
        route_fee = self._wait_lnd_route_fee(
            receiver_pubkey,
            self.amount_sats,
            self.lnd_route_base_fee_msat // 1000,
        )
        return receiver, route_fee

    def _wait_lnd_route_fee(self, receiver_pubkey, amount_sats, min_fee_sats):
        last = None
        for _ in range(90):
            try:
                last = self.LNDs[0].ln_cli_with_cmd(
                    f"queryroutes {receiver_pubkey} {amount_sats}"
                )
                fees = [int(route["total_fees"]) for route in last.get("routes", [])]
                if fees and min(fees) >= min_fee_sats:
                    return min(fees)
            except Exception:
                pass
            time.sleep(1)
        raise TimeoutError(
            f"LND route fee did not reach {min_fee_sats} sats, last={last}"
        )

    def _open_routed_fiber_receiver(self):
        udt = self.get_account_udt_script(self.fiber1.account_private)
        fiber3 = self.start_new_fiber(
            self.generate_account(
                10_000,
                self.fiber1.account_private,
                10_000 * 100000000,
            ),
            fiber_version=self.fiber_version,
        )
        self.open_channel(
            fiber3,
            self.fiber1,
            self.fiber_channel_balance,
            self.fiber_channel_balance,
            udt=udt,
        )
        self.open_channel(
            fiber3,
            self.fiber2,
            self.fiber_channel_balance,
            0,
            fiber1_fee=self.fiber_route_fee_rate,
            udt=udt,
        )

        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(self.amount_sats),
                "currency": "Fibd",
                "description": "cch fee budget dry run",
                "udt_type_script": udt,
                "payment_preimage": self.generate_random_preimage(),
                "hash_algorithm": "sha256",
            }
        )
        dry_run = self.fiber1.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "dry_run": True,
                "max_fee_amount": hex(10_000_000),
                "max_fee_rate": hex(10_000_000),
            }
        )
        route_fee = int(dry_run["fee"], 16)
        assert route_fee >= self.amount_sats * self.fiber_route_fee_rate // 1_000_000
        return udt, route_fee

    def _new_fiber_invoice(self, udt, amount_sats=None):
        return self.fiber2.get_client().new_invoice(
            {
                "amount": hex(amount_sats or self.amount_sats),
                "currency": "Fibd",
                "description": "cch fee budget regression",
                "udt_type_script": udt,
                "payment_preimage": self.generate_random_preimage(),
                "hash_algorithm": "sha256",
            }
        )

    def _assert_fiber_invoice_amount(self, fiber, invoice, expected_sats):
        parsed = fiber.get_client().parse_invoice({"invoice": invoice})
        assert int(parsed["invoice"]["amount"], 16) == expected_sats

    def _assert_lnd_invoice_amount(self, lnd, invoice, expected_sats):
        parsed = lnd.ln_cli_with_cmd(f"decodepayreq {invoice}")
        assert int(parsed["num_satoshis"]) == expected_sats

    def _assert_incoming_invoice_amount(self, order, expected_sats):
        incoming_invoice = order["incoming_invoice"]
        if "Fiber" in incoming_invoice:
            self._assert_fiber_invoice_amount(
                self.fiber1, incoming_invoice["Fiber"], expected_sats
            )
            return
        if "Lightning" in incoming_invoice:
            self._assert_lnd_invoice_amount(
                self.LNDs[0], incoming_invoice["Lightning"], expected_sats
            )
            return
        raise AssertionError(f"unknown incoming_invoice: {incoming_invoice}")

    def _assert_outgoing_pay_req_amount(self, order, expected_sats):
        outgoing_pay_req = order["outgoing_pay_req"]
        if outgoing_pay_req.startswith("fib"):
            self._assert_fiber_invoice_amount(
                self.fiber1, outgoing_pay_req, expected_sats
            )
            return
        self._assert_lnd_invoice_amount(self.LNDs[0], outgoing_pay_req, expected_sats)

    def _verify_send_btc_failure_rollback(self, receiver, payment_hash):
        _wait_fiber_payment_status(self.fiber2, payment_hash, "Failed")
        incoming_invoice = self.fiber1.get_client().get_invoice(
            {"payment_hash": payment_hash}
        )
        assert incoming_invoice["status"] != "Paid"
        _wait_lnd_invoice_state(receiver, payment_hash, "OPEN")

    def _verify_receive_btc_failure_rollback(self, payment_hash):
        _wait_lnd_invoice_state(self.LNDs[0], payment_hash, "CANCELED")

    def test_send_btc_uses_configured_fee_budget_and_succeeds(self):
        self._restart_cch(base_fee_sats=500, max_outgoing_fee_percentage=100)
        self._open_udt_channel_fiber2_to_cch()

        outgoing_fee_sats = self._wait_lnd_route_fee(
            self.LNDs[1].getinfo()["identity_pubkey"], self.amount_sats, 0
        )
        assert outgoing_fee_sats == 0

        lnd_invoice = self.LNDs[1].addinvoice(self.amount_sats)
        order = self.fiber1.get_client().send_btc(
            {
                "btc_pay_req": lnd_invoice["payment_request"],
                "currency": "Fibd",
            }
        )
        fee_sats = int(order["fee_sats"], 16)
        incoming_amount_sats = self.amount_sats + fee_sats
        self._assert_incoming_invoice_amount(order, incoming_amount_sats)
        self._assert_outgoing_pay_req_amount(order, self.amount_sats)
        assert int(order["amount_sats"], 16) == incoming_amount_sats
        payment = self.fiber2.get_client().send_payment(
            {"invoice": order["incoming_invoice"]["Fiber"]}
        )

        self.wait_payment_state(self.fiber2, payment["payment_hash"], "Success")
        order = self.fiber1.get_client().get_cch_order(
            {"payment_hash": payment["payment_hash"]}
        )
        assert order["status"] == "Success"
        assert int(order["fee_sats"], 16) == 500
        assert int(order["amount_sats"], 16) == self.amount_sats + 500
        self._assert_incoming_invoice_amount(order, self.amount_sats + 500)
        self._assert_outgoing_pay_req_amount(order, self.amount_sats)
        assert outgoing_fee_sats <= int(order["fee_sats"], 16)
        _wait_lnd_invoice_state(self.LNDs[1], payment["payment_hash"], "SETTLED")

    def test_send_btc_fee_budget_failure_rejects_over_budget_outgoing(self):
        receiver, outgoing_fee_sats = self._open_routed_lnd_receiver()
        self._restart_cch(base_fee_sats=200, max_outgoing_fee_percentage=50)
        self._open_udt_channel_fiber2_to_cch()
        assert outgoing_fee_sats == self.lnd_route_base_fee_msat // 1000
        assert outgoing_fee_sats > 200 * 50 // 100

        lnd_invoice = receiver.addinvoice(self.amount_sats)
        order = self.fiber1.get_client().send_btc(
            {
                "btc_pay_req": lnd_invoice["payment_request"],
                "currency": "Fibd",
            }
        )
        fee_sats = int(order["fee_sats"], 16)
        incoming_amount_sats = self.amount_sats + fee_sats
        self._assert_incoming_invoice_amount(order, incoming_amount_sats)
        self._assert_outgoing_pay_req_amount(order, self.amount_sats)
        assert int(order["amount_sats"], 16) == incoming_amount_sats
        payment = self.fiber2.get_client().send_payment(
            {"invoice": order["incoming_invoice"]["Fiber"]}
        )

        order = _wait_cch_order_status(self.fiber1, payment["payment_hash"], "Failed")
        assert int(order["fee_sats"], 16) == 200
        assert int(order["amount_sats"], 16) == self.amount_sats + 200
        self._assert_incoming_invoice_amount(order, self.amount_sats + 200)
        self._assert_outgoing_pay_req_amount(order, self.amount_sats)
        if self.verify_rollback:
            self._verify_send_btc_failure_rollback(receiver, payment["payment_hash"])

    def test_receive_btc_uses_full_fee_budget_and_succeeds(self):
        self._restart_cch(base_fee_sats=500, max_outgoing_fee_percentage=100)
        udt, route_fee = self._open_routed_fiber_receiver()
        assert route_fee == self.amount_sats * self.fiber_route_fee_rate // 1_000_000
        assert route_fee <= 500

        invoice = self._new_fiber_invoice(udt)
        self._assert_fiber_invoice_amount(
            self.fiber2, invoice["invoice_address"], self.amount_sats
        )
        order = self.fiber1.get_client().receive_btc(
            {"fiber_pay_req": invoice["invoice_address"]}
        )
        fee_sats = int(order["fee_sats"], 16)
        incoming_amount_sats = self.amount_sats + fee_sats
        self._assert_incoming_invoice_amount(order, incoming_amount_sats)
        self._assert_outgoing_pay_req_amount(order, self.amount_sats)
        assert int(order["amount_sats"], 16) == self.amount_sats
        self.LNDs[1].payinvoice(order["incoming_invoice"]["Lightning"])

        order = _wait_cch_order_status(self.fiber1, order["payment_hash"], "Success")
        outgoing_invoice = self.fiber2.get_client().get_invoice(
            {"payment_hash": order["payment_hash"]}
        )
        assert int(order["fee_sats"], 16) == 500
        assert int(order["amount_sats"], 16) == self.amount_sats
        self._assert_incoming_invoice_amount(order, self.amount_sats + 500)
        self._assert_outgoing_pay_req_amount(order, self.amount_sats)
        assert route_fee <= int(order["fee_sats"], 16)
        assert outgoing_invoice["status"] == "Paid"
        _wait_lnd_invoice_state(self.LNDs[0], order["payment_hash"], "SETTLED")

    def test_receive_btc_fee_budget_failure_rejects_over_budget_outgoing(self):
        self._restart_cch(base_fee_sats=200, max_outgoing_fee_percentage=50)
        udt, route_fee = self._open_routed_fiber_receiver()
        assert route_fee == self.amount_sats * self.fiber_route_fee_rate // 1_000_000
        assert route_fee > 100

        invoice = self._new_fiber_invoice(udt)
        self._assert_fiber_invoice_amount(
            self.fiber2, invoice["invoice_address"], self.amount_sats
        )
        order = self.fiber1.get_client().receive_btc(
            {"fiber_pay_req": invoice["invoice_address"]}
        )
        fee_sats = int(order["fee_sats"], 16)
        incoming_amount_sats = self.amount_sats + fee_sats
        self._assert_incoming_invoice_amount(order, incoming_amount_sats)
        self._assert_outgoing_pay_req_amount(order, self.amount_sats)
        assert int(order["amount_sats"], 16) == self.amount_sats
        self.LNDs[1].ln_cli_with_cmd_without_json(
            f"payinvoice {order['incoming_invoice']['Lightning']} "
            "--force --timeout 30s &"
        )

        order = _wait_cch_order_status(self.fiber1, order["payment_hash"], "Failed")
        outgoing_invoice = self.fiber2.get_client().get_invoice(
            {"payment_hash": order["payment_hash"]}
        )
        assert int(order["fee_sats"], 16) == 200
        assert int(order["amount_sats"], 16) == self.amount_sats
        self._assert_incoming_invoice_amount(order, self.amount_sats + 200)
        self._assert_outgoing_pay_req_amount(order, self.amount_sats)
        assert route_fee > int(order["fee_sats"], 16) * 50 // 100
        assert outgoing_invoice["status"] != "Paid"
        if self.verify_rollback:
            self._verify_receive_btc_failure_rollback(order["payment_hash"])
