"""PR #1323 regression: when the user declines the migration confirm prompt,
fnn must abort with `MigrateError::UserCancelled` and must NOT touch the data.
"""

import pytest

from framework.test_fiber import FiberConfigPath

from ._helpers import (
    fiber_bin_exists,
    list_channels_with_timeout,
    MigrationFiberTest,
    start_blocking,
    start_with_confirm,
    wait_channels_ready,
)

pytestmark = pytest.mark.skipif(
    not fiber_bin_exists("download/fiber/0.8.1/fnn"),
    reason="v0.8.1 binary not downloaded (run download_fiber.py first)",
)


class TestUserCancelMigration(MigrationFiberTest):
    def test_decline_migration_aborts_startup(self):
        # 1. produce a v0.8.1 store (db-version == INIT_DB_VERSION)
        old_a = self.start_new_fiber(
            self.generate_account(10000), fiber_version=FiberConfigPath.V081_DEV
        )
        old_b = self.start_new_fiber(
            self.generate_account(10000), fiber_version=FiberConfigPath.V081_DEV
        )
        self.open_channel(old_a, old_b, 1000 * 100000000, 0)
        wait_channels_ready(old_a)
        old_a.stop()
        old_b.stop()

        # 2. switch to current and decline the prompt
        old_a.fiber_config_enum = FiberConfigPath.CURRENT_DEV

        exit_code, output = start_blocking(old_a, confirm="n", timeout=60)
        assert exit_code != 0, "fnn must NOT start when user declines migration"
        assert (
            "cancel" in output.lower() or "usercancelled" in output.lower()
        ), f"expected UserCancelled in output, got:\n{output[-2000:]}"

        # 3. flipping the answer to "y" on a retry must succeed and the channel
        #    data must still be readable (the cancelled run must not have
        #    partially written anything corrupting).
        start_with_confirm(old_a, confirm="y")
        chans = list_channels_with_timeout(old_a)
        assert len(chans) >= 1, "channel must survive a previously cancelled migration"
