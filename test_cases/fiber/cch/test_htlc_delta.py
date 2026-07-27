import time

from framework.basic_fiber_with_cch import FiberCchTest


BTC_BLOCK_TIME_SECONDS = 10 * 60
PENDING_TLC_TIMEOUT_SECONDS = 30
EXPECTED_LND_TLC_EXPIRY_COUNT = 4
EXPECTED_FIBER_TLC_EXPIRY_COUNT = 4
EXPECTED_EXPIRY_LEVEL_COUNT = 4
EXPIRY_DUPLICATE_TOLERANCE_SECONDS = 2
EXPIRY_ASSERTION_TOLERANCE_SECONDS = 2
THIRTY_HOURS_SECONDS = 30 * 60 * 60
MIN_CROSS_CHAIN_EXPIRY_GAP_SECONDS = 4 * 60 * 60
MIN_HOP_EXPIRY_GAP_SECONDS = 4 * 60 * 60


class TestHtlcDelta(FiberCchTest):
    start_fiber_config = {
        "cch_base_fee_sats": 0,
        "cch_fee_rate_per_million_sats": 5000,
    }
    """ """

    def _start_synced_lnd(self):
        lnd = self.start_new_lnd()
        lnd_info = None
        for _ in range(30):
            lnd_info = lnd.getinfo()
            if lnd_info["synced_to_chain"]:
                return lnd
            time.sleep(1)
        assert False, f"LND did not sync before opening channel: {lnd_info}"

    def _open_two_hop_fiber_route(self):
        self.fiber3 = self.start_new_fiber(
            self.generate_account(10000, self.fiber1.account_private, 10000 * 100000000)
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
        self.open_channel(
            self.fiber2,
            self.fiber3,
            1000 * 100000000,
            1000 * 100000000,
            udt=self.get_account_udt_script(self.fiber1.account_private),
        )

    def _collapse_expiry_levels(self, expiry_seconds):
        expiry_levels = []
        for expiry in sorted(expiry_seconds, reverse=True):
            if (
                not expiry_levels
                or expiry_levels[-1] - expiry > EXPIRY_DUPLICATE_TOLERANCE_SECONDS
            ):
                expiry_levels.append(expiry)
        return expiry_levels

    def _get_all_nodes_tlc_expiry_parts(self, payment_hash):
        expected_tlc_expiry_count = (
            EXPECTED_LND_TLC_EXPIRY_COUNT + EXPECTED_FIBER_TLC_EXPIRY_COUNT
        )
        all_nodes_payment_tlc_expiry = []
        for _ in range(PENDING_TLC_TIMEOUT_SECONDS):
            all_nodes_payment_tlc_expiry = self.get_all_nodes_payment_tlc_expiry(
                payment_hash
            )
            if len(all_nodes_payment_tlc_expiry) == expected_tlc_expiry_count:
                break
            time.sleep(1)

        print("get_all_nodes_payment_tlc_expiry", all_nodes_payment_tlc_expiry)
        assert len(all_nodes_payment_tlc_expiry) == expected_tlc_expiry_count, (
            f"expected {expected_tlc_expiry_count} TLC expiry times for "
            f"{payment_hash}, got "
            f"{all_nodes_payment_tlc_expiry}"
        )
        lnd_expiry_seconds = all_nodes_payment_tlc_expiry[
            :EXPECTED_LND_TLC_EXPIRY_COUNT
        ]
        fiber_expiry_seconds = all_nodes_payment_tlc_expiry[
            EXPECTED_LND_TLC_EXPIRY_COUNT:
        ]
        expiry_levels = self._collapse_expiry_levels(all_nodes_payment_tlc_expiry)
        assert len(expiry_levels) == EXPECTED_EXPIRY_LEVEL_COUNT, (
            f"expected {EXPECTED_EXPIRY_LEVEL_COUNT} distinct expiry levels, "
            f"got {expiry_levels}"
        )
        return lnd_expiry_seconds, fiber_expiry_seconds

    def _assert_receive_btc_tlc_expiry_window(self, payment_hash):
        lnd_expiry_seconds, fiber_expiry_seconds = self._get_all_nodes_tlc_expiry_parts(
            payment_hash
        )

        assert min(lnd_expiry_seconds) > THIRTY_HOURS_SECONDS, (
            f"LND TLC expiries should all be greater than 30h, got "
            f"{lnd_expiry_seconds}"
        )
        assert max(fiber_expiry_seconds) < THIRTY_HOURS_SECONDS, (
            f"Fiber TLC expiries should all be less than 30h, got "
            f"{fiber_expiry_seconds}"
        )
        assert (
            min(lnd_expiry_seconds) - max(fiber_expiry_seconds)
            >= MIN_CROSS_CHAIN_EXPIRY_GAP_SECONDS - EXPIRY_ASSERTION_TOLERANCE_SECONDS
        ), (
            f"LND min expiry should be at least 4h greater than Fiber max "
            f"expiry within {EXPIRY_ASSERTION_TOLERANCE_SECONDS}s tolerance, "
            f"got lnd={lnd_expiry_seconds}, fiber={fiber_expiry_seconds}"
        )

        fiber_expiry_levels = self._collapse_expiry_levels(fiber_expiry_seconds)
        assert (
            len(fiber_expiry_levels) == 2
        ), f"expected 2 Fiber expiry levels, got {fiber_expiry_levels}"
        assert (
            fiber_expiry_levels[0] - fiber_expiry_levels[1]
            >= MIN_HOP_EXPIRY_GAP_SECONDS - EXPIRY_ASSERTION_TOLERANCE_SECONDS
        ), (
            f"Fiber expiry levels should be at least 4h apart within "
            f"{EXPIRY_ASSERTION_TOLERANCE_SECONDS}s tolerance, got "
            f"{fiber_expiry_levels}"
        )

    def _assert_send_btc_tlc_expiry_window(self, payment_hash):
        lnd_expiry_seconds, fiber_expiry_seconds = self._get_all_nodes_tlc_expiry_parts(
            payment_hash
        )

        assert max(lnd_expiry_seconds) < THIRTY_HOURS_SECONDS, (
            f"LND TLC expiries should all be less than 30h for send_btc, got "
            f"{lnd_expiry_seconds}"
        )
        assert min(fiber_expiry_seconds) > THIRTY_HOURS_SECONDS, (
            f"Fiber TLC expiries should all be greater than 30h for send_btc, got "
            f"{fiber_expiry_seconds}"
        )
        assert (
            min(fiber_expiry_seconds) - max(lnd_expiry_seconds)
            >= MIN_CROSS_CHAIN_EXPIRY_GAP_SECONDS - EXPIRY_ASSERTION_TOLERANCE_SECONDS
        ), (
            f"Fiber min expiry should be at least 4h greater than LND max "
            f"expiry within {EXPIRY_ASSERTION_TOLERANCE_SECONDS}s tolerance, "
            f"got lnd={lnd_expiry_seconds}, fiber={fiber_expiry_seconds}"
        )

        lnd_expiry_levels = self._collapse_expiry_levels(lnd_expiry_seconds)
        assert (
            len(lnd_expiry_levels) == 2
        ), f"expected 2 LND expiry levels, got {lnd_expiry_levels}"
        assert (
            lnd_expiry_levels[0] - lnd_expiry_levels[1]
            >= MIN_HOP_EXPIRY_GAP_SECONDS - EXPIRY_ASSERTION_TOLERANCE_SECONDS
        ), (
            f"LND expiry levels should be at least 4h apart within "
            f"{EXPIRY_ASSERTION_TOLERANCE_SECONDS}s tolerance, got "
            f"{lnd_expiry_levels}"
        )
        fiber_expiry_levels = self._collapse_expiry_levels(fiber_expiry_seconds)
        assert (
            len(fiber_expiry_levels) == 2
        ), f"expected 2 Fiber expiry levels, got {fiber_expiry_levels}"
        assert (
            fiber_expiry_levels[0] - fiber_expiry_levels[1]
            >= MIN_HOP_EXPIRY_GAP_SECONDS - EXPIRY_ASSERTION_TOLERANCE_SECONDS
        ), (
            f"Fiber expiry levels should be at least 4h apart within "
            f"{EXPIRY_ASSERTION_TOLERANCE_SECONDS}s tolerance, got "
            f"{fiber_expiry_levels}"
        )

    # @pytest.mark.skip("https://github.com/nervosnetwork/fiber/issues/1218")
    def test_check_tlc_time(self):
        """
        Returns:

        """
        self.LND3 = self._start_synced_lnd()
        self.faucetBtc(self.LND3, 5)
        self.LND3.open_channel(self.LNDs[1], 1000000, 1, 0)
        self.btcNode.miner(6)
        time.sleep(3)
        self._open_two_hop_fiber_route()

        invoice = self.fiber3.get_client().new_invoice(
            {
                "amount": hex(100000),
                "currency": "Fibd",
                "description": "test invoice generated by node2",
                # "payment_preimage": self.generate_random_preimage(),
                "payment_hash": self.generate_random_preimage(),
                "hash_algorithm": "sha256",
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
            }
        )
        btc_response = self.fiber1.get_client().receive_btc(
            {"fiber_pay_req": invoice["invoice_address"]}
        )

        self.LND3.ln_cli_with_cmd_without_json(
            f"payinvoice {btc_response['incoming_invoice']['Lightning']} --force &"
        )
        payment_hash = btc_response["payment_hash"]
        self.wait_invoice_state(self.fiber3, payment_hash, "Received", timeout=30)
        self.wait_payment_state(self.fiber1, payment_hash, "Inflight", timeout=30)
        self.wait_cch_order_state(
            self.fiber1, payment_hash, "OutgoingInFlight", timeout=30
        )

        self._assert_receive_btc_tlc_expiry_window(payment_hash)

    def test_send_btc_check_tlc_time(self):
        self.LND3 = self._start_synced_lnd()
        self.LNDs[1].open_channel(self.LND3, 1000000, 1, 0)
        self.btcNode.miner(6)
        ingrid_p2tr_address = self.LND3.ln_cli_with_cmd("newaddress p2tr")["address"]
        self.btcNode.sendtoaddress(ingrid_p2tr_address, 5, 25)
        self.btcNode.miner(1)
        self.LND3.open_channel(self.LNDs[1], 1000000, 1, 0)
        self.btcNode.miner(10)
        time.sleep(120)
        lnd_invoice = self.LND3.addinvoice(
            1000,
            "send-btc-htlc-delta",
        )
        self.LNDs[0].payinvoice(lnd_invoice["payment_request"])
        self._open_two_hop_fiber_route()

        payment_hash = self.generate_random_preimage()
        lnd_invoice = self.LND3.addholdinvoice(
            payment_hash.replace("0x", ""),
            1000,
            "send-btc-htlc-delta",
        )
        send_btc_response = self.fiber1.get_client().send_btc(
            {
                "btc_pay_req": lnd_invoice["payment_request"],
                "currency": "Fibd",
            }
        )
        assert send_btc_response["payment_hash"] == payment_hash

        payment = self.fiber3.get_client().send_payment(
            {"invoice": send_btc_response["incoming_invoice"]["Fiber"]}
        )
        assert payment["payment_hash"] == payment_hash

        self.wait_payment_state(self.fiber3, payment_hash, "Inflight", timeout=30)
        self.wait_invoice_state(self.fiber1, payment_hash, "Received", timeout=30)
        self.wait_cch_order_state(
            self.fiber1, payment_hash, "OutgoingInFlight", timeout=30
        )

        self._assert_send_btc_tlc_expiry_window(payment_hash)
