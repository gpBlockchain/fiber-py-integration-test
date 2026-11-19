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


def test_restore_time_without_cluster_clock_is_disabled():
    class Case(FiberTest):
        cluster_clock = None
        _clock_origin = (1.0, 2.0)

    with pytest.raises(RuntimeError, match="Host system clock restore is disabled"):
        Case.restore_time()
