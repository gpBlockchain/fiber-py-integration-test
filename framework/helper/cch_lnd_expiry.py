"""Assertions for LND invoice expiry produced by Fiber receive_btc.

Fiber's create_or_recover_lnd_invoice (v0.10.1) computes:

    refreshed_at = now_ms / 1000  # whole seconds
    order_remaining = created_at + order_expiry_delta_seconds - refreshed_at
    fiber_remaining = invoice expiry remaining, or 2 * min_outgoing if unset
    expiry = min(fiber_remaining, order_remaining)

The encoded LND expiry is therefore the configured delta minus any whole
seconds that elapsed between order creation and invoice build, and it is
never allowed to extend past the Fiber order deadline. A same-second build
yields expiry == delta; crossing a second boundary yields delta - 1.

These helpers keep that product meaning: they accept the documented 1s
floor, and reject values that are merely "some positive number <= delta".
"""

from __future__ import annotations

import math
from typing import Mapping, Optional, Tuple, Union

Number = Union[int, str]


def parse_u64(value: Number) -> int:
    """Parse a JSON integer that may be decimal, 0x-hex, or already an int."""
    if isinstance(value, bool) or value is None:
        raise ValueError(f"invalid integer: {value!r}")
    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"expected non-negative integer, got {value}")
        return value
    text = str(value).strip()
    if text.startswith(("0x", "0X")):
        parsed = int(text, 16)
    else:
        parsed = int(text, 10)
    if parsed < 0:
        raise ValueError(f"expected non-negative integer, got {value!r}")
    return parsed


def default_fiber_remaining_seconds(
    min_outgoing_invoice_expiry_delta_seconds: int,
) -> int:
    """CKB invoices with no expiry use twice the configured outgoing minimum."""
    if min_outgoing_invoice_expiry_delta_seconds < 0:
        raise ValueError("min_outgoing_invoice_expiry_delta_seconds must be >= 0")
    return min_outgoing_invoice_expiry_delta_seconds * 2


def relative_expiry_bounds(
    order_expiry_delta_seconds: int,
    min_outgoing_invoice_expiry_delta_seconds: int,
    call_started_at: float,
    call_finished_at: float,
    fiber_remaining_seconds: Optional[int] = None,
) -> Tuple[int, int]:
    """Inclusive [lower, upper] for the LND relative expiry field.

    upper is the product cap: min(order delta, fiber remaining).
    lower subtracts only whole seconds observable in the receive_btc call
    window (floor(t1) - floor(t0)), then is clamped by the product minimum
    that would otherwise reject the invoice.
    """
    if order_expiry_delta_seconds < 0 or min_outgoing_invoice_expiry_delta_seconds < 0:
        raise ValueError("expiry configuration must be >= 0")
    if call_finished_at < call_started_at:
        raise ValueError("call_finished_at precedes call_started_at")

    cap = order_expiry_delta_seconds
    if fiber_remaining_seconds is None:
        cap = min(
            cap,
            default_fiber_remaining_seconds(min_outgoing_invoice_expiry_delta_seconds),
        )
    else:
        if fiber_remaining_seconds < 0:
            raise ValueError("fiber_remaining_seconds must be >= 0")
        cap = min(cap, fiber_remaining_seconds)

    max_age = math.floor(call_finished_at) - math.floor(call_started_at)
    lower = cap - max_age
    lower = max(lower, min_outgoing_invoice_expiry_delta_seconds)
    if lower > cap:
        # The measured window already exhausted the configured lifetime; a
        # successful invoice still cannot be below the product minimum.
        lower = cap
    return lower, cap


def lnd_absolute_expiry(decode: Mapping) -> Tuple[int, int, int]:
    """Return (timestamp, relative expiry, timestamp + expiry) from decodepayreq."""
    timestamp = parse_u64(decode["timestamp"])
    expiry = parse_u64(decode["expiry"])
    return timestamp, expiry, timestamp + expiry


def parse_order_created_at(order: Mapping) -> Optional[int]:
    """CCH RPC field is `timestamp` (seconds since epoch, often 0x-hex)."""
    if "timestamp" not in order or order["timestamp"] is None:
        return None
    return parse_u64(order["timestamp"])


def assert_receive_btc_lnd_expiry(
    decode: Mapping,
    *,
    order_expiry_delta_seconds: int,
    min_outgoing_invoice_expiry_delta_seconds: int,
    call_started_at: float,
    call_finished_at: float,
    order_created_at: Optional[int] = None,
    clock_tolerance_seconds: int = 1,
    fiber_remaining_seconds: Optional[int] = None,
) -> Tuple[int, int, int]:
    """Assert LND expiry matches the configured deadline, allowing 1s flooring.

    Relative expiry must sit in the call-window bounds derived from the
    product formula. Absolute expiry (LND timestamp + expiry) must stay
    within one second of the Fiber order deadline when created_at is known,
    covering second flooring and a 1s LND/Fiber clock difference. The
    1-second tolerance is not a license to accept any value in (0, delta].
    """
    if clock_tolerance_seconds < 0:
        raise ValueError("clock_tolerance_seconds must be >= 0")

    timestamp, expiry, absolute = lnd_absolute_expiry(decode)
    lower, upper = relative_expiry_bounds(
        order_expiry_delta_seconds,
        min_outgoing_invoice_expiry_delta_seconds,
        call_started_at,
        call_finished_at,
        fiber_remaining_seconds=fiber_remaining_seconds,
    )
    assert lower <= expiry <= upper, (
        f"LND invoice expiry {expiry} is outside the product window "
        f"[{lower}, {upper}] (delta={order_expiry_delta_seconds}, "
        f"min_outgoing={min_outgoing_invoice_expiry_delta_seconds}, "
        f"call=[{call_started_at}, {call_finished_at}], decode={dict(decode)})"
    )

    if order_created_at is not None:
        deadline = order_created_at + order_expiry_delta_seconds
        assert (
            deadline - clock_tolerance_seconds
            <= absolute
            <= deadline + clock_tolerance_seconds
        ), (
            f"LND absolute expiry {absolute} (ts={timestamp} + expiry={expiry}) "
            f"is more than {clock_tolerance_seconds}s from Fiber deadline "
            f"{deadline} (created_at={order_created_at} + "
            f"delta={order_expiry_delta_seconds}), decode={dict(decode)}"
        )

    return timestamp, expiry, absolute
