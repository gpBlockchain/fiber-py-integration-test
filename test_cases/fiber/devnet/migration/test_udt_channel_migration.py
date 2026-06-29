"""PR #1355 regression: UDT channel data must survive auto-migration."""

import pytest

from framework.test_fiber import FiberConfigPath

from ._helpers import (
    LATEST_DB_VERSION_AFTER_PR1323,
    assert_log_matches,
    fiber_bin_exists,
    list_channels_with_timeout,
    MigrationFiberTest,
    start_with_confirm,
    wait_log_matches,
)

pytestmark = pytest.mark.skipif(
    not fiber_bin_exists("download/fiber/0.8.1/fnn"),
    reason="v0.8.1 binary not downloaded (run download_fiber.py first)",
)


class TestUdtChannelMigration(MigrationFiberTest):
    def test_auto_migrate_v081_udt_channel(self):
        udt_script = self.get_account_udt_script(self.account1_private_key)
        old_a = self.start_new_fiber(
            self.generate_account(10000, self.account1_private_key, 2000 * 100000000),
            fiber_version=FiberConfigPath.V081_DEV,
        )
        old_b = self.start_new_fiber(
            self.generate_account(10000), fiber_version=FiberConfigPath.V081_DEV
        )

        self.open_channel(old_a, old_b, 1000 * 100000000, 0, udt=udt_script)
        self.send_payment(old_a, old_b, 1 * 100000000, udt=udt_script)
        old_channels = old_a.get_client().list_channels({})["channels"]
        assert len(old_channels) >= 1
        assert old_channels[0]["funding_udt_type_script"] == udt_script

        old_a.stop()
        old_b.stop()

        old_a.fiber_config_enum = FiberConfigPath.CURRENT_DEV
        old_b.fiber_config_enum = FiberConfigPath.CURRENT_DEV
        start_with_confirm(old_a, confirm="y")
        start_with_confirm(old_b, confirm="y")

        wait_log_matches(
            old_a, r"Migrating to {}".format(LATEST_DB_VERSION_AFTER_PR1323)
        )
        assert_log_matches(old_a, r"connectivity_state and external_funding")

        channels = list_channels_with_timeout(old_a)
        assert len(channels) == len(old_channels), "UDT channel must survive migration"
        assert channels[0]["state"]["state_name"] == "ChannelReady"
        assert channels[0]["funding_udt_type_script"] == udt_script

        self.send_payment(old_a, old_b, 1 * 100000000, udt=udt_script)
