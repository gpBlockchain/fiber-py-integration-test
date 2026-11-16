"""Helpers pick the exact cached commitment and restore the clock safely."""

import time
from unittest import mock

import pytest

import framework.attack_commitment as ac
from framework.basic_fiber import FiberTest


def test_submit_newest_waits_for_post_tlc_commitment(monkeypatch):
    monkeypatch.setattr(ac.time, "sleep", lambda _: None)
    client = mock.Mock()
    s0 = {"commitment_number": "0x1", "tx_hash": "s0"}
    s1 = {"commitment_number": "0x2", "tx_hash": "s1"}
    client.call.side_effect = [
        {"transactions": [s0]},
        {"transactions": [s1, s0]},
        {"tx_hash": "s1"},
    ]
    assert ac.submit_newest_commitment(client, "ch", 1) == {"tx_hash": "s1"}
    client.call.assert_called_with(
        "submit_commitment_transaction",
        [{"channel_id": "ch", "commitment_number": "0x2"}],
    )


def test_submit_newest_rejects_mismatched_tx():
    client = mock.Mock()
    client.call.side_effect = [
        {"transactions": [{"commitment_number": "0x2", "tx_hash": "s1"}]},
        {"tx_hash": "other"},
    ]
    with pytest.raises(AssertionError):
        ac.submit_newest_commitment(client, "ch", 1)


def test_latest_commitment_number_is_max():
    client = mock.Mock()
    client.call.return_value = {
        "transactions": [
            {"commitment_number": "0x3", "tx_hash": "a"},
            {"commitment_number": "0x1", "tx_hash": "b"},
        ]
    }
    assert ac.latest_commitment_number(client, "ch") == 3


def test_restore_time_returns_to_origin_plus_elapsed():
    class Case(FiberTest):
        pass

    # Pretend the clock was advanced 9 days, 60 real seconds ago.
    advanced = 9 * 24 * 3600
    Case._clock_origin = (time.time() - advanced - 60, time.monotonic() - 60)
    calls = []
    with mock.patch(
        "framework.util.change_time",
        lambda hour, minutes=0: calls.append((hour, minutes)) or True,
    ):
        Case.restore_time()
    assert Case._clock_origin is None
    ((hour, minutes),) = calls
    assert hour == 0
    assert abs(minutes - (-advanced / 60)) < 1, minutes
