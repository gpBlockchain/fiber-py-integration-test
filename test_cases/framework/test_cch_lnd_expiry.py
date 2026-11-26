"""Offline checks for receive_btc LND expiry bounds."""

import pytest

from framework.helper.cch_lnd_expiry import (
    assert_receive_btc_lnd_expiry,
    default_fiber_remaining_seconds,
    parse_order_created_at,
    parse_u64,
    relative_expiry_bounds,
)

DELTA = 10
MIN_OUTGOING = 5


def test_parse_u64_accepts_int_decimal_and_hex():
    assert parse_u64(10) == 10
    assert parse_u64("10") == 10
    assert parse_u64("0xa") == 10
    assert parse_order_created_at({"timestamp": "0x6b0ce846"}) == 0x6B0CE846
    assert parse_order_created_at({}) is None
    assert parse_order_created_at({"timestamp": None}) is None


def test_parse_u64_rejects_invalid_values():
    with pytest.raises(ValueError):
        parse_u64(-1)
    with pytest.raises(ValueError):
        parse_u64(True)
    with pytest.raises(ValueError):
        parse_u64("nope")


def test_default_fiber_remaining_is_twice_min_outgoing():
    assert default_fiber_remaining_seconds(MIN_OUTGOING) == 10


def test_same_second_window_requires_exact_configured_expiry():
    lower, upper = relative_expiry_bounds(DELTA, MIN_OUTGOING, 1000.1, 1000.9)
    assert (lower, upper) == (10, 10)


def test_crossing_a_second_boundary_allows_nine_and_ten():
    lower, upper = relative_expiry_bounds(DELTA, MIN_OUTGOING, 1000.9, 1001.1)
    assert (lower, upper) == (9, 10)
    created_at = 1794799622
    assert_receive_btc_lnd_expiry(
        {"timestamp": "1794799623", "expiry": "9"},
        order_expiry_delta_seconds=DELTA,
        min_outgoing_invoice_expiry_delta_seconds=MIN_OUTGOING,
        call_started_at=1000.9,
        call_finished_at=1001.1,
        order_created_at=created_at,
    )
    assert_receive_btc_lnd_expiry(
        {"timestamp": created_at, "expiry": "10"},
        order_expiry_delta_seconds=DELTA,
        min_outgoing_invoice_expiry_delta_seconds=MIN_OUTGOING,
        call_started_at=1000.9,
        call_finished_at=1001.1,
        order_created_at=created_at,
    )


def test_rejects_loose_positive_values_inside_delta():
    # 5 is the product minimum, but a same-second build must still be 10.
    with pytest.raises(AssertionError, match="outside the product window"):
        assert_receive_btc_lnd_expiry(
            {"timestamp": 1000, "expiry": 5},
            order_expiry_delta_seconds=DELTA,
            min_outgoing_invoice_expiry_delta_seconds=MIN_OUTGOING,
            call_started_at=1000.0,
            call_finished_at=1000.2,
        )


@pytest.mark.parametrize("expiry", [0, 1, 4, 8, 11])
def test_rejects_expiry_outside_one_second_floor_window(expiry):
    with pytest.raises(AssertionError):
        assert_receive_btc_lnd_expiry(
            {"timestamp": 1000, "expiry": expiry},
            order_expiry_delta_seconds=DELTA,
            min_outgoing_invoice_expiry_delta_seconds=MIN_OUTGOING,
            call_started_at=1000.9,
            call_finished_at=1001.1,
        )


def test_absolute_expiry_must_track_fiber_deadline():
    created_at = 1794799613
    assert_receive_btc_lnd_expiry(
        {"timestamp": created_at, "expiry": "10"},
        order_expiry_delta_seconds=DELTA,
        min_outgoing_invoice_expiry_delta_seconds=MIN_OUTGOING,
        call_started_at=created_at,
        call_finished_at=created_at + 0.2,
        order_created_at=created_at,
    )
    # 1s LND/Fiber skew is accepted.
    assert_receive_btc_lnd_expiry(
        {"timestamp": created_at + 1, "expiry": "9"},
        order_expiry_delta_seconds=DELTA,
        min_outgoing_invoice_expiry_delta_seconds=MIN_OUTGOING,
        call_started_at=created_at + 0.9,
        call_finished_at=created_at + 1.1,
        order_created_at=created_at,
    )
    # 3s away from the Fiber deadline is not a flooring artifact.
    with pytest.raises(AssertionError, match="Fiber deadline"):
        assert_receive_btc_lnd_expiry(
            {"timestamp": created_at - 3, "expiry": "10"},
            order_expiry_delta_seconds=DELTA,
            min_outgoing_invoice_expiry_delta_seconds=MIN_OUTGOING,
            call_started_at=created_at,
            call_finished_at=created_at + 0.2,
            order_created_at=created_at,
        )


def test_long_call_window_clamps_lower_bound_to_min_outgoing():
    lower, upper = relative_expiry_bounds(DELTA, MIN_OUTGOING, 1000.0, 1006.2)
    assert (lower, upper) == (5, 10)


def test_ci_repro_second_invoice_expiry_nine():
    # Captured from PR112 CI: first invoice expiry 10, second 9 after a 1s gap.
    lnd_timestamp = 1794799623
    order_created_at = lnd_timestamp - 1
    assert_receive_btc_lnd_expiry(
        {"timestamp": "1794799623", "expiry": "9"},
        order_expiry_delta_seconds=DELTA,
        min_outgoing_invoice_expiry_delta_seconds=MIN_OUTGOING,
        call_started_at=lnd_timestamp - 0.2,
        call_finished_at=lnd_timestamp + 0.2,
        order_created_at=order_created_at,
    )
