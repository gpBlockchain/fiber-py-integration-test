"""PR #1323/#1355 regression: the documented two-step upgrade path
v0.7.0 -> (run v0.8.x `fnn-migrate`) -> current must succeed end-to-end.

This is the upgrade path PR #1323 explicitly preserves: pre-INIT_DB_VERSION
databases must first be brought up to INIT_DB_VERSION using the legacy
standalone migrate tool from a v0.8.x release, then the current binary's
auto-migration takes over for everything from INIT_DB_VERSION onward
(currently the channel actor data migration).
"""

import pytest

from framework.test_fiber import FiberConfigPath

from ._helpers import (
    LATEST_DB_VERSION_AFTER_PR1323,
    fiber_bin_exists,
    list_channels_with_timeout,
    MigrationFiberTest,
    open_v070_channel,
    send_invoice_payment_with_timeout,
    start_with_confirm,
    wait_channels_ready,
    wait_log_matches,
    wait_peer_connected,
)

pytestmark = pytest.mark.skipif(
    not (
        fiber_bin_exists("download/fiber/0.7.0/fnn")
        and fiber_bin_exists("download/fiber/0.8.1/fnn-migrate")
    ),
    reason="v0.7.0 fnn and v0.8.1 fnn-migrate must both be present",
)


class TestTwoStepUpgradeFromV070(MigrationFiberTest):
    def test_v070_then_v081_migrate_then_current_autostart(self):
        # 1. v0.7.0 phase: real channel data on disk
        old_a = self.start_new_fiber(
            self.generate_account(10000), fiber_version=FiberConfigPath.V070_DEV
        )
        old_b = self.start_new_fiber(
            self.generate_account(10000), fiber_version=FiberConfigPath.V070_DEV
        )
        old_a.connect_peer(old_b)
        wait_peer_connected(old_a)
        open_v070_channel(old_a, 1000 * 100000000)
        wait_channels_ready(old_a)
        self.send_invoice_payment(old_a, old_b, 1)
        self.send_invoice_payment(old_b, old_a, 1)
        old_a.stop()
        old_b.stop()

        # 2. step 1 of upgrade: legacy fnn-migrate from v0.8.1
        old_a.fiber_config_enum = FiberConfigPath.V081_DEV
        old_b.fiber_config_enum = FiberConfigPath.V081_DEV
        old_a.migration()
        old_b.migration()

        # 3. step 2 of upgrade: switch to current and let auto-migrate finish
        old_a.fiber_config_enum = FiberConfigPath.CURRENT_DEV
        old_b.fiber_config_enum = FiberConfigPath.CURRENT_DEV
        start_with_confirm(old_a, confirm="y")
        start_with_confirm(old_b, confirm="y")

        wait_log_matches(
            old_a, r"Migrating to {}".format(LATEST_DB_VERSION_AFTER_PR1323)
        )

        # 4. liveness checks
        chans = list_channels_with_timeout(old_a)
        assert len(chans) >= 1, "channel must survive the two-step upgrade"
        send_invoice_payment_with_timeout(self, old_a, old_b, 1)
        send_invoice_payment_with_timeout(self, old_b, old_a, 1)
