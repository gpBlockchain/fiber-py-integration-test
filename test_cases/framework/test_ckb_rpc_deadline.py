"""CKB RPC 可选超时不改变默认调用；不启动节点、不访问网络。"""

from types import SimpleNamespace

import pytest
import requests

import framework.rpc as rpc


def test_default_request_keeps_no_timeout(monkeypatch):
    calls = []

    def post(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(json=lambda: {"result": "0x1"})

    monkeypatch.setattr(rpc.requests, "post", post)
    assert rpc.RPCClient("http://localhost").get_tip_block_number() == 1
    assert "timeout" not in calls[0]


def test_request_timeout_uses_remaining_deadline(monkeypatch):
    calls = []

    def post(*args, **kwargs):
        calls.append(kwargs["timeout"])
        return SimpleNamespace(json=lambda: {"result": "0x1"})

    monkeypatch.setattr(rpc.requests, "post", post)
    monkeypatch.setattr(rpc.time, "monotonic", lambda: 100)
    client = rpc.RPCClient("http://localhost", timeout=10, try_count=1)
    client.deadline = 103
    assert client.get_tip_block_number() == 1
    assert calls == [3]


def test_expired_budget_sends_no_request(monkeypatch):
    monkeypatch.setattr(rpc.time, "monotonic", lambda: 100)
    monkeypatch.setattr(
        rpc.requests, "post", lambda *a, **kw: pytest.fail("unexpected HTTP")
    )
    client = rpc.RPCClient("http://localhost", timeout=10, try_count=1)
    client.deadline = 100
    with pytest.raises(TimeoutError, match="deadline exceeded"):
        client.get_tip_block_number()


def test_late_response_does_not_pass_expired_budget(monkeypatch):
    ticks = iter([100, 104])
    monkeypatch.setattr(rpc.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(
        rpc.requests,
        "post",
        lambda *a, **kw: SimpleNamespace(json=lambda: {"result": "0x1"}),
    )
    client = rpc.RPCClient("http://localhost", timeout=10, try_count=1)
    client.deadline = 103
    with pytest.raises(TimeoutError, match="deadline exceeded"):
        client.get_tip_block_number()


@pytest.mark.parametrize("error", [requests.ConnectionError, requests.Timeout])
def test_single_attempt_has_no_hidden_retry_or_sleep(monkeypatch, error):
    calls = []

    def post(*args, **kwargs):
        calls.append(kwargs["timeout"])
        raise error("test transport failure")

    monkeypatch.setattr(rpc.requests, "post", post)
    monkeypatch.setattr(
        rpc.time, "sleep", lambda _: pytest.fail("unexpected retry sleep")
    )
    with pytest.raises(error):
        rpc.RPCClient(
            "http://localhost", timeout=10, try_count=1
        ).get_tip_block_number()
    assert calls == [10]
