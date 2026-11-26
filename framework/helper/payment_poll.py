"""Bounded polling for payment status without retrying the payment itself."""

from __future__ import annotations

import time
from typing import Callable, Dict, Mapping, MutableMapping, Optional

from framework.cluster_clock import scheduling_now

PaymentReader = Callable[[], Mapping]


def wait_until_any_payment_failed(
    readers: Mapping[str, PaymentReader],
    *,
    timeout: float = 120,
    interval: float = 1,
    clock: Callable[[], float] = scheduling_now,
    sleep: Callable[[float], None] = time.sleep,
) -> Dict[str, Mapping]:
    """Poll until at least one payment status is Failed.

    Timeouts use real scheduling time (``scheduling_now`` / monotonic), not
    ClusterClock business wall time and not a faketime rate. Payments are not
    retried. Both sides remaining Inflight until timeout is a failure, not a
    pass. Errors raised by a reader propagate immediately.

    `readers` maps a label to a zero-argument callable that returns the latest
    payment object (must include a `status` field).
    """
    if not readers:
        raise ValueError("readers must not be empty")
    if timeout < 0:
        raise ValueError("timeout must be >= 0")
    if interval < 0:
        raise ValueError("interval must be >= 0")

    deadline = clock() + timeout
    latest: MutableMapping[str, Optional[Mapping]] = {name: None for name in readers}

    while True:
        for name, read in readers.items():
            latest[name] = read()
        if any(
            payment is not None and payment.get("status") == "Failed"
            for payment in latest.values()
        ):
            return {name: payment for name, payment in latest.items()}

        remaining = deadline - clock()
        if remaining <= 0:
            details = {name: payment for name, payment in latest.items()}
            statuses = {
                name: (payment or {}).get("status") for name, payment in details.items()
            }
            raise AssertionError(
                "timed out after {timeout}s waiting for at least one payment "
                "to reach Failed; statuses={statuses}; payments={details}".format(
                    timeout=timeout, statuses=statuses, details=details
                )
            )
        sleep(min(interval, remaining) if interval > 0 else 0)
