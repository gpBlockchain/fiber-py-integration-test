"""Offline checks for bounded payment Failed polling."""

import pytest

from framework.helper.payment_poll import wait_until_any_payment_failed


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def test_returns_when_one_side_fails_after_inflight():
    clock = FakeClock()
    states = [
        {
            "fiber1": {"status": "Inflight", "id": 1},
            "fiber3": {"status": "Inflight", "id": 3},
        },
        {
            "fiber1": {"status": "Inflight", "id": 1},
            "fiber3": {"status": "Failed", "id": 3},
        },
    ]

    def reader(name):
        def _read():
            return states[0][name] if len(states) > 1 else states[0][name]

        return _read

    # Advance the snapshot after the first full poll by wrapping readers.
    poll_count = {"n": 0}

    def make_reader(name):
        def _read():
            index = 0 if poll_count["n"] < 2 else 1
            # increment after both readers in a round: fiber1 then fiber3
            result = states[index][name]
            poll_count["n"] += 1
            return result

        return _read

    result = wait_until_any_payment_failed(
        {"fiber1": make_reader("fiber1"), "fiber3": make_reader("fiber3")},
        timeout=5,
        interval=1,
        clock=clock.time,
        sleep=clock.sleep,
    )
    assert result["fiber3"]["status"] == "Failed"
    assert result["fiber1"]["status"] == "Inflight"
    assert clock.sleeps == [1]


def test_timeout_prints_both_payment_details_and_does_not_pass():
    clock = FakeClock()

    def inflight(tag):
        return lambda: {"status": "Inflight", "payment_hash": tag, "fee": "0x1"}

    with pytest.raises(AssertionError, match="timed out after 2") as exc:
        wait_until_any_payment_failed(
            {"fiber1": inflight("p1"), "fiber3": inflight("p3")},
            timeout=2,
            interval=1,
            clock=clock.time,
            sleep=clock.sleep,
        )
    message = str(exc.value)
    assert "Inflight" in message
    assert "p1" in message and "p3" in message
    assert clock.now >= 2


def test_reader_exception_propagates_without_retrying_payment():
    clock = FakeClock()
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise RuntimeError("rpc down")

    with pytest.raises(RuntimeError, match="rpc down"):
        wait_until_any_payment_failed(
            {"fiber1": boom, "fiber3": lambda: {"status": "Inflight"}},
            timeout=5,
            interval=1,
            clock=clock.time,
            sleep=clock.sleep,
        )
    assert calls["n"] == 1
    assert clock.sleeps == []


def test_immediate_failed_does_not_sleep():
    clock = FakeClock()
    result = wait_until_any_payment_failed(
        {
            "fiber1": lambda: {"status": "Failed", "reason": "cost"},
            "fiber3": lambda: {"status": "Inflight"},
        },
        timeout=5,
        interval=1,
        clock=clock.time,
        sleep=clock.sleep,
    )
    assert result["fiber1"]["status"] == "Failed"
    assert clock.sleeps == []


def test_success_and_inflight_is_not_enough_and_times_out():
    clock = FakeClock()
    with pytest.raises(AssertionError, match="timed out"):
        wait_until_any_payment_failed(
            {
                "fiber1": lambda: {"status": "Success"},
                "fiber3": lambda: {"status": "Inflight"},
            },
            timeout=1,
            interval=1,
            clock=clock.time,
            sleep=clock.sleep,
        )
