"""Regression coverage for fiber PR #1463.

remove_watch_channel must refuse live channels instead of deleting protection.
"""

import time

from framework.basic_fiber import FiberTest


class TestPR1463RemoveWatchChannel(FiberTest):
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 5}

    def _wait_log_contains(self, fiber, text, timeout=20):
        log_path = f"{fiber.tmp_path}/node.log"
        for _ in range(timeout):
            with open(log_path, "r") as f:
                if text in f.read():
                    return
            time.sleep(1)
        assert False, f"did not find {text!r} in {log_path}"

    def test_remove_watch_channel_refuses_live_channel(self):
        self.open_channel(self.fiber1, self.fiber2, 200 * 100000000, 100 * 100000000)
        channel_id = self.fiber1.get_client().list_channels(
            {"pubkey": self.fiber2.get_pubkey()}
        )["channels"][0]["channel_id"]

        self.fiber2.get_client().remove_watch_channel({"channel_id": channel_id})
        self._wait_log_contains(self.fiber2, "Refusing to remove watchtower")

        payment_hash = self.send_payment(self.fiber1, self.fiber2, 1 * 100000000)
        self.wait_payment_state(self.fiber1, payment_hash, "Success")
