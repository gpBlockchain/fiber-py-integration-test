"""Settlement fixtures must submit the exact signed pre-TLC transaction."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from test_cases.fiber.devnet.security import (
    test_stale_commitment_raa_pending_tlc_stuck as cases,
)


def make_case():
    client = Mock()
    case = cases.TestStaleCommitmentRaaPendingTlcStuck()
    case.channel_id = "channel"
    case.attacker = SimpleNamespace(get_client=lambda: client)
    return case, client


def test_snapshot_waits_for_cache_and_selects_latest_existing_transaction(monkeypatch):
    case, client = make_case()
    previous = {"commitment_number": "0x10", "tx_hash": "before-payment"}
    client.call.side_effect = [
        {"transactions": []},
        {"transactions": [previous, {"commitment_number": "0xf", "tx_hash": "older"}]},
    ]
    monkeypatch.setattr(cases.time, "sleep", lambda _: None)
    assert case._snapshot_previous_commitment() == previous
    assert all(
        call.args == ("list_commitment_transactions", [{"channel_id": "channel"}])
        for call in client.call.call_args_list
    )


def test_submit_uses_only_the_exact_snapshot_number():
    case, client = make_case()
    previous = {"commitment_number": "0x10", "tx_hash": "before-payment"}
    client.call.return_value = {"tx_hash": "before-payment"}
    assert case._submit_previous_commitment(previous) == (16, client.call.return_value)
    client.call.assert_called_once_with(
        "submit_commitment_transaction",
        [{"channel_id": "channel", "commitment_number": "0x10"}],
    )


def test_submit_rejects_a_different_transaction_without_retry():
    case, client = make_case()
    client.call.return_value = {"tx_hash": "after-payment"}
    with pytest.raises(AssertionError, match="pre_tlc_commitment"):
        case._submit_previous_commitment(
            {"commitment_number": "0x10", "tx_hash": "before-payment"}
        )
    assert client.call.call_count == 1


def test_submit_error_does_not_try_a_post_tlc_number():
    case, client = make_case()
    client.call.side_effect = RuntimeError("transaction unavailable")
    with pytest.raises(RuntimeError, match="transaction unavailable"):
        case._submit_previous_commitment(
            {"commitment_number": "0x10", "tx_hash": "before-payment"}
        )
    assert client.call.call_count == 1


def test_missing_snapshot_rpc_is_not_treated_as_security_success():
    case, client = make_case()
    client.call.side_effect = RuntimeError("Method not found")
    with pytest.raises(RuntimeError, match="Method not found"):
        case._snapshot_previous_commitment()
    assert client.call.call_count == 1
