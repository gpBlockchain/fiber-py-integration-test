"""Real multi-channel send_btc coverage adjacent to PR #1556.

TP-CCH-1556-003 [P1]: six send_btc swaps use three independent wrapped-BTC
Fiber channels (2 + 2 + 2). All six remain in flight until their real LND
hold invoices settle, proving the receive_btc invoice-tracker limit of five
does not constrain the send_btc LND payment-tracker direction.
"""

from hashlib import sha256
import time

from framework.basic_fiber_with_cch import FiberCchTest


CHANNEL_COUNT = 3
SWAPS_PER_CHANNEL = 2
SEND_BTC_SWAP_COUNT = CHANNEL_COUNT * SWAPS_PER_CHANNEL
CHANNEL_BALANCE = 500 * 100000000
PAYMENT_AMOUNT_SATS = 100


class TestPr1556MultiChannelSendBtcPayments(FiberCchTest):
    """Exercise real CCH Fiber-to-LND payments across multiple channels."""

    def _new_lnd_hold_invoice(self, description):
        preimage = self.generate_random_preimage()
        payment_hash = (
            f"0x{sha256(bytes.fromhex(preimage.removeprefix('0x'))).hexdigest()}"
        )
        invoice = self.LNDs[1].addholdinvoice(
            payment_hash.removeprefix("0x"), PAYMENT_AMOUNT_SATS, description
        )
        return invoice, preimage, payment_hash

    def _wait_lnd_invoice_state(self, payment_hash, expected_state, timeout=180):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            invoice = self.LNDs[1].ln_cli_with_cmd(
                f"lookupinvoice {payment_hash.removeprefix('0x')}"
            )
            if invoice["state"] == expected_state:
                return invoice
            time.sleep(1)

        raise AssertionError(
            f"LND invoice {payment_hash} did not reach {expected_state} within {timeout}s"
        )

    def _pending_inbound_tlc_count(self, payer, payment_hashes):
        expected_hashes = set(payment_hashes)
        channels = self.fiber1.get_client().list_channels(
            {"pubkey": payer.get_pubkey()}
        )["channels"]
        assert len(channels) == 1
        assert channels[0]["state"]["state_name"] == "ChannelReady"

        return sum(
            1
            for tlc in channels[0]["pending_tlcs"]
            if tlc["payment_hash"] in expected_hashes and "Inbound" in tlc["status"]
        )

    def test_six_send_btc_swaps_across_three_channels(self):
        """TP-CCH-1556-003: six real send_btc swaps remain independently active."""
        udt_script = self.get_account_udt_script(self.fiber1.account_private)

        self.faucet(
            self.fiber1.account_private,
            0,
            self.fiber1.account_private,
            CHANNEL_COUNT * 3 * CHANNEL_BALANCE,
        )
        payers = []
        for _ in range(CHANNEL_COUNT):
            payer = self.start_new_fiber(
                self.generate_account(
                    10000,
                    self.fiber1.account_private,
                    CHANNEL_BALANCE * 10,
                ),
                fiber_version=self.fiber_version,
            )
            self.open_channel(
                payer,
                self.fiber1,
                CHANNEL_BALANCE,
                CHANNEL_BALANCE,
                fiber1_fee=0,
                fiber2_fee=0,
                udt=udt_script,
            )
            payers.append(payer)

        swaps = []
        for swap_index in range(SEND_BTC_SWAP_COUNT):
            payer = payers[swap_index // SWAPS_PER_CHANNEL]
            lnd_invoice, preimage, payment_hash = self._new_lnd_hold_invoice(
                f"pr1556-send-btc-{swap_index}"
            )
            order = self.fiber1.get_client().send_btc(
                {"btc_pay_req": lnd_invoice["payment_request"], "currency": "Fibd"}
            )
            assert order["payment_hash"] == payment_hash
            assert "Fiber" in order["incoming_invoice"]

            payment = payer.get_client().send_payment(
                {"invoice": order["incoming_invoice"]["Fiber"]}
            )
            assert payment["payment_hash"] == payment_hash
            swaps.append((payer, preimage, payment_hash, payment))

        for payer, _preimage, payment_hash, _payment in swaps:
            self.wait_cch_order_state(
                self.fiber1, payment_hash, "OutgoingInFlight", timeout=180
            )
            self.wait_payment_state(payer, payment_hash, "Inflight", timeout=180)
            self.wait_invoice_state(self.fiber1, payment_hash, "Received", timeout=180)
            self._wait_lnd_invoice_state(payment_hash, "ACCEPTED")

        for payer in payers:
            channel_hashes = [
                payment_hash
                for swap_payer, _preimage, payment_hash, _payment in swaps
                if swap_payer is payer
            ]
            assert self._pending_inbound_tlc_count(payer, channel_hashes) == (
                SWAPS_PER_CHANNEL
            )

        for _payer, preimage, _payment_hash, _payment in swaps:
            self.LNDs[1].ln_cli_with_cmd(f"settleinvoice {preimage.removeprefix('0x')}")

        for payer, _preimage, payment_hash, _payment in swaps:
            self._wait_lnd_invoice_state(payment_hash, "SETTLED")
            self.wait_cch_order_state(self.fiber1, payment_hash, "Success", timeout=180)
            self.wait_payment_state(payer, payment_hash, "Success", timeout=180)
            self.wait_invoice_state(self.fiber1, payment_hash, "Paid", timeout=180)
