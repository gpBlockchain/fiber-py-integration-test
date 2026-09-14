"""Shared marker and path for tests that need the instrumented FNN."""

import os

import pytest

from framework.util import get_project_root

ATTACK_FNN = os.path.join(get_project_root(), "download/fiber/attack/fnn")


def requires_attack_fnn(test_item):
    """Select the test for attack-FNN CI and skip when its binary is absent."""
    test_item = pytest.mark.requires_attack_fnn(test_item)
    return pytest.mark.skipif(
        not (os.path.isfile(ATTACK_FNN) and os.access(ATTACK_FNN, os.X_OK)),
        reason=f"executable attack fnn not found at {ATTACK_FNN}",
    )(test_item)
