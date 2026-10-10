"""Reusable shared Fiber/devnet base for tests that advance cluster wall time."""

import time

from framework.basic_share_fiber import SharedFiberTest
from framework.cluster_clock import resolve_faketime_library


class BasicClockFiber(SharedFiberTest):
    """Run FNN and CKB with one process-scoped virtual wall clock.

    Install libfaketime or set FIBER_TEST_FAKETIME_LIB to its library path.
    Each test should derive its target time from the state it creates; time only
    moves forward and the class shares its chain/FNN state across test methods.
    """

    EPOCH_SECONDS = 4 * 60 * 60

    @classmethod
    def setup_class(cls):
        cls.virtual_clock_library = resolve_faketime_library()
        super().setup_class()

    def advance_time_by(self, seconds=EPOCH_SECONDS, *, mine_epochs=1):
        self._check_epochs(mine_epochs)
        now_ms = self.cluster_clock.advance_seconds(seconds)
        self._mine_epochs(mine_epochs)
        return now_ms

    def advance_time_to(self, unix_time_ms, *, mine_epochs=1):
        self._check_epochs(mine_epochs)
        now_ms = self.cluster_clock.advance_to_ms(unix_time_ms)
        self._mine_epochs(mine_epochs)
        return now_ms

    def _mine_epochs(self, count):
        if count:
            self.node.getClient().generate_epochs(hex(count), 0)

    @staticmethod
    def _check_epochs(count):
        if not isinstance(count, int) or count < 0:
            raise ValueError("mine_epochs must be a non-negative integer")

    def wait_chain_median_time(self, target_ms, *, timeout=30, interval=0.2):
        """Wait for the devnet median time, using a real-time timeout."""
        deadline = time.monotonic() + timeout
        observed = None
        while time.monotonic() < deadline:
            client = self.node.getClient()
            header = client.get_tip_header()
            observed = int(client.get_block_median_time(header["hash"]), 16)
            if observed >= target_ms:
                return observed
            time.sleep(interval)
        raise TimeoutError(
            f"CKB median time stayed at {observed}, below {target_ms} after {timeout}s"
        )
