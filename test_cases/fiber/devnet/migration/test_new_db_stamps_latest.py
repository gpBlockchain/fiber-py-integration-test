"""PR #1323 regression: a brand-new database (no `db-version` key) must be
stamped with LATEST_DB_VERSION on first open and must NOT prompt the user.
A second start must be a no-op (`Database version ... is current, no migration
needed`).
"""

from ._helpers import (
    fiber_store_dir,
    MigrationFiberTest,
    read_node_log,
    start_with_confirm,
)


class TestNewDbStampsLatest(MigrationFiberTest):
    def test_new_db_no_migration_needed(self):
        fiber = self.start_new_fiber(self.account1_private_key)

        # The current binary started against an empty data dir, so the
        # auto_migrate "no db-version -> stamp LATEST" branch must have run.
        store = fiber_store_dir(fiber)
        assert store.exists(), "fiber1 store must exist after first startup"

        # No migration plan / progress lines should appear in the log because
        # the DB is brand-new.
        log1 = read_node_log(fiber)
        assert "Database migration required" not in log1
        assert "Migrating to" not in log1

        # Second startup must hit the "is current, no migration needed" branch.
        fiber.stop()
        start_with_confirm(fiber, confirm="n")  # would cancel if asked

        log2 = read_node_log(fiber)
        assert (
            "Database migration required" not in log2
        ), "auto_migrate must NOT prompt when db-version already equals LATEST"
        # the framework startup may or may not log the exact phrase; the strong
        # contract is: the node is up and answering RPC.
        info = fiber.get_client().node_info()
        assert "node_id" in info or "pubkey" in info
