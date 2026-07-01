import pytest
import requests

from framework.fiber_rpc import FiberRPCClient


def test_rpc_timeout_fails_with_method_and_url(monkeypatch):
    calls = []

    def fake_post(url, data, headers, timeout):
        calls.append((url, timeout))
        raise requests.exceptions.Timeout("slow rpc")

    monkeypatch.setattr(requests, "post", fake_post)

    client = FiberRPCClient("http://127.0.0.1:8228", try_count=3, timeout=2)

    with pytest.raises(Exception) as exc_info:
        client.call("send_payment", [{}])

    assert "RPC request timed out" in str(exc_info.value)
    assert "method=send_payment" in str(exc_info.value)
    assert "url=http://127.0.0.1:8228" in str(exc_info.value)
    assert calls == [("http://127.0.0.1:8228", 2)]
