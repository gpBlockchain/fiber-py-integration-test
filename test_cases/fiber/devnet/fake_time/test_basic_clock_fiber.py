"""BasicClockFiber smoke: advance wall time and mine one CKB epoch."""

from framework.basic_clock_fiber import BasicClockFiber


class TestBasicClockFiber(BasicClockFiber):
    def test_advance_one_epoch(self):
        client = self.node.getClient()
        before = client.get_tip_header()
        pubkey = self.fiber1.get_client().node_info()["pubkey"]
        before_ms = self.cluster_clock.now_ms()

        advanced_ms = self.advance_time_by()

        after = client.get_tip_header()
        assert advanced_ms >= before_ms + self.EPOCH_SECONDS * 1000
        assert int(after["number"], 16) > int(before["number"], 16)
        assert after["epoch"] != before["epoch"]
        assert int(after["timestamp"], 16) >= advanced_ms - 60_000
        assert self.fiber1.get_client().node_info()["pubkey"] == pubkey
