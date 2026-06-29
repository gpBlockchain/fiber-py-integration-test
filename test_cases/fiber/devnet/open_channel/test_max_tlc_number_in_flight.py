import hashlib
import time

import pytest

from framework.basic_fiber import FiberTest
from framework.config import DEFAULT_MIN_DEPOSIT_CKB


def sha256_hex(preimage_hex: str) -> str:
    raw = bytes.fromhex(preimage_hex.replace("0x", ""))
    return "0x" + hashlib.sha256(raw).hexdigest()


class TestMaxTlcNumberInFlight(FiberTest):
    def new_hold_invoice(self, fiber, amount, description):
        preimage = self.generate_random_preimage()
        payment_hash = sha256_hex(preimage)
        invoice = fiber.get_client().new_invoice(
            {
                "amount": hex(amount),
                "currency": "Fibd",
                "description": description,
                "payment_hash": payment_hash,
                "hash_algorithm": "sha256",
            }
        )
        return invoice, payment_hash, preimage

    def test_max_tlc_number_in_flight_above_system_max(self):
        max_tlc_number_in_flights = ["0x7e", "0xffffffffff"]
        for max_tlc_number_in_flight in max_tlc_number_in_flights:
            with pytest.raises(Exception) as exc_info:
                temporary_channel_id = self.fiber1.get_client().open_channel(
                    {
                        "pubkey": self.fiber2.get_pubkey(),
                        "funding_amount": hex(200 * 100000000),
                        "public": True,
                        "max_tlc_number_in_flight": max_tlc_number_in_flight,
                    }
                )
            expected_error_message = "greater than the system maximal value 125"
            assert expected_error_message in exc_info.value.args[0], (
                f"Expected substring '{expected_error_message}' "
                f"not found in actual string '{exc_info.value.args[0]}'"
            )

    def test_hold_invoice_occupies_tlc_number_in_flight_limit(self):
        """
        Hold invoices keep TLCs in-flight.

        Note: this send_payment-based scenario cannot reliably reach the
        channel-level max_tlc_number_in_flight value of 125. Before the channel
        actor checks the 125-TLC limit, the graph router discounts a channel by
        0.95 ** pending_count and filters it once the route probability drops
        below 0.01. That happens at about 90 pending TLCs, so this case may fail
        with "Failed to build route" before it can exercise the channel limit.
        """
        # 因为测不了125 所以测60
        max_tlc_number_in_flight = 10
        temporary_channel = self.fiber1.get_client().open_channel(
            {
                "pubkey": self.fiber2.get_pubkey(),
                "funding_amount": hex(DEFAULT_MIN_DEPOSIT_CKB + 1000),
                "public": True,
            }
        )
        time.sleep(1)
        # 2. Accept the channel with fiber2 as the client
        self.fiber2.get_client().accept_channel(
            {
                "temporary_channel_id": temporary_channel["temporary_channel_id"],
                "funding_amount": hex(1000 * 100000000),
                "max_tlc_number_in_flight": hex(max_tlc_number_in_flight),
            }
        )
        # 3. Wait for the channel state to be "ChannelReady"
        self.wait_for_channel_state(
            self.fiber2.get_client(), self.fiber1.get_pubkey(), "ChannelReady"
        )

        self.fiber1.get_client().graph_channels()
        self.fiber1.get_client().graph_nodes()
        channel_id = self.fiber1.get_client().list_channels(
            {"pubkey": self.fiber2.get_pubkey()}
        )["channels"][0]["channel_id"]
        self.send_payment(self.fiber2, self.fiber1, 100 * 100000000)
        amount = 1 * 100000000
        for i in range(max_tlc_number_in_flight):
            print("current max_tlc_number_in_flight:", i)
            invoice, payment_hash, _preimage = self.new_hold_invoice(
                self.fiber2, amount, f"hold invoice {i}"
            )
            payment = self.fiber1.get_client().send_payment(
                {"invoice": invoice["invoice_address"]}
            )
            self.wait_invoice_state(self.fiber2, payment_hash, "Received", 120, 1)
            self.wait_payment_state(
                self.fiber1, payment["payment_hash"], "Inflight", 30, 1
            )

        invoice, payment_hash, _preimage = self.new_hold_invoice(
            self.fiber2, amount, "hold invoice over limit"
        )
        over_limit_failed = False
        try:
            payment = self.fiber1.get_client().send_payment(
                {"invoice": invoice["invoice_address"]}
            )
        except Exception as exc:
            error = str(exc)
            assert (
                "TlcNumberExceedLimit" in error
                or "TemporaryChannelFailure" in error
                or "Failed to build route" in error
            )
            over_limit_failed = True
        if not over_limit_failed:
            self.wait_payment_state(
                self.fiber1, payment["payment_hash"], "Failed", 120, 1
            )
            failed_payment = self.fiber1.get_client().get_payment(
                {"payment_hash": payment["payment_hash"]}
            )
            failed_error = str(failed_payment.get("failed_error"))
            assert (
                "TlcNumberExceedLimit" in failed_error
                or "TemporaryChannelFailure" in failed_error
                or "Failed to build route" in failed_error
            )

        self.fiber1.get_client().shutdown_channel(
            {
                "channel_id": channel_id,
                "force": True,
            }
        )
        time.sleep(2)
        self.fiber1.get_client().node_info()
        channels_after_shutdown = self.fiber1.get_client().list_channels(
            {"pubkey": self.fiber2.get_pubkey(), "include_closed": True}
        )
        assert any(
            channel["channel_id"] == channel_id
            for channel in channels_after_shutdown["channels"]
        )

    # @pytest.mark.skip("https://github.com/nervosnetwork/fiber/issues/450")
    def test_max_tlc_number_in_flight_zero(self):
        """
        max_tlc_number_in_flight = 0
        Returns:
        """

        temporary_channel_id = self.fiber1.get_client().open_channel(
            {
                "pubkey": self.fiber2.get_pubkey(),
                "funding_amount": hex(200 * 100000000),
                "public": True,
                "max_tlc_number_in_flight": "0x0",
            }
        )
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_pubkey(), "ChannelReady", 120
        )
        time.sleep(5)
        # transfer
        self.fiber1.get_client().graph_channels()
        self.fiber1.get_client().graph_nodes()
        payment_preimage = self.generate_random_preimage()
        invoice_balance = 100 * 100000000
        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(invoice_balance),
                "currency": "Fibd",
                "description": "test invoice generated by node2",
                "payment_preimage": payment_preimage,
                "hash_algorithm": "sha256",
            }
        )
        before_channel = self.fiber1.get_client().list_channels({})

        payment = self.fiber1.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
            }
        )
        # expected_error_message = "TemporaryChannelFailure"
        # assert expected_error_message in payment["failed_error"]
        self.wait_payment_state(self.fiber1, payment["payment_hash"], "Success")

        invoice_balance = 100 * 100000000
        invoice = self.fiber1.get_client().new_invoice(
            {
                "amount": hex(invoice_balance),
                "currency": "Fibd",
                "description": "test invoice generated by node2",
                "payment_preimage": self.generate_random_preimage(),
                "hash_algorithm": "sha256",
            }
        )

        payment = self.fiber2.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
            }
        )
        # expected_error_message = "TemporaryChannelFailure"
        # assert expected_error_message in payment["failed_error"]
        self.wait_payment_state(self.fiber2, payment["payment_hash"], "Failed")
