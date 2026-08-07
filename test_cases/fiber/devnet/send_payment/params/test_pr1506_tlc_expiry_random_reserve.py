import time

import pytest

from framework.basic_fiber import FiberTest

ONE_CKB = 100000000
FINAL_TLC_EXPIRY_DELTA = 24 * 60 * 60 * 1000


class TestPR1506TlcExpiryRandomReserve(FiberTest):
    def _wait_graph_channels(self, fiber, channels_count, timeout=60):
        for _ in range(timeout):
            channels = fiber.get_client().graph_channels({}).get("channels", [])
            if len(channels) >= channels_count:
                return
            time.sleep(1)
        assert False, f"graph_channels did not sync to {channels_count}"

    def test_dry_run_rejects_route_without_random_expiry_reserve(self):
        fiber3 = self.start_new_fiber(self.generate_account(1000))
        self.open_channel(self.fiber1, self.fiber2, 500 * ONE_CKB, 0)
        self.open_channel(self.fiber2, fiber3, 500 * ONE_CKB, 0)
        self._wait_graph_channels(self.fiber1, 2)

        hop_delta = int(self.fiber1.get_client().node_info()["tlc_expiry_delta"], 16)
        final_delta = max(FINAL_TLC_EXPIRY_DELTA, hop_delta)
        base_route_limit = final_delta + hop_delta

        params = {
            "target_pubkey": fiber3.get_pubkey(),
            "amount": hex(10 * ONE_CKB),
            "keysend": True,
            "dry_run": True,
            "final_tlc_expiry_delta": hex(final_delta),
            "tlc_expiry_limit": hex(base_route_limit),
        }

        with pytest.raises(Exception) as exc_info:
            self.fiber1.get_client().send_payment(params)

        error = exc_info.value.args[0]
        assert (
            "tlc_expiry_limit" in error
            or "random expiry reserve" in error
            or "exceeds tlc_expiry_limit" in error
            or "Failed to build route" in error
            or "no path found" in error
        ), f"unexpected error: {error}"

        params["tlc_expiry_limit"] = hex(base_route_limit + hop_delta)
        payment = self.fiber1.get_client().send_payment(params)
        assert "payment_hash" in payment
