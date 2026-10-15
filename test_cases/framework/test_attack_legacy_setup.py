"""Keep Legacy attack cases on an old-old channel until their nodes restart."""

from io import BytesIO
from types import SimpleNamespace

import pytest

from framework.attack_fnn import LEGACY_COUNTERPARTY_ENV
from framework.basic_p2p import P2pFiberTest
from framework.test_fiber import FiberConfigPath
from test_cases.fiber.devnet.compatibility import test_full_hash_bad_preimage as cases
from test_cases.fiber.devnet.compatibility import (
    test_full_hash_empty_snapshot as empty_cases,
)
from test_cases.fiber.devnet.migration import _helpers


def test_legacy_attack_setup_opens_old_old_before_upgrading(monkeypatch):
    events = []

    class Node:
        def __init__(self, name):
            self.name = name
            self.extra_env = {}

        def stop(self):
            events.append((self.name, "stop"))

        def connect_peer(self, peer):
            events.append((self.name, "connect", peer.name))

    def old_old_channel(test, _method):
        assert test.fiber_version is FiberConfigPath.V091_DEV
        assert test.attacker_fiber_version is FiberConfigPath.V091_DEV
        assert test.attacker_env is None
        test.victim = Node("victim")
        test.attacker = Node("attacker")
        test.channel_id = "original-channel"
        events.append(("old-old", "ready"))

    def restart(node, **_kwargs):
        events.append((node.name, "restart", node.fiber_config_enum, node.extra_env))

    channel = {
        "channel_id": "original-channel",
        "channel_outpoint": "funding-cell",
        "local_balance": "0x10",
        "remote_balance": "0x0",
        "latest_commitment_transaction_hash": "commitment",
    }
    monkeypatch.setattr(P2pFiberTest, "setup_method", old_old_channel)
    monkeypatch.setattr(cases, "start_with_confirm", restart)
    monkeypatch.setattr(cases.TestFullHashBadPreimage, "_channel", lambda *_: channel)
    monkeypatch.setattr(
        cases.TestFullHashBadPreimage,
        "_channel_ready",
        lambda test, channel_id: events.append(("ready", channel_id)),
    )

    test = cases.TestFullHashBadPreimage()
    test.setup_method(
        cases.TestFullHashBadPreimage.test_direct_payer_fails_confirmed_prefix_only_claim
    )

    assert events == [
        ("old-old", "ready"),
        ("victim", "stop"),
        ("attacker", "stop"),
        (
            "attacker",
            "restart",
            FiberConfigPath.ATTACK_FULL_HASH_DEV,
            LEGACY_COUNTERPARTY_ENV,
        ),
        ("victim", "restart", FiberConfigPath.CURRENT_DEV, {}),
        ("victim", "connect", "attacker"),
        ("ready", "original-channel"),
    ]


def test_migration_restart_preserves_node_environment(monkeypatch, tmp_path):
    spawned = {}

    class Process:
        stdin = BytesIO()

    class Socket:
        def __init__(self, *_args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def settimeout(self, _timeout):
            pass

        def connect_ex(self, _address):
            return 0

    def popen(_cmd, **kwargs):
        spawned.update(kwargs)
        return Process()

    monkeypatch.setattr(_helpers.subprocess, "Popen", popen)
    monkeypatch.setattr(_helpers.socket, "socket", Socket)
    node = SimpleNamespace(
        fiber_config_enum=FiberConfigPath.ATTACK_FULL_HASH_DEV,
        tmp_path=str(tmp_path),
        rpc_port="24300",
        extra_env=LEGACY_COUNTERPARTY_ENV,
    )

    _helpers.start_with_confirm(node)

    assert spawned["env"]["FIBER_TEST_DISABLE_FULL_HASH_FEATURE"] == "1"


def test_failed_legacy_setup_releases_nodes(monkeypatch):
    events = []

    class Node:
        def __init__(self, name):
            self.name = name

        def force_stop(self):
            events.append((self.name, "stop"))

        def clean(self):
            events.append((self.name, "clean"))

    def fail_setup(test, _method):
        test.fibers = [Node("victim"), Node("attacker")]
        raise TimeoutError("channel did not become ready")

    monkeypatch.setattr(cases.TestFullHashBadPreimage, "_setup_method", fail_setup)
    test = cases.TestFullHashBadPreimage()

    with pytest.raises(TimeoutError, match="channel did not become ready"):
        test.setup_method(
            cases.TestFullHashBadPreimage.test_direct_payer_fails_confirmed_prefix_only_claim
        )

    assert events == [
        ("attacker", "stop"),
        ("attacker", "clean"),
        ("victim", "stop"),
        ("victim", "clean"),
    ]


def test_empty_snapshot_legacy_opens_old_old_before_upgrade(monkeypatch):
    events = []

    class Node:
        def __init__(self, name):
            self.name = name
            self.extra_env = {}

        def stop(self):
            events.append((self.name, "stop"))

        def connect_peer(self, peer):
            events.append((self.name, "connect", peer.name))

    def old_old_channel(test, _method):
        assert test.fiber_version is FiberConfigPath.V091_DEV
        assert test.attacker_fiber_version is FiberConfigPath.V091_DEV
        test.victim = Node("victim")
        test.attacker = Node("attacker")
        test.channel_id = "original-channel"
        events.append(("old-old", "ready"))

    channel = {
        "channel_id": "original-channel",
        "channel_outpoint": "funding-cell",
        "local_balance": "0x10",
        "remote_balance": "0x0",
        "latest_commitment_transaction_hash": "commitment",
    }
    monkeypatch.setattr(P2pFiberTest, "setup_method", old_old_channel)
    monkeypatch.setattr(
        empty_cases.TestFullHashEmptySnapshot, "channel_of", lambda *_: channel
    )
    monkeypatch.setattr(
        empty_cases.TestFullHashEmptySnapshot,
        "wait_channel_state",
        lambda test, node, *_args, **_kwargs: events.append((node.name, "ready")),
    )
    monkeypatch.setattr(
        empty_cases,
        "start_with_confirm",
        lambda node, **_kwargs: events.append(
            (node.name, "restart", node.fiber_config_enum, node.extra_env)
        ),
    )

    test = empty_cases.TestFullHashEmptySnapshot()
    test.setup_method(
        empty_cases.TestFullHashEmptySnapshot.test_legacy_empty_snapshot_withdrawn_before_pending_s1
    )

    assert events == [
        ("old-old", "ready"),
        ("victim", "stop"),
        ("attacker", "stop"),
        (
            "attacker",
            "restart",
            FiberConfigPath.ATTACK_FULL_HASH_DEV,
            LEGACY_COUNTERPARTY_ENV,
        ),
        ("victim", "restart", FiberConfigPath.CURRENT_DEV, {}),
        ("victim", "connect", "attacker"),
        ("victim", "ready"),
        ("attacker", "ready"),
    ]
