from unittest.mock import Mock

import pytest

from framework import basic_fiber
from test_cases.fiber.devnet.node_info import test_node_info


@pytest.fixture
def clock(monkeypatch):
    sleeps = []
    monkeypatch.setattr(basic_fiber.time, "monotonic", lambda: sum(sleeps))
    monkeypatch.setattr(basic_fiber.time, "sleep", sleeps.append)
    return sleeps


def test_counts_wait_for_delayed_actor_removal(clock):
    client = Mock()
    client.node_info.side_effect = [
        {"channel_count": "0x1"},
        {"channel_count": "0x1"},
        {"channel_count": "0x0"},
    ]
    result = basic_fiber.FiberTest().wait_for_node_info_counts(client, 0)
    assert result == {"channel_count": "0x0"}
    assert clock == [1, 1]


def test_counts_match_pending_and_actor_count_in_same_snapshot(clock):
    client = Mock()
    client.node_info.side_effect = [
        {"channel_count": "0xa", "pending_channel_count": "0x0"},
        {"channel_count": "0x9", "pending_channel_count": "0x1"},
        {"channel_count": "0xa", "pending_channel_count": "0x1"},
    ]
    result = basic_fiber.FiberTest().wait_for_node_info_counts(client, 10, 1)
    assert result == {"channel_count": "0xa", "pending_channel_count": "0x1"}
    assert clock == [1, 1]


def test_counts_return_immediately_when_matched(clock):
    client = Mock()
    client.node_info.return_value = {"channel_count": "0x0"}
    basic_fiber.FiberTest().wait_for_node_info_counts(client, 0, timeout=0)
    assert client.node_info.call_count == 1
    assert clock == []


def test_counts_timeout_reports_last_snapshot(clock):
    client = Mock()
    client.node_info.return_value = {"channel_count": "0x1"}
    with pytest.raises(TimeoutError, match="last node_info.*0x1"):
        basic_fiber.FiberTest().wait_for_node_info_counts(client, 0, timeout=2)
    assert client.node_info.call_count == 3
    assert clock == [1, 1]


def test_counts_propagate_rpc_failure(clock):
    client = Mock()
    client.node_info.side_effect = RuntimeError("rpc failed")
    with pytest.raises(RuntimeError, match="rpc failed"):
        basic_fiber.FiberTest().wait_for_node_info_counts(client, 0)
    assert clock == []


def test_channel_count_uses_each_peers_baseline():
    case = test_node_info.TestNodeInfo()
    case.fiber1 = Mock()
    case.fiber2 = Mock()
    case.node = Mock()
    client1 = case.fiber1.get_client.return_value
    client2 = case.fiber2.get_client.return_value
    client1.node_info.return_value = {
        "channel_count": "0x2",
        "pending_channel_count": "0x0",
    }
    client2.node_info.return_value = {
        "channel_count": "0x5",
        "pending_channel_count": "0x1",
    }
    case.wait_for_node_info_counts = Mock()
    case.wait_for_channel_state = Mock(return_value="channel-id")
    case.get_account_script = Mock(return_value={})

    case.test_channel_count()

    assert [
        (
            call.args[0],
            call.kwargs["channel_count"],
            call.kwargs["pending_channel_count"],
        )
        for call in case.wait_for_node_info_counts.call_args_list
    ] == [
        (client1, 3, 1),
        (client2, 6, 2),
        (client1, 3, 0),
        (client2, 6, 1),
        (client1, 2, 0),
        (client2, 5, 1),
    ]
    case.node.stop_miner.assert_called_once()
    case.node.start_miner.assert_called_once()


def test_channel_count_restores_mining_after_pending_timeout():
    case = test_node_info.TestNodeInfo()
    case.fiber1 = Mock()
    case.fiber2 = Mock()
    case.node = Mock()
    for fiber in (case.fiber1, case.fiber2):
        fiber.get_client.return_value.node_info.return_value = {
            "channel_count": "0x0",
            "pending_channel_count": "0x0",
        }
    case.wait_for_node_info_counts = Mock(side_effect=TimeoutError("pending"))

    with pytest.raises(TimeoutError, match="pending"):
        case.test_channel_count()

    case.node.stop_miner.assert_called_once()
    case.node.start_miner.assert_called_once()
