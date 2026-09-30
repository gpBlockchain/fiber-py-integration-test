"""Shared markers and paths for tests that need an instrumented FNN."""

import os

import pytest

from framework.util import get_project_root

ATTACK_FNN = os.path.join(get_project_root(), "download/fiber/attack/fnn")

# p2p-tap debug RPCs adapted onto fix/commitment-lock-full-payment-hash
# (8b95af3). Unlike the legacy attacker this build understands the V1
# commitment layout and its watchtower can emit a prefix-only preimage claim.
ATTACK_FULL_HASH_FNN = os.path.join(
    get_project_root(), "download/fiber/attack-full-payment-hash/fnn"
)

# Counterparty switches read by that build at start / settlement time.
DISABLE_FULL_HASH_FEATURE_ENV = "FIBER_TEST_DISABLE_FULL_HASH_FEATURE"
ALLOW_FULL_HASH_MISMATCH_ENV = "FIBER_TEST_ALLOW_FULL_HASH_MISMATCH"
# FBR-2026-0060: when set (e.g. "sha256"), the node writes this algorithm into
# the inner trampoline hop payload while the outer payment session keeps its
# own, letting a malicious sender desynchronize the two TLC hash algorithms.
TRAMPOLINE_INNER_HASH_ALGORITHM_ENV = "FIBER_TEST_TRAMPOLINE_INNER_HASH_ALGORITHM"

LEGACY_COUNTERPARTY_ENV = {DISABLE_FULL_HASH_FEATURE_ENV: "1"}
V1_PREFIX_CLAIM_ENV = {ALLOW_FULL_HASH_MISMATCH_ENV: "1"}
TRAMPOLINE_INNER_SHA256_ENV = {TRAMPOLINE_INNER_HASH_ALGORITHM_ENV: "sha256"}


def requires_attack_fnn(test_item):
    """Select the test for attack-FNN CI and skip when its binary is absent."""
    test_item = pytest.mark.requires_attack_fnn(test_item)
    return pytest.mark.skipif(
        not (os.path.isfile(ATTACK_FNN) and os.access(ATTACK_FNN, os.X_OK)),
        reason=f"executable attack fnn not found at {ATTACK_FNN}",
    )(test_item)


def requires_full_hash_attack_fnn(test_item):
    """Select full-payment-hash counterparty tests; skip when the build is absent."""
    test_item = pytest.mark.requires_attack_fnn(test_item)
    return pytest.mark.skipif(
        not (
            os.path.isfile(ATTACK_FULL_HASH_FNN)
            and os.access(ATTACK_FULL_HASH_FNN, os.X_OK)
        ),
        reason=f"executable full-hash attack fnn not found at {ATTACK_FULL_HASH_FNN}",
    )(test_item)
