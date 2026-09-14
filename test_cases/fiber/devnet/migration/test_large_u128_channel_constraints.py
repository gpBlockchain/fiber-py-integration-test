"""PR #1355 regression: explicit large u128 channel constraints must migrate.

This covers the bug class fixed by avoiding JSON round-trips in channel data
migration. While a payment is Inflight, both v0.8.1 nodes are replaced with
current fnn; the large u128 constraint and pending payment must resume together.
"""

import time

import pytest

from framework.config import DEFAULT_MIN_DEPOSIT_CKB
from framework.test_fiber import FiberConfigPath
from test_cases.fiber.devnet.settle_invoice.test_settle_invoice import sha256_hex

from ._helpers import (
    LATEST_DB_VERSION_AFTER_PR1323,
    assert_log_matches,
    fiber_bin_exists,
    list_channels_with_timeout,
    MigrationFiberTest,
    send_invoice_payment_with_retry,
    start_with_confirm,
    wait_log_matches,
    wait_peer_connected,
)

pytestmark = pytest.mark.skipif(
    not fiber_bin_exists("download/fiber/0.8.1/fnn"),
    reason="v0.8.1 binary not downloaded (run download_fiber.py first)",
)


class TestLargeU128ChannelConstraints(MigrationFiberTest):
    def test_auto_migrate_v081_explicit_max_tlc_value_in_flight(self):
        large_max_tlc_value = (1 << 128) - 1

        old_a = self.start_new_fiber(
            self.generate_account(10000), fiber_version=FiberConfigPath.V081_DEV
        )
        old_b = self.start_new_fiber(
            self.generate_account(10000), fiber_version=FiberConfigPath.V081_DEV
        )
        old_a.connect_peer(old_b)
        wait_peer_connected(old_a)

        temporary_channel = old_a.get_client().open_channel(
            {
                "pubkey": old_b.get_pubkey(),
                "funding_amount": hex(DEFAULT_MIN_DEPOSIT_CKB + 10 * 100000000),
                "public": True,
                "max_tlc_value_in_flight": hex(large_max_tlc_value),
            }
        )
        time.sleep(1)
        old_b.get_client().accept_channel(
            {
                "temporary_channel_id": temporary_channel["temporary_channel_id"],
                "funding_amount": hex(1000 * 100000000),
                "max_tlc_value_in_flight": hex(large_max_tlc_value),
            }
        )
        self.wait_for_channel_state(
            old_a.get_client(), old_b.get_pubkey(), "ChannelReady"
        )

        # Start a payment and keep it Inflight at the receiver.  The hold invoice
        # makes the replacement point deterministic: AddTlc is committed on both
        # sides, but RemoveTlc has not started yet.
        preimage = self.generate_random_preimage()
        payment_hash = sha256_hex(preimage)
        invoice = old_b.get_client().new_invoice(
            {
                "amount": hex(1),
                "currency": "Fibd",
                "description": "large u128 migration hold invoice",
                "expiry": hex(3600),
                "final_cltv": hex(40),
                "payment_hash": payment_hash,
                "hash_algorithm": "sha256",
            }
        )
        payment = old_a.get_client().send_payment(
            {"invoice": invoice["invoice_address"]}
        )
        assert payment["payment_hash"] == payment_hash
        self.wait_invoice_state(old_b, payment_hash, "Received", 120, 1)
        payment_before_replace = old_a.get_client().get_payment(
            {"payment_hash": payment_hash}
        )
        assert payment_before_replace["status"] == "Inflight"

        old_a_channels = old_a.get_client().list_channels({})["channels"]
        old_b_channels = old_b.get_client().list_channels({})["channels"]
        assert any(channel["pending_tlcs"] for channel in old_a_channels)
        assert any(channel["pending_tlcs"] for channel in old_b_channels)

        old_channel_count = len(old_a_channels)
        assert old_channel_count >= 1

        # Replace both v0.8.1 nodes while the payment is still Inflight.
        old_a.stop()
        old_b.stop()

        old_a.fiber_config_enum = FiberConfigPath.CURRENT_DEV
        old_b.fiber_config_enum = FiberConfigPath.CURRENT_DEV
        start_with_confirm(old_a, confirm="y")
        start_with_confirm(old_b, confirm="y")
        old_a.connect_peer(old_b)
        wait_peer_connected(old_a, timeout=30)
        wait_peer_connected(old_b, timeout=30)

        wait_log_matches(
            old_a, r"Migrating to {}".format(LATEST_DB_VERSION_AFTER_PR1323)
        )
        assert_log_matches(old_a, r"connectivity_state and external_funding")

        chans = list_channels_with_timeout(old_a)
        assert len(chans) == old_channel_count, "channel must survive migration"
        assert all(c["state"]["state_name"] == "ChannelReady" for c in chans), chans
        assert any(
            c["pending_tlcs"] for c in chans
        ), f"pending TLC must survive node replacement: {chans}"

        invoice_after_restart = old_b.get_client().get_invoice(
            {"payment_hash": payment_hash}
        )
        assert invoice_after_restart["status"] == "Received"
        payment_after_replace = old_a.get_client().get_payment(
            {"payment_hash": payment_hash}
        )
        assert payment_after_replace["status"] == "Inflight"

        # Complete the same payment after replacement and prove the migrated
        # channel remains usable for new payments in both directions.
        old_b.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )
        self.wait_payment_state(old_a, payment_hash, "Success", 120, 1)
        invoice_after_settle = old_b.get_client().get_invoice(
            {"payment_hash": payment_hash}
        )
        assert invoice_after_settle["status"] == "Paid"

        for _ in range(30):
            migrated_a_channels = old_a.get_client().list_channels({})["channels"]
            migrated_b_channels = old_b.get_client().list_channels({})["channels"]
            if all(
                not channel["pending_tlcs"]
                for channel in migrated_a_channels + migrated_b_channels
            ):
                break
            time.sleep(1)
        else:
            assert False, (
                "migrated TLC did not fully settle: "
                f"old_a={migrated_a_channels}, old_b={migrated_b_channels}"
            )

        send_invoice_payment_with_retry(self, old_a, old_b, 1)
        send_invoice_payment_with_retry(self, old_b, old_a, 1)
