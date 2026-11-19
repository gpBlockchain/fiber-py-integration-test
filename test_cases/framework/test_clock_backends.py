"""Offline coverage for bitcoind/LND ClusterClock backends."""

from unittest.mock import Mock

import pytest

from framework.clock_backends import (
    BitcoindMocktimeBackend,
    LndFileOffsetBackend,
    resolve_lnd_file_clock_binaries,
)
from framework.cluster_clock import ClockPropagationError, ClusterClock


@pytest.fixture
def clock(tmp_path):
    library = tmp_path / "libfaketime.so.1"
    library.touch()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("framework.cluster_clock.platform.system", lambda: "Linux")
        instance = ClusterClock(library)
    try:
        yield instance
    finally:
        instance.close()


def test_bitcoind_backend_skips_setmocktime_at_offset_zero(clock):
    node = Mock()
    clock.attach(BitcoindMocktimeBackend(node))
    node.set_mocktime.assert_not_called()


def test_bitcoind_backend_sets_and_verifies_mocktime(clock):
    node = Mock()
    node.get_mocktime_seconds.side_effect = lambda: clock.now_s()
    clock.attach(BitcoindMocktimeBackend(node))
    clock.advance_seconds(3600)
    node.set_mocktime.assert_called_once()
    unix = node.set_mocktime.call_args.args[0]
    assert abs(unix - clock.now_s()) <= 1


def test_bitcoind_backend_raises_when_mocktime_does_not_stick(clock):
    node = Mock()
    node.get_mocktime_seconds.return_value = int(__import__("time").time())
    clock.attach(BitcoindMocktimeBackend(node))
    with pytest.raises(ClockPropagationError, match="setmocktime"):
        clock.advance_seconds(3600)


def test_lnd_backend_accepts_jumped_invoice_timestamp(clock):
    def invoice():
        return {"timestamp": clock.now_s()}

    clock.attach(LndFileOffsetBackend(Mock(), invoice_fn=invoice))
    clock.advance_seconds(3600)


def test_lnd_backend_rejects_official_host_timestamp(clock):
    def invoice():
        return {"timestamp": int(__import__("time").time())}

    clock.attach(LndFileOffsetBackend(Mock(rpc_port=19999), invoice_fn=invoice))
    with pytest.raises(ClockPropagationError, match="Official LND"):
        clock.advance_seconds(3600)


def test_propagate_reports_partial_failure(clock):
    good = Mock()
    good.get_mocktime_seconds.side_effect = lambda: clock.now_s()
    bad = Mock()
    bad.get_mocktime_seconds.return_value = 1
    clock.attach(BitcoindMocktimeBackend(good))
    clock.attach(BitcoindMocktimeBackend(bad))
    with pytest.raises(ClockPropagationError, match="did not propagate"):
        clock.advance_seconds(60)
    good.set_mocktime.assert_called()
    bad.set_mocktime.assert_called()


def test_resolve_lnd_file_clock_binaries_requires_marker(tmp_path, monkeypatch):
    monkeypatch.delenv("FIBER_TEST_LND_FILE_CLOCK_BIN", raising=False)
    monkeypatch.setattr(
        "framework.clock_backends.get_project_root", lambda: str(tmp_path)
    )
    with pytest.raises(FileNotFoundError, match="file-offset-clock"):
        resolve_lnd_file_clock_binaries()
