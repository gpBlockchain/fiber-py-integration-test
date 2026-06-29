"""PR #1323 archive sanity: the legacy `fnn-migrate` binary must NOT be shipped
inside the v0.9.0+ release tarball anymore (the `migrate/` workspace was renamed
to `migrate_archive/` and removed from CI). The v0.8.x release line keeps it.

This test asserts the on-disk layout that download_fiber.py extracts.
"""

import os

import pytest

from framework.util import get_project_root


def _exists(rel: str) -> bool:
    return os.path.isfile(os.path.join(get_project_root(), rel))


class TestLegacyMigrateArchived:
    def test_v081_release_still_ships_fnn_migrate(self):
        if not _exists("download/fiber/0.8.1/fnn"):
            pytest.skip("v0.8.1 not downloaded")
        assert _exists(
            "download/fiber/0.8.1/fnn-migrate"
        ), "v0.8.1 release MUST keep fnn-migrate (used as the bridge tool)"

    def test_current_release_no_longer_ships_fnn_migrate(self):
        if not _exists("download/fiber/current/fnn"):
            pytest.skip("current binary not downloaded")
        # PR #1323 archives the standalone tool. If a future release brings it
        # back, this test will flip to a green dot once the assertion is
        # inverted -- but for now its absence is part of the contract.
        assert not _exists("download/fiber/current/fnn-migrate"), (
            "PR #1323 removed standalone fnn-migrate from the new release; "
            "found it again under download/fiber/current/. "
            "If this is intentional, update this assertion."
        )
