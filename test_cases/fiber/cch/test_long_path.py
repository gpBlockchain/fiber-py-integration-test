import time

from framework.basic_fiber_with_cch import FiberCchTest


class TestLongPath(FiberCchTest):

    # LND's production default is 90 seconds; regtest can relay immediately.
    start_lnd_config = {"lnd_trickle_delay": 100}
    start_fiber_config = {
        "cch_base_fee_sats": 0,
        "cch_fee_rate_per_million_sats": 50000,
        "cch_btc_final_tlc_expiry_delta_blocks": 720,
        "cch_ckb_final_tlc_expiry_delta_seconds": 432000,
    }

    def _wait_lnd_invoice_state(self, lnd, payment_hash, expected, timeout=120):
        rhash = payment_hash[2:] if payment_hash.startswith("0x") else payment_hash
        last = None
        for _ in range(timeout):
            last = lnd.ln_cli_with_cmd(f"lookupinvoice {rhash}")
            if last["state"] == expected:
                return last
            time.sleep(1)
        raise TimeoutError(
            f"LND invoice {payment_hash} did not reach {expected}, last={last}"
        )

    def _wait_cch_success(self, payment_hash):
        self.wait_cch_order_state(self.fiber1, payment_hash, "Success", 180)
        order = self.fiber1.get_client().get_cch_order({"payment_hash": payment_hash})
        assert order["status"] == "Success"
        return order

    def _new_fiber_invoice(self, payee, amount_sats, udt_script, description):
        return payee.get_client().new_invoice(
            {
                "amount": hex(amount_sats),
                "currency": "Fibd",
                "description": description,
                "udt_type_script": udt_script,
                "payment_preimage": self.generate_random_preimage(),
                "hash_algorithm": "sha256",
            }
        )

    def _assert_fiber_invoice_amount(self, fiber, invoice, expected_sats):
        parsed = fiber.get_client().parse_invoice({"invoice": invoice})
        assert int(parsed["invoice"]["amount"], 16) == expected_sats

    def _complete_send_btc(self, payer_fiber, payee_lnd, amount_sats):
        lnd_invoice = payee_lnd.addinvoice(amount_sats, "long-path-send-btc")
        order = self.fiber1.get_client().send_btc(
            {
                "btc_pay_req": lnd_invoice["payment_request"],
                "currency": "Fibd",
            }
        )
        assert "Fiber" in order["incoming_invoice"]
        self._assert_fiber_invoice_amount(
            payer_fiber,
            order["incoming_invoice"]["Fiber"],
            int(order["amount_sats"], 16),
        )

        payment = payer_fiber.get_client().send_payment(
            {
                "invoice": order["incoming_invoice"]["Fiber"],
                "max_fee_rate": hex(1000000000000000),
            }
        )
        assert payment["payment_hash"] == order["payment_hash"]
        self.wait_payment_state(payer_fiber, payment["payment_hash"], "Success", 600)

        final_order = self._wait_cch_success(payment["payment_hash"])
        assert int(final_order["amount_sats"], 16) >= amount_sats
        self._wait_lnd_invoice_state(payee_lnd, payment["payment_hash"], "SETTLED")

    def _complete_receive_btc(
        self, payer_lnd, payee_fiber, amount_sats, udt_script, description
    ):
        invoice = self._new_fiber_invoice(
            payee_fiber, amount_sats, udt_script, description
        )
        order = self.fiber1.get_client().receive_btc(
            {"fiber_pay_req": invoice["invoice_address"]}
        )
        assert "Lightning" in order["incoming_invoice"]
        self._assert_fiber_invoice_amount(
            payee_fiber, order["outgoing_pay_req"], amount_sats
        )

        payer_lnd.payinvoice(order["incoming_invoice"]["Lightning"])

        final_order = self._wait_cch_success(order["payment_hash"])
        assert int(final_order["amount_sats"], 16) == amount_sats + int(
            amount_sats * 0.05
        )
        self.wait_invoice_state(payee_fiber, order["payment_hash"], "Paid", 120)
        self._wait_lnd_invoice_state(self.LNDs[0], order["payment_hash"], "SETTLED")

    def test_send_btc_with_receive_btc_long_path_both_sides(self):
        """Build long paths on both Fiber and LND sides for CCH swaps.

        Covered paths:
            send_btc: fiberN -> ... -> fiber2 -> fiber1(CCH/LND0) -> LND1 -> ... -> LNDN
            receive_btc: LNDN -> ... -> LND1 -> LND0(CCH/fiber1) -> fiber2
            receive_btc: LNDN -> ... -> LND1 -> LND0(CCH/fiber1) -> fiber2 -> ... -> fiberN
            receive_btc: LND1 -> LND0(CCH/fiber1) -> fiber2 -> ... -> fiberN

        The channel graph is funded in both directions so the receive_btc paths
        prove long-route delivery instead of only proving order creation.
        """
        total_fiber_nodes = 6
        total_lnd_nodes = 5
        assert total_fiber_nodes >= 2
        assert total_lnd_nodes >= 2

        udt_script = self.get_account_udt_script(self.fiber1.account_private)
        channel_balance = 300 * 100000000
        payment_amount = 100000

        self.faucet(
            self.fiber2.account_private,
            0,
            self.fiber1.account_private,
            2000 * 100000000,
        )
        fibers = [self.fiber1, self.fiber2]
        for _ in range(total_fiber_nodes - 2):
            account_private = self.generate_account(
                10000,
                self.fiber1.account_private,
                2000 * 100000000,
            )
            fibers.append(
                self.start_new_fiber(account_private, fiber_version=self.fiber_version)
            )
        self.faucet(
            self.fiber1.account_private,
            0,
            self.fiber1.account_private,
            5000 * 100000000,
        )

        # Bidirectional liquidity supports both send_btc incoming and receive_btc outgoing legs.
        for i in range(len(fibers) - 1):
            self.open_channel(
                fibers[i + 1],
                fibers[i],
                channel_balance,
                channel_balance,
                fiber1_fee=0,
                fiber2_fee=0,
                udt=udt_script,
            )

        lnds = [self.LNDs[0], self.LNDs[1]]
        for _ in range(total_lnd_nodes - 2):
            previous_lnd = lnds[-1]
            new_lnd = self.start_new_lnd()
            self.faucetBtc(previous_lnd, 5)
            previous_lnd.open_channel(new_lnd, 1000000, 1, 0)
            self.btcNode.miner(6)
            self.faucetBtc(new_lnd, 5)
            new_lnd.open_channel(previous_lnd, 1000000, 1, 0)
            self.btcNode.miner(6)
            lnds.append(new_lnd)

        # Each adjacent pair has two public channels. Wait until every LND sees
        # both graph edges and their directional policies before routing.
        lnd_pubkeys = [lnd.getinfo()["identity_pubkey"] for lnd in lnds]
        expected_channel_pairs = {
            frozenset((lnd_pubkeys[i], lnd_pubkeys[i + 1]))
            for i in range(len(lnd_pubkeys) - 1)
        }
        graph_sync_timeout = 60
        graph_sync_deadline = time.monotonic() + graph_sync_timeout
        last_missing_pairs = {}
        while time.monotonic() < graph_sync_deadline:
            last_missing_pairs = {}
            for index, lnd in enumerate(lnds):
                graph_edges = lnd.ln_cli_with_cmd("describegraph").get("edges", [])
                synced_channel_counts = {
                    channel_pair: 0 for channel_pair in expected_channel_pairs
                }
                for edge in graph_edges:
                    channel_pair = frozenset((edge["node1_pub"], edge["node2_pub"]))
                    if (
                        channel_pair in expected_channel_pairs
                        and edge.get("node1_policy") is not None
                        and edge.get("node2_policy") is not None
                    ):
                        synced_channel_counts[channel_pair] += 1
                missing_pairs = {
                    channel_pair: channel_count
                    for channel_pair, channel_count in synced_channel_counts.items()
                    if channel_count < 2
                }
                if missing_pairs:
                    last_missing_pairs[index] = missing_pairs
            if not last_missing_pairs:
                break
            time.sleep(1)
        else:
            assert False, (
                "LND graph channels and policies did not sync within "
                f"{graph_sync_timeout} seconds; "
                f"ready channel counts below 2 by node: {last_missing_pairs}"
            )

        for lnd in lnds[1:]:
            invoice = lnd.addinvoice(1000)
            self.LNDs[0].payinvoice(invoice["payment_request"])

        farthest_fiber = fibers[-1]
        farthest_lnd = lnds[-1]

        self.send_payment(farthest_fiber, self.fiber1, 1, True, udt_script)
        self.send_payment(self.fiber1, farthest_fiber, 1, True, udt_script)

        print(
            f"CCH long path - Fiber hops={total_fiber_nodes - 1}, "
            f"LND hops={total_lnd_nodes - 1}"
        )
        self._complete_send_btc(farthest_fiber, farthest_lnd, payment_amount)
        self._complete_receive_btc(
            farthest_lnd,
            self.fiber2,
            payment_amount,
            udt_script,
            "long-path-receive-btc-lndN-to-fiber2",
        )
        self._complete_receive_btc(
            farthest_lnd,
            farthest_fiber,
            payment_amount,
            udt_script,
            "long-path-receive-btc-lndN-to-fiberN",
        )
        self._complete_receive_btc(
            self.LNDs[1],
            farthest_fiber,
            payment_amount,
            udt_script,
            "long-path-receive-btc-lnd1-to-fiberN",
        )
