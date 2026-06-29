"""PR #1323 sanity check: starting against an artificially "future" db-version
must abort with `MigrateError::DatabaseTooNew` instead of silently downgrading
the user.

We simulate a future db-version by directly writing into RocksDB after stopping
the node. To stay backend-agnostic, we only assert on the user-visible behavior
(non-zero exit + `too new` / "newer" diagnostic), not on the exact RocksDB key
format.

If your CI doesn't ship `python-rocksdb`, this test is automatically skipped.
"""

import pytest

from ._helpers import (
    fiber_store_dir,
    MigrationFiberTest,
    read_node_log,
    start_blocking,
)

rocksdb = pytest.importorskip(
    "rocksdict",
    reason="rocksdict not installed; install with `pip install rocksdict` to run",
)


class TestNewerDbRejected(MigrationFiberTest):
    def test_future_db_version_rejected(self):
        # Start current fnn against a fresh DB, then stop it so we can rewrite
        # db-version to a future value.
        fiber = self.start_new_fiber(self.account1_private_key)
        fiber.stop()

        # write a future db-version into the RocksDB store directly
        future_version = "29991231235959"
        store_path = str(fiber_store_dir(fiber))
        db = rocksdb.Rdict(store_path)
        try:
            db[b"db-version"] = future_version.encode()
        finally:
            db.close()

        exit_code, output = start_blocking(fiber, confirm="y", timeout=60)
        assert exit_code != 0, "fnn must NOT start when db is newer than binary"
        log = output + "\n" + read_node_log(fiber)
        assert (
            "newer" in log.lower() or "too new" in log.lower() or future_version in log
        ), f"expected DatabaseTooNew diagnostic, got:\n{log[-2000:]}"
