import time

import pytest

from framework.basic_fiber_with_cch import FiberCchTest


# @pytest.mark.skip("https://github.com/nervosnetwork/fiber/issues/1216 https://github.com/nervosnetwork/fiber/pull/1498")
class TestCCHExpiryDeltaSeconds(FiberCchTest):
    """
    https://github.com/nervosnetwork/fiber/issues/1216
        Currently, when a CCH order expires due to expiry_delta_seconds in Fiber, only the order itself is marked as failed (CchOrder.status = Failed), but the associated incoming_invoice is not cleaned up. In business reality, if an expired order is failed but the incoming_invoice is not cancelled/failed, it can cause:

        Pending TLC or hold invoice resources remain locked
        Cooperative channel shutdown or Watchtower processes can be blocked
        Users may mistakenly attempt payments to invalid orders
        Suggested improvements:

        Add a CancelIncomingInvoice action, symmetric with the existing SettleIncomingInvoice
        When an order enters the Failed status, proactively trigger cancel/cleanup behavior for the incoming_invoice, covering both Fiber and Lightning types
        The scheduler expire_order logic should ideally route through the actor/state machine to ensure dispatcher actions can always be triggered (rather than a direct store update)
        Supplement integration tests to verify that after order expiry, incoming_invoice is appropriately cancelled/released
        Relevant code modules:

        crates/fiber-lib/src/cch/actions/mod.rs
        crates/fiber-lib/src/cch/actions/cancel_incoming_invoice.rs (recommended new module)
        scheduler / actor / state machine logic
        Improving this cleanup mechanism will ensure consistency on failed paths and more robust resource handling.


    """

    def _restart_fiber1_with_expiry(self, expiry_seconds):
        self.fiber1.stop()
        self.fiber1.prepare(
            {
                "cch": True,
                "cch_lnd_cert_path": f"{self.LNDs[0].tmp_path}/tls.cert",
                "cch_lnd_rpc_url": f"https://localhost:{self.LNDs[0].rpc_port}",
                "cch_order_expiry_delta_seconds": expiry_seconds,
            }
        )
        self.fiber1.start()

    def _wait_cch_order_failed(self, payment_hash, timeout):
        start = time.time()
        while time.time() - start < timeout:
            order = self.fiber1.get_client().get_cch_order(
                {"payment_hash": payment_hash}
            )
            if str(order["status"]).lower() == "failed":
                return order, time.time() - start
            time.sleep(1)
        raise TimeoutError(
            f"CCH order {payment_hash} did not become Failed in {timeout}s"
        )

    def _wait_lnd_invoice_state(self, lnd, payment_hash, state, timeout=30):
        start = time.time()
        while time.time() - start < timeout:
            invoice = lnd.ln_cli_with_cmd(
                f"lookupinvoice {payment_hash.replace('0x', '')}"
            )
            if invoice["state"] == state:
                return invoice
            time.sleep(1)
        raise TimeoutError(
            f"LND invoice {payment_hash} did not become {state} in {timeout}s"
        )

    def _create_receive_btc_order(self):
        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(1000),
                "currency": "Fibd",
                "description": "test cch order expiry",
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                "payment_preimage": self.generate_random_preimage(),
                "hash_algorithm": "sha256",
            }
        )
        return self.fiber1.get_client().receive_btc(
            {
                "fiber_pay_req": invoice["invoice_address"],
            }
        )

    def _create_send_btc_order(self):
        lnd_invoice = self.LNDs[1].addinvoice(1000, "test-cch-order-expiry")
        return self.fiber1.get_client().send_btc(
            {
                "btc_pay_req": lnd_invoice["payment_request"],
                "currency": "Fibd",
            }
        )

    def _open_udt_channel_to_cch(self):
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

    def _assert_pending_tlc(self, fiber, payment_hash, direction):
        pending_tlc = self.get_pending_tlc(fiber, payment_hash)
        active_tlcs = [
            entry
            for entry in pending_tlc[direction]
            if next(iter(entry["tlc"]["status"].values())) != "RemoveAckConfirmed"
        ]
        assert active_tlcs, (
            f"expected {direction} pending TLC for {payment_hash}, "
            f"got {pending_tlc}"
        )
        return active_tlcs

    def _pending_tlcs_empty_or_removed(self, pending_tlc):
        return all(
            next(iter(entry["tlc"]["status"].values())) == "RemoveAckConfirmed"
            for entries in pending_tlc.values()
            for entry in entries
        )

    def _wait_pending_tlc_removed(self, fiber, payment_hash, timeout=30):
        start = time.time()
        while time.time() - start < timeout:
            pending_tlc = self.get_pending_tlc(fiber, payment_hash)
            if self._pending_tlcs_empty_or_removed(pending_tlc):
                return pending_tlc
            time.sleep(1)
        raise TimeoutError(
            f"pending TLC for {payment_hash} was not removed in {timeout}s: "
            f"{pending_tlc}"
        )

    def test_order_expiry_delta_seconds_should_mark_order_failed(self):
        self.faucet(
            self.fiber2.account_private,
            0,
            self.fiber1.account_private,
            300000 * 100000000,
        )
        self.open_channel(
            self.fiber2,
            self.fiber1,
            100000 * 100000000,
            100000 * 100000000,
            udt=self.get_account_udt_script(self.fiber1.account_private),
        )
        expiry_seconds = 10
        self._restart_fiber1_with_expiry(expiry_seconds)

        with open(self.fiber1.fiber_config_path, "r") as f:
            config_text = f.read()
        assert f"order_expiry_delta_seconds: {expiry_seconds}" in config_text

        receive_btc_result = self._create_receive_btc_order()
        payment_hash = receive_btc_result["payment_hash"]
        order = self.fiber1.get_client().get_cch_order({"payment_hash": payment_hash})

        assert "expiry_delta_seconds" in order
        actual_expiry = order["expiry_delta_seconds"]
        if isinstance(actual_expiry, str):
            actual_expiry = int(actual_expiry, 16)
        assert actual_expiry == expiry_seconds

        order, elapsed = self._wait_cch_order_failed(
            payment_hash=payment_hash,
            timeout=expiry_seconds + 30,
        )
        assert str(order["status"]).lower() == "failed"
        assert elapsed >= expiry_seconds - 1

    def test_expired_order_should_cancel_incoming_lightning_invoice(self):
        self.faucet(
            self.fiber2.account_private,
            0,
            self.fiber1.account_private,
            300000 * 100000000,
        )
        self.open_channel(
            self.fiber2,
            self.fiber1,
            100000 * 100000000,
            100000 * 100000000,
            udt=self.get_account_udt_script(self.fiber1.account_private),
        )
        expiry_seconds = 10
        self._restart_fiber1_with_expiry(expiry_seconds)

        receive_btc_result = self._create_receive_btc_order()
        payment_hash = receive_btc_result["payment_hash"]

        self._wait_cch_order_failed(
            payment_hash=payment_hash, timeout=expiry_seconds + 30
        )
        lnd_invoice = self._wait_lnd_invoice_state(
            self.LNDs[0], payment_hash, "CANCELED"
        )
        assert lnd_invoice["state"] == "CANCELED"

    def test_expired_order_should_cancel_incoming_fiber_invoice(self):
        expiry_seconds = 10
        self._restart_fiber1_with_expiry(expiry_seconds)

        send_btc_result = self._create_send_btc_order()
        payment_hash = send_btc_result["payment_hash"]
        self._wait_cch_order_failed(
            payment_hash=payment_hash, timeout=expiry_seconds + 30
        )
        self.wait_invoice_state(self.fiber1, payment_hash, "Cancelled", timeout=30)

    def test_cancelled_outgoing_hold_invoice_should_cancel_incoming_fiber_invoice(
        self,
    ):
        self._open_udt_channel_to_cch()

        payment_hash = self.generate_random_preimage()
        lnd_invoice = self.LNDs[1].addholdinvoice(
            payment_hash.replace("0x", ""),
            1000,
            "test-cch-hold-invoice-cancel",
        )
        send_btc_result = self.fiber1.get_client().send_btc(
            {
                "btc_pay_req": lnd_invoice["payment_request"],
                "currency": "Fibd",
            }
        )
        payment = self.fiber2.get_client().send_payment(
            {"invoice": send_btc_result["incoming_invoice"]["Fiber"]}
        )
        assert payment["payment_hash"] == payment_hash

        self.wait_payment_state(self.fiber2, payment_hash, "Inflight")
        self.wait_invoice_state(self.fiber1, payment_hash, "Received", timeout=30)
        self.wait_cch_order_state(self.fiber1, payment_hash, "OutgoingInFlight")
        self._assert_pending_tlc(self.fiber1, payment_hash, "Inbound")
        self._assert_pending_tlc(self.fiber2, payment_hash, "Outbound")

        self.LNDs[1].ln_cli_with_cmd(f"cancelinvoice {payment_hash.replace('0x', '')}")

        self._wait_cch_order_failed(payment_hash=payment_hash, timeout=30)
        self.wait_payment_state(self.fiber2, payment_hash, "Failed")
        self.wait_invoice_state(self.fiber1, payment_hash, "Cancelled", timeout=30)
        self._wait_pending_tlc_removed(self.fiber1, payment_hash)
        self._wait_pending_tlc_removed(self.fiber2, payment_hash)

    def test_cancelled_outgoing_fiber_hold_invoice_should_cancel_incoming_lnd_invoice(
        self,
    ):
        self._open_udt_channel_to_cch()

        payment_hash = self.generate_random_preimage()
        fiber_invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(1000),
                "currency": "Fibd",
                "description": "test cch lnd incoming cancel",
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                "payment_hash": payment_hash,
                "hash_algorithm": "sha256",
                "expiry": hex(21610),
                "final_cltv": "0x28",
            }
        )
        receive_btc_result = self.fiber1.get_client().receive_btc(
            {"fiber_pay_req": fiber_invoice["invoice_address"]}
        )
        assert receive_btc_result["payment_hash"] == payment_hash

        self.LNDs[1].ln_cli_with_cmd_without_json(
            f"payinvoice {receive_btc_result['incoming_invoice']['Lightning']} --force &"
        )
        time.sleep(5)
        self.wait_payment_state(self.fiber1, payment_hash, "Inflight")
        self.wait_invoice_state(self.fiber2, payment_hash, "Received", timeout=30)
        self.wait_cch_order_state(self.fiber1, payment_hash, "OutgoingInFlight")
        self._assert_pending_tlc(self.fiber1, payment_hash, "Outbound")
        self._assert_pending_tlc(self.fiber2, payment_hash, "Inbound")

        self.fiber2.get_client().cancel_invoice({"payment_hash": payment_hash})

        self._wait_cch_order_failed(payment_hash=payment_hash, timeout=30)
        self._wait_pending_tlc_removed(self.fiber1, payment_hash)
        self._wait_pending_tlc_removed(self.fiber2, payment_hash)
        lnd_invoice = self._wait_lnd_invoice_state(
            self.LNDs[0], payment_hash, "CANCELED"
        )
        assert lnd_invoice["state"] == "CANCELED"
