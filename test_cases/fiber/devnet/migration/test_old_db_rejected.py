"""PR #1323 regression: starting current fnn against an old (< INIT_DB_VERSION)
database must fail fast with a clear `DatabaseTooOld` style message instead of
silently corrupting the store or hanging.

We use a v0.7.0 database, since its highest db-version is well below
INIT_DB_VERSION = 20260302100001.
"""

import pytest

from framework.test_fiber import FiberConfigPath

from ._helpers import (
    INIT_DB_VERSION,
    fiber_bin_exists,
    MigrationFiberTest,
    open_v070_channel,
    read_node_log,
    start_blocking,
    wait_channels_ready,
    wait_peer_connected,
)

pytestmark = pytest.mark.skipif(
    not fiber_bin_exists("download/fiber/0.7.0/fnn"),
    reason="v0.7.0 binary not downloaded (run download_fiber.py first)",
)


class TestOldDbRejected(MigrationFiberTest):
    def test_v070_db_directly_against_current_is_rejected(self):
        # 1. produce a real v0.7.0 store
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
        old_a.stop()
        old_b.stop()

        # 2. switch to current binary WITHOUT running v0.8.x fnn-migrate
        old_a.fiber_config_enum = FiberConfigPath.CURRENT_DEV

        # 3. starting must fail. We pipe "y" so that, if for some reason the
        #    binary still asks for confirmation, we are not the cause of the
        #    failure. The expected exit comes from MigrateError::DatabaseTooOld.
        exit_code, output = start_blocking(old_a, confirm="y", timeout=60)
        assert exit_code != 0, "fnn must NOT start against a pre-INIT_DB_VERSION DB"

        log = output + "\n" + read_node_log(old_a)
        # message format from MigrateError::DatabaseTooOld
        assert (
            "too old" in log.lower()
            or INIT_DB_VERSION in log
            or "fnn-migrate" in log.lower()
        ), f"expected DatabaseTooOld diagnostic, got log:\n{log[-2000:]}"

        # the user-facing hint should point at the correct legacy tool version
        # (design says v0.7.x, current code says v0.8.x -- this assertion
        # locks the contract; flip the literal if the project chooses one).
        assert (
            "v0.7" in log or "v0.8" in log
        ), "DatabaseTooOld must point users at the legacy fnn-migrate version"
