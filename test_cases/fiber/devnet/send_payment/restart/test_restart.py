"""Verify payment recovery after graceful and forced middle-node restarts."""

import time

from framework.basic_fiber import FiberTest
from framework.fiber_rpc import FiberRPCClient


class TestStopMidNode(FiberTest):
    try_count = 30

    def _wait_route_ready(self, links, timeout=60):
        """Observe automatic reconnection; do not reconnect peers or send probes."""
        deadline = time.monotonic() + timeout
        last = {}
        while time.monotonic() < deadline:
            ready = True
            for node, peer_pubkey, channel_id in links:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    ready = False
                    break
                # Keep read retries bounded instead of using the client's 200 retries.
                client = FiberRPCClient(
                    node.get_client().url, try_count=1, timeout=min(3, remaining)
                )
                key = (node.rpc_port, channel_id)
                try:
                    peers = client.list_peers()["peers"]
                    channels = client.list_channels({"pubkey": peer_pubkey})["channels"]
                    channel = next(
                        (item for item in channels if item["channel_id"] == channel_id),
                        None,
                    )
                    connected = any(peer["pubkey"] == peer_pubkey for peer in peers)
                    state = channel["state"]["state_name"] if channel else None
                    last[key] = {"connected": connected, "state": state}
                    ready = ready and connected and state == "ChannelReady"
                except Exception as error:
                    last[key] = repr(error)
                    ready = False
            if ready:
                return
            time.sleep(min(0.5, max(0, deadline - time.monotonic())))
        raise TimeoutError(f"Route did not reconnect within {timeout}s; last={last}")

    def _check_restart_payment_recovery(self, force):
        # 1. Create an isolated three-node route and wait for graph propagation.
        fiber = self.start_new_fiber(self.generate_account(10000))
        self.fiber1.connect_peer(fiber)
        links = []
        for left, right in zip(self.fibers, self.fibers[1:]):
            channel_id = self.open_channel(
                left, right, 1000 * 100000000, 1000 * 100000000
            )
            links.extend(
                (
                    (left, right.get_pubkey(), channel_id),
                    (right, left.get_pubkey(), channel_id),
                )
            )
        self._wait_route_ready(links)
        for node in self.fibers:
            self.wait_graph_channels_sync(node, 2)

        directions = (
            (self.fibers[0], self.fibers[2]),
            (self.fibers[2], self.fibers[0]),
        )
        middle = self.fibers[1]
        for round_index in range(self.try_count):
            self.logger.info("restart round=%s force=%s", round_index + 1, force)
            # 2. Submit each payment once. Submission errors must not silently
            # turn this into a restart test with no accepted payments.
            accepted = []
            for _ in range(10):
                for sender, receiver in directions:
                    payment_hash = self.send_payment(
                        sender, receiver, 1, wait=False, try_count=0
                    )
                    accepted.append((sender, payment_hash))

            if force:
                middle.force_stop()
            else:
                middle.stop()
            middle.start(fnn_log_level=self.fnn_log_level)
            self._wait_route_ready(links)

            # 3. ChannelReady is persisted state, not proof that replay finished.
            # Keep the original Success-or-Failed contract for pre-restart payments.
            for sender, payment_hash in accepted:
                self.wait_payment_finished(sender, payment_hash, 120)
            for node in self.fibers:
                self.wait_fibers_pending_tlc_eq0(node, wait_times=120)

            # 4. Only now test fresh payments; never replace a failed probe with
            # a retry (the framework's default send_payment does that).
            for sender, receiver in directions:
                payment_hash = self.send_payment(
                    sender, receiver, 1, wait=False, try_count=0
                )
                self.wait_payment_state(sender, payment_hash, "Success", 120)

    def test_stop_mid_node(self):
        """Graceful restart: old payments terminate and both directions recover."""
        self._check_restart_payment_recovery(force=False)

    def test_force_stop_mid_node(self):
        """Forced restart: old payments terminate and both directions recover."""
        self._check_restart_payment_recovery(force=True)
