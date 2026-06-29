"""PR #1355 regression: pending TLC state can be resumed after migration."""

import pytest

from framework.test_fiber import FiberConfigPath
from test_cases.fiber.devnet.settle_invoice.test_settle_invoice import sha256_hex

from ._helpers import (
    LATEST_DB_VERSION_AFTER_PR1323,
    assert_log_matches,
    fiber_bin_exists,
    list_channels_with_timeout,
    MigrationFiberTest,
    start_with_confirm,
    wait_log_matches,
    wait_peer_connected,
)

pytestmark = pytest.mark.skipif(
    not fiber_bin_exists("download/fiber/0.8.1/fnn"),
    reason="v0.8.1 binary not downloaded (run download_fiber.py first)",
)


class TestPendingHoldInvoiceMigration(MigrationFiberTest):
    def test_auto_migrate_v081_pending_hold_invoice(self):
        old_a = self.start_new_fiber(
            self.generate_account(10000), fiber_version=FiberConfigPath.V081_DEV
        )
        old_b = self.start_new_fiber(
            self.generate_account(10000), fiber_version=FiberConfigPath.V081_DEV
        )

        self.open_channel(old_a, old_b, 1000 * 100000000, 0)
        preimage = self.generate_random_preimage()
        payment_hash = sha256_hex(preimage)
        invoice = old_b.get_client().new_invoice(
            {
                "amount": hex(1 * 100000000),
                "currency": "Fibd",
                "description": "migration hold invoice",
                "expiry": hex(3600),
                "final_cltv": hex(40),
                "payment_hash": payment_hash,
                "hash_algorithm": "sha256",
            }
        )
        payment = old_a.get_client().send_payment(
            {"invoice": invoice["invoice_address"]}
        )
        self.wait_invoice_state(old_b, payment_hash, "Received", 120, 1)
        old_channels = old_a.get_client().list_channels({})["channels"]
        assert len(old_channels[0]["pending_tlcs"]) >= 1

        old_a.stop()
        old_b.stop()

        old_a.fiber_config_enum = FiberConfigPath.CURRENT_DEV
        old_b.fiber_config_enum = FiberConfigPath.CURRENT_DEV
        start_with_confirm(old_a, confirm="y")
        start_with_confirm(old_b, confirm="y")
        old_a.connect_peer(old_b)
        wait_peer_connected(old_a)

        wait_log_matches(
            old_a, r"Migrating to {}".format(LATEST_DB_VERSION_AFTER_PR1323)
        )
        assert_log_matches(old_a, r"connectivity_state and external_funding")

        channels = list_channels_with_timeout(old_a)
        assert len(channels) == len(old_channels), "channel must survive migration"
        assert channels[0]["state"]["state_name"] == "ChannelReady"

        invoice_after_restart = old_b.get_client().get_invoice(
            {"payment_hash": payment_hash}
        )
        assert invoice_after_restart["status"] == "Received"

        old_b.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )
        self.wait_payment_state(old_a, payment["payment_hash"], "Success", 120, 1)
        invoice_paid = old_b.get_client().get_invoice({"payment_hash": payment_hash})
        assert invoice_paid["status"] == "Paid"
