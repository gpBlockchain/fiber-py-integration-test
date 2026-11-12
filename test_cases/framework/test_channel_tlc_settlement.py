"""Framework-only checks for initial channel liquidity and settlement waits."""

from unittest.mock import Mock, call

import pytest

from framework import basic_fiber


@pytest.fixture
def clock(monkeypatch):
    sleeps = []
    monkeypatch.setattr(basic_fiber.time, "monotonic", lambda: sum(sleeps))
    monkeypatch.setattr(basic_fiber.time, "sleep", sleeps.append)
    return sleeps


def channel(channel_id="target", offered=0, received=0, state="ChannelReady"):
    return {
        "channel_id": channel_id,
        "state": {"state_name": state},
        "offered_tlc_balance": hex(offered),
        "received_tlc_balance": hex(received),
    }


def test_waits_for_both_directions_on_the_requested_channel(clock):
    fiber = Mock()
    client = fiber.get_client.return_value
    settled = channel()
    client.list_channels.side_effect = [
        {"channels": [channel("other"), channel(offered=100)]},
        {"channels": [channel(received=100)]},
        {"channels": [channel("other", offered=50), settled]},
    ]
    result = basic_fiber.FiberTest().wait_channel_tlcs_settled(fiber, "target")
    assert result == settled
    assert clock == [1, 1]
    client.send_payment.assert_not_called()


@pytest.mark.parametrize(
    "channels", [[], [channel(offered=1)], [channel(state="Closed")]]
)
def test_timeout_reports_unsettled_or_missing_channel(clock, channels):
    fiber = Mock()
    fiber.get_client.return_value.list_channels.return_value = {"channels": channels}
    with pytest.raises(TimeoutError, match="Channel target TLCs did not settle"):
        basic_fiber.FiberTest().wait_channel_tlcs_settled(fiber, "target", timeout=1.5)
    assert clock == [1, 0.5]


def test_query_errors_propagate(clock):
    fiber = Mock()
    fiber.get_client.return_value.list_channels.side_effect = RuntimeError("RPC failed")
    with pytest.raises(RuntimeError, match="RPC failed"):
        basic_fiber.FiberTest().wait_channel_tlcs_settled(fiber, "target")
    assert clock == []


@pytest.mark.parametrize("udt", [None, {"args": "udt"}])
@pytest.mark.parametrize("peer_balance", [0, 1000])
def test_open_channel_waits_after_initial_balance_transfer(udt, peer_balance):
    case = basic_fiber.FiberTest()
    sender, receiver = Mock(), Mock()
    sender.get_client.return_value.list_channels.return_value = {"channels": []}
    receiver.get_client.return_value.node_info.return_value = {
        "open_channel_auto_accept_min_ckb_funding_amount": "0x0"
    }
    case.wait_for_new_channel_state = Mock(return_value="target")
    case.send_payment = Mock()
    case.wait_channel_tlcs_settled = Mock()
    sequence = Mock()
    sequence.attach_mock(case.send_payment, "payment")
    sequence.attach_mock(case.wait_channel_tlcs_settled, "settled")

    assert case.open_channel(sender, receiver, 2000, peer_balance, udt=udt) == "target"
    assert sequence.mock_calls == (
        [
            call.payment(sender, receiver, peer_balance, True, udt, 10),
            call.settled(sender, "target"),
            call.settled(receiver, "target"),
        ]
        if peer_balance
        else []
    )
