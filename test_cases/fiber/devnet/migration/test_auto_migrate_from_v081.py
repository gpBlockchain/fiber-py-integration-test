"""PR #1323/#1355 regression: auto-migrate from v0.8.1 to current.

Scenario:
  1. Start a pair of v0.8.1 fnn nodes, open a channel, exchange payments so the
     RocksDB store contains real ChannelActorData (old layout).
  2. Stop both nodes.
  3. Switch the binary to current WITHOUT running the old
     standalone `fnn-migrate` tool.
  4. Start each node again. The new auto-migration must:
       - present a confirmation prompt on stderr (we pipe "y"),
       - run the latest channel actor data migration against the existing
         ChannelActorData entries,
       - leave db-version at LATEST_DB_VERSION_AFTER_PR1323.
  5. After both nodes are up, the channel must still be alive and a fresh
     payment must succeed in both directions.
"""

import pytest

from framework.test_fiber import FiberConfigPath

from ._helpers import (
    LATEST_DB_VERSION_AFTER_PR1323,
    assert_log_matches,
    fiber_bin_exists,
    list_channels_with_timeout,
    MigrationFiberTest,
    send_invoice_payment_with_timeout,
    start_with_confirm,
    wait_log_matches,
)

pytestmark = pytest.mark.skipif(
    not fiber_bin_exists("download/fiber/0.8.1/fnn"),
    reason="v0.8.1 binary not downloaded (run download_fiber.py first)",
)


class TestAutoMigrateFromV081(MigrationFiberTest):
    def test_auto_migrate_v081_to_current(self):
        # 1. start two v0.8.1 nodes
        old_a = self.start_new_fiber(
            self.generate_account(10000), fiber_version=FiberConfigPath.V081_DEV
        )
        old_b = self.start_new_fiber(
            self.generate_account(10000), fiber_version=FiberConfigPath.V081_DEV
        )

        # 2. open a public channel and exercise it so ChannelActorData is on disk
        self.open_channel(old_a, old_b, 1000 * 100000000, 0)
        self.send_invoice_payment(old_a, old_b, 1)
        self.send_invoice_payment(old_b, old_a, 1)

        old_channel_count = len(old_a.get_client().list_channels({})["channels"])
        assert old_channel_count >= 1

        old_a.stop()
        old_b.stop()

        # 3. switch binary to current WITHOUT running fnn-migrate
        old_a.fiber_config_enum = FiberConfigPath.CURRENT_DEV
        old_b.fiber_config_enum = FiberConfigPath.CURRENT_DEV

        # 4. start with auto-confirm; auto-migration must succeed
        start_with_confirm(old_a, confirm="y")
        start_with_confirm(old_b, confirm="y")

        # the migration plan + step messages should have been logged
        wait_log_matches(old_a, r"Database migration required")
        wait_log_matches(
            old_a, r"Migrating to {}".format(LATEST_DB_VERSION_AFTER_PR1323)
        )
        assert_log_matches(
            old_a, r"Migration {} complete".format(LATEST_DB_VERSION_AFTER_PR1323)
        )
        assert_log_matches(old_a, r"connectivity_state and external_funding")

        # 5. channel survived the migration and is still usable via RPC
        chans = list_channels_with_timeout(old_a)
        assert len(chans) == old_channel_count, "channel must survive migration"
        assert all(c["state"]["state_name"] == "ChannelReady" for c in chans), chans

        # 6. fresh payments must work in both directions
        send_invoice_payment_with_timeout(self, old_a, old_b, 1)
        send_invoice_payment_with_timeout(self, old_b, old_a, 1)

        # 7. starting again should NOT trigger another migration
        old_a.stop()
        start_with_confirm(old_a, confirm="y")
        log = open(f"{old_a.tmp_path}/node.log").read()
        # after a clean second startup we expect the "is current, no migration
        # needed" branch (or at least no new "Migrating to" line for the same
        # version a second time)
        assert (
            log.count("Migrating to {}".format(LATEST_DB_VERSION_AFTER_PR1323)) == 1
        ), "auto-migration must be idempotent on second startup"
