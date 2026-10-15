"""Keep Legacy attack cases on an old-old channel until their nodes restart."""

from io import BytesIO
from types import SimpleNamespace

from framework.attack_fnn import LEGACY_COUNTERPARTY_ENV
from framework.basic_p2p import P2pFiberTest
from framework.test_fiber import FiberConfigPath
from test_cases.fiber.devnet.compatibility import test_full_hash_bad_preimage as cases
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
