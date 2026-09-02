"""Base class for P2P fault-injection scenarios.

Each test method gets:

- ``self.victim``: stock ``download/fiber/current/fnn`` (no Dev intercept)
- ``self.attacker``: debug ``download/fiber/attack/fnn``
- ``self.peer``: ``P2pPeer`` bound to the attacker
- ``self.channel_id``: a ChannelReady channel (unless ``auto_open_channel``
  is set to False)

Write a new scenario like this::

    from framework.basic_p2p import P2pFiberTest

    class TestDelayAddTlc(P2pFiberTest):
        def test_delay_inbound_add_tlc(self):
            self.peer.intercept(self.channel_id, capture_in=["AddTlc"])
            self.send_payment(
                self.victim, self.attacker, 1 * 100000000, wait=False
            )
            self.peer.wait_deliver("AddTlc")
"""

from __future__ import annotations

import time

from framework.attack_fnn import requires_attack_fnn
from framework.basic_fiber import FiberTest
from framework.config import DEFAULT_MIN_DEPOSIT_CKB
from framework.p2p_peer import P2pPeer, hex_int
from framework.test_fiber import Fiber, FiberConfigPath

SECP256K1_BLAKE160_CODE_HASH = (
    "0x9bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8"
)


@requires_attack_fnn
class P2pFiberTest(FiberTest):
    """Honest current victim + debug attacker, optional ready channel."""

    fiber_version = FiberConfigPath.CURRENT_DEV
    auto_open_channel = True
    # Keep P2P fault-injection suites independent from the default devnet
    # ports and its on-disk stores, so a focused run has no cross-suite locks.
    tmp_path_name = "tmp-p2p"
    ckb_rpc_port = 18114
    ckb_p2p_port = 18125
    fiber1_rpc_port = 18228
    fiber1_p2p_port = 18227
    fiber2_rpc_port = 18229
    fiber2_p2p_port = 18230
    extra_fiber_rpc_port = 18251
    extra_fiber_p2p_port = 18402
    fnn_log_level = "error"
    attacker_auto_accept = True
    channel_local_balance = 200 * 100000000
    channel_remote_balance = 0

    victim: Fiber
    attacker: Fiber
    peer: P2pPeer
    channel_id: str | None
    temporary_channel_id: str | None

    def setup_method(self, method):
        super().setup_method(method)
        self.victim = self.fiber1
        # P2P cases use the stock victim plus the debug attacker.  The second
        # stock node created by FiberTest is not part of this topology; release
        # it before starting the attacker so focused fault-injection runs stay
        # within the local process budget.
        spare_fiber = self.fiber2
        spare_fiber.force_stop()
        spare_fiber.clean()
        self.fibers.remove(spare_fiber)
        if not self.attacker_auto_accept:
            self.start_fiber_config = {
                **getattr(self, "start_fiber_config", {}),
                "fiber_open_channel_auto_accept_min_ckb_funding_amount": hex(10**18),
            }
        self.attacker = self.start_new_fiber(
            self.generate_account(10000),
            fiber_version=FiberConfigPath.ATTACK_DEV,
        )
        self.peer = P2pPeer(self.attacker)
        self.channel_id = None
        self.temporary_channel_id = None
        if self.auto_open_channel:
            self.open_ready_channel()

    def open_ready_channel(self, local=None, remote=None):
        self.channel_id = self.open_channel(
            self.victim,
            self.attacker,
            self.channel_local_balance if local is None else local,
            self.channel_remote_balance if remote is None else remote,
        )
        return self.channel_id

    def propose_channel(self, local=None, public=True):
        """Victim sends OpenChannel and returns the temporary channel id."""
        amount = self.channel_local_balance if local is None else local
        self.victim.connect_peer(self.attacker)
        result = self.victim.get_client().open_channel(
            {
                "pubkey": self.attacker.get_pubkey(),
                "funding_amount": hex(amount + DEFAULT_MIN_DEPOSIT_CKB),
                "public": public,
            }
        )
        self.temporary_channel_id = result["temporary_channel_id"]
        return self.temporary_channel_id

    def accept_proposed(self, temp_id=None, remote=None):
        """Attacker answers AcceptChannel. Set intercept on temp_id first."""
        temp_id = temp_id or self.temporary_channel_id
        amount = self.channel_remote_balance if remote is None else remote
        return self.attacker.get_client().accept_channel(
            {
                "temporary_channel_id": temp_id,
                "funding_amount": hex(amount + DEFAULT_MIN_DEPOSIT_CKB),
            }
        )

    def wait_ready(self, timeout=180):
        self.channel_id = self.wait_for_channel_state(
            self.victim.get_client(),
            self.attacker.get_pubkey(),
            "ChannelReady",
            timeout=timeout,
        )
        return self.channel_id

    def pay(self, amount=1 * 100000000, wait=False):
        """Victim pays attacker. Default is async so you can intercept."""
        return self.send_payment(self.victim, self.attacker, amount, wait=wait)

    def start_observer(self):
        """A third stock node used to watch gossip / graph broadcast."""
        observer = self.start_new_fiber(
            self.generate_account(1000),
            fiber_version=FiberConfigPath.CURRENT_DEV,
        )
        observer.connect_peer(self.victim)
        return observer

    def wait_graph_channel(self, fiber, timeout=90):
        """Wait until ``fiber``'s graph sees the victim-attacker channel."""
        left = self.victim.get_pubkey()
        right = self.attacker.get_pubkey()
        deadline = time.time() + timeout
        last = []
        while time.time() < deadline:
            last = fiber.get_client().graph_channels().get("channels") or []
            for channel in last:
                nodes = {channel.get("node1"), channel.get("node2")}
                if left in nodes and right in nodes:
                    return channel
            time.sleep(1)
        raise TimeoutError(
            f"graph on {fiber.client.url} never saw {left}--{right}; last={last}"
        )

    def channel_of(self, fiber, include_closed=True):
        channels = fiber.get_client().list_channels({"include_closed": include_closed})[
            "channels"
        ]
        wanted = [item for item in (self.channel_id, self.temporary_channel_id) if item]
        for channel in channels:
            if channel["channel_id"] in wanted:
                return channel
        if wanted:
            raise AssertionError(f"channel {wanted} not found on {fiber.client.url}")
        if channels:
            return channels[0]
        raise AssertionError(f"no channel on {fiber.client.url}")

    def channel_state(self, fiber, include_closed=True):
        return self.channel_of(fiber, include_closed=include_closed)["state"][
            "state_name"
        ]

    def wait_channel_state(self, fiber, expected, timeout=30, include_closed=True):
        deadline = time.time() + timeout
        last = None
        expected_states = (expected,) if isinstance(expected, str) else tuple(expected)
        while time.time() < deadline:
            try:
                last = self.channel_state(fiber, include_closed=include_closed)
            except AssertionError:
                last = None
            if last in expected_states:
                return last
            time.sleep(0.2)
        raise TimeoutError(
            f"channel {self.channel_id} state {last!r} != {expected_states}"
        )

    def close_script(self, fiber):
        return {
            "code_hash": SECP256K1_BLAKE160_CODE_HASH,
            "hash_type": "type",
            "args": fiber.get_account()["lock_arg"],
        }

    def rpc_shutdown(self, fiber, force=False, fee_rate=1000):
        params = {"channel_id": self.channel_id}
        if force:
            params["force"] = True
        else:
            params["close_script"] = self.close_script(fiber)
            params["fee_rate"] = hex(fee_rate)
        return fiber.get_client().shutdown_channel(params)

    def disconnect_peers(self):
        self.victim.get_client().disconnect_peer({"pubkey": self.attacker.get_pubkey()})

    def reconnect_peers(self):
        self.victim.connect_peer(self.attacker)

    @staticmethod
    def hex_int(value):
        return hex_int(value)


class P2pRouterTest(P2pFiberTest):
    """Two stock nodes plus a debug router in the middle.

    Alice (current/fnn) -- Router (attack/fnn) -- Bob (current/fnn)

    The router sees Alice's P2P messages as inbound on ``ch_alice`` and
    Bob's as inbound on ``ch_bob``. Intercept either leg independently.
    """

    auto_open_channel = False

    alice: Fiber
    router: Fiber
    bob: Fiber
    ch_alice: str
    ch_bob: str

    def setup_method(self, method):
        super().setup_method(method)
        self.alice = self.victim
        self.router = self.attacker
        self.bob = self.start_new_fiber(
            self.generate_account(10000),
            fiber_version=FiberConfigPath.CURRENT_DEV,
        )
        self.ch_alice = self.open_channel(self.alice, self.router, 200 * 100000000, 0)
        self.bob.connect_peer(self.router)
        self.ch_bob = self.open_channel(self.router, self.bob, 200 * 100000000, 0)
        self.channel_id = self.ch_alice
        self.wait_graph_channels_sync(self.alice, 2, timeout=90)
        self.wait_graph_channels_sync(self.bob, 2, timeout=90)

    def intercept_alice(self, **kwargs):
        return self.peer.intercept(self.ch_alice, **kwargs)

    def intercept_bob(self, **kwargs):
        return self.peer.intercept(self.ch_bob, **kwargs)

    def pay_alice_to_bob(self, amount=1 * 100000000, wait=False):
        return self.send_payment(self.alice, self.bob, amount, wait=wait)

    def message_from(self, message):
        pubkey = message.get("peer_pubkey") or ""
        if pubkey == self.alice.get_pubkey():
            return "alice"
        if pubkey == self.bob.get_pubkey():
            return "bob"
        return "router"
