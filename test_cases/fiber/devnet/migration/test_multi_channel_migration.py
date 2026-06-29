"""PR #1355 regression: multiple channel actor records migrate together."""

import pytest

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


class TestMultiChannelMigration(MigrationFiberTest):
    def test_auto_migrate_v081_three_node_route(self):
        old_a = self.start_new_fiber(
            self.generate_account(10000), fiber_version=FiberConfigPath.V081_DEV
        )
        old_b = self.start_new_fiber(
            self.generate_account(10000), fiber_version=FiberConfigPath.V081_DEV
        )
        old_c = self.start_new_fiber(
            self.generate_account(10000), fiber_version=FiberConfigPath.V081_DEV
        )

        self.open_channel(old_a, old_b, 1000 * 100000000, 0)
        self.open_channel(old_b, old_c, 1000 * 100000000, 0)
        send_invoice_payment_with_retry(self, old_a, old_c, 1)

        old_b_channel_count = len(old_b.get_client().list_channels({})["channels"])
        assert old_b_channel_count >= 2

        old_a.stop()
        old_b.stop()
        old_c.stop()

        old_a.fiber_config_enum = FiberConfigPath.CURRENT_DEV
        old_b.fiber_config_enum = FiberConfigPath.CURRENT_DEV
        old_c.fiber_config_enum = FiberConfigPath.CURRENT_DEV
        start_with_confirm(old_a, confirm="y")
        start_with_confirm(old_b, confirm="y")
        start_with_confirm(old_c, confirm="y")
        old_a.connect_peer(old_b)
        old_b.connect_peer(old_c)
        wait_peer_connected(old_a)
        wait_peer_connected(old_b)

        wait_log_matches(
            old_b, r"Migrating to {}".format(LATEST_DB_VERSION_AFTER_PR1323)
        )
        assert_log_matches(old_b, r"connectivity_state and external_funding")

        channels = list_channels_with_timeout(old_b)
        assert len(channels) == old_b_channel_count, "all channels must survive"
        assert all(c["state"]["state_name"] == "ChannelReady" for c in channels)

        send_invoice_payment_with_retry(self, old_a, old_c, 1)
