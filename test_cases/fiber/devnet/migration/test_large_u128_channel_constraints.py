"""PR #1355 regression: explicit large u128 channel constraints must migrate.

This covers the bug class fixed by avoiding JSON round-trips in channel data
migration. A v0.8.1 channel can persist u128-sized constraint fields; current
fnn must migrate that data and still read the channel normally.
"""

import time

import pytest

from framework.config import DEFAULT_MIN_DEPOSIT_CKB
from framework.test_fiber import FiberConfigPath

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

        self.send_invoice_payment(old_a, old_b, 1, False)
        self.send_invoice_payment(old_b, old_a, 1, False)
        old_channel_count = len(old_a.get_client().list_channels({})["channels"])
        assert old_channel_count >= 1

        old_a.stop()
        old_b.stop()

        old_a.fiber_config_enum = FiberConfigPath.CURRENT_DEV
        old_b.fiber_config_enum = FiberConfigPath.CURRENT_DEV
        start_with_confirm(old_a, confirm="y")
        start_with_confirm(old_b, confirm="y")
        old_a.connect_peer(old_b)
        wait_peer_connected(old_a, timeout=30)

        wait_log_matches(
            old_a, r"Migrating to {}".format(LATEST_DB_VERSION_AFTER_PR1323)
        )
        assert_log_matches(old_a, r"connectivity_state and external_funding")

        chans = list_channels_with_timeout(old_a)
        assert len(chans) == old_channel_count, "channel must survive migration"
        assert all(c["state"]["state_name"] == "ChannelReady" for c in chans), chans

        send_invoice_payment_with_retry(self, old_a, old_b, 1)
        send_invoice_payment_with_retry(self, old_b, old_a, 1)
