"""Integration tests for fiber PR #1263.

PR #1263 — *Add gossip ban and rate limit policy*.

Background
----------
Adds ``FiberConfig.gossip_policy`` (``GossipPolicyConfig``) wiring an inbound
gossip rate-limiter + ban tracker into the fiber network actor:

* ``ban``: peers accumulate ``score`` on each ``GossipViolation``; once
  ``score >= threshold`` the peer is disconnected and rejected for the next
  ``duration_ms``. Peers with at least one active channel are NOT
  disconnected (only scored).
* ``inbound_channel_update``: token-bucket rate-limiter keyed by
  ``(peer, outpoint, direction)`` for ChannelUpdate gossip messages. After
  ``burst`` updates in ``interval_ms`` the limiter rejects further updates
  from that key.
* ``outbound_global`` / ``outbound_peer``: token-bucket rate-limit outbound
  gossip bytes; overflow goes to ``outbound_delay_queue_capacity``.
* Deferred ChannelAnnouncement: announcements whose funding tx is not yet
  visible locally are buffered rather than treated as invalid gossip
  (no ban).

The ``gossip_policy:`` block is loadable from the config file only — it is
not exposed via CLI flags or env vars. The dev_config_3 template the
framework renders does not include it; tests below inject it by patching
the rendered ``config.yml`` between ``prepare()`` and ``start()``.

What is observable from Python integration tests
------------------------------------------------
The Python layer can directly drive:
* ``open_channel`` / ``update_channel`` (broadcasts a ChannelUpdate).
* ``graph_channels`` on a peer (shows the latest accepted ChannelUpdate for
  each direction).

Hence we test:
1. Default ``gossip_policy`` doesn't break boot.
2. Custom ``gossip_policy`` block under ``fiber:`` is accepted by the parser.
3. Inbound ChannelUpdate rate-limit: with a very small ``burst`` override,
   spamming ``update_channel`` from one peer causes the receiver's graph to
   stop reflecting later fee rates.

Behaviors that require injecting invalid gossip (ban tracker scoring,
disconnect on threshold, deferred-announcement path) need internal hooks
not exposed via JSON-RPC, so they are not covered here.
"""

import re
import time

from framework.basic_fiber import FiberTest
from framework.config import DEFAULT_MIN_DEPOSIT_CKB

CHANNEL_CAPACITY = 500 * 100000000  # 500 CKB
PUBLIC_CHANNEL_OTHER = {"public": True}


def _inject_gossip_policy_block(config_path, gossip_policy_yaml):
    """Insert (or replace) a ``gossip_policy:`` block immediately under the
    top-level ``fiber:`` key in the rendered ``config.yml``.

    ``gossip_policy_yaml`` is the raw multi-line YAML to splice in, already
    indented with two leading spaces on each non-empty line, e.g.::

        '  gossip_policy:\\n    inbound_channel_update:\\n      burst: 2\\n      interval_ms: 60000\\n'
    """
    with open(config_path) as f:
        content = f.read()

    # Drop any existing gossip_policy block we may have previously injected,
    # using a simple block-strip: from "  gossip_policy:" up to (but not
    # including) the next line whose indentation is 0–1 spaces (i.e. the
    # next top-level key or blank line that breaks indentation).
    content = re.sub(
        r"(?ms)^  gossip_policy:\n(?:    .*\n|      .*\n|        .*\n|\s*\n)+",
        "",
        content,
    )

    if not content.startswith("fiber:\n"):
        raise AssertionError(
            f"unexpected config.yml shape, doesn't start with 'fiber:': {content[:80]!r}"
        )

    content = content.replace("fiber:\n", "fiber:\n" + gossip_policy_yaml, 1)
    with open(config_path, "w") as f:
        f.write(content)


def _restart_fiber_with_gossip_policy(fiber, gossip_policy_yaml, fnn_log_level):
    fiber.stop()
    _inject_gossip_policy_block(fiber.fiber_config_path, gossip_policy_yaml)
    fiber.start(fnn_log_level=fnn_log_level)


def _channel_for_peer(fiber, peer_pubkey):
    channels = fiber.get_client().list_channels({"pubkey": peer_pubkey})["channels"]
    assert channels, f"no channel with peer {peer_pubkey}"
    return channels[0]


def _graph_channel(fiber, channel_outpoint):
    """Find a graph_channels entry by channel_outpoint. Returns None if
    not yet visible in this node's graph."""
    resp = fiber.get_client().graph_channels({})
    for ch in resp.get("channels", []):
        if ch.get("channel_outpoint") == channel_outpoint:
            return ch
    return None


def _wait_graph_channel(fiber, channel_outpoint, timeout=30):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = _graph_channel(fiber, channel_outpoint)
        if last is not None:
            return last
        time.sleep(1)
    raise AssertionError(
        f"channel {channel_outpoint} not found in {fiber.fiber_config_path}'s graph in {timeout}s"
    )


class TestGossipPolicy(FiberTest):
    __test__ = True

    # ------------------------------------------------------------------
    # 1. Default gossip_policy (no override) — the new field must be
    #    optional, so an existing-template config should still boot.
    # ------------------------------------------------------------------
    def test_node_boots_with_default_gossip_policy(self):
        # setup_method already brought up fiber1 + fiber2 with the
        # dev_config_3 template, which doesn't set gossip_policy. If the
        # binary requires the field, both startups would have failed and
        # this assertion line wouldn't be reached.
        info1 = self.fiber1.get_client().node_info()
        info2 = self.fiber2.get_client().node_info()
        assert info1["pubkey"], "fiber1 node_info missing pubkey"
        assert info2["pubkey"], "fiber2 node_info missing pubkey"

    # ------------------------------------------------------------------
    # 2. Custom gossip_policy block under fiber: — the parser must accept
    #    the new keys.
    # ------------------------------------------------------------------
    def test_node_boots_with_custom_gossip_policy(self):
        custom = (
            "  gossip_policy:\n"
            "    ban:\n"
            "      threshold: 50\n"
            "      duration_ms: 1000\n"
            "    inbound_channel_update:\n"
            "      interval_ms: 1000\n"
            "      burst: 3\n"
            "    outbound_global:\n"
            "      rate_bytes_per_sec: 1024000\n"
            "      burst_bytes: 2048000\n"
            "    outbound_peer:\n"
            "      rate_bytes_per_sec: 51200\n"
            "      burst_bytes: 102400\n"
            "    outbound_delay_queue_capacity: 1024\n"
        )
        _restart_fiber_with_gossip_policy(
            self.fiber1, custom, fnn_log_level=self.fnn_log_level
        )
        info = self.fiber1.get_client().node_info()
        assert info["pubkey"], "fiber1 node_info missing pubkey after restart"

    # ------------------------------------------------------------------
    # 3. Inbound ChannelUpdate rate-limit: with burst=2 / interval_ms=60s
    #    on fiber2, send 8 update_channel calls from fiber1 with
    #    increasing fee rates. The 8th value must NOT propagate to fiber2.
    # ------------------------------------------------------------------
    def test_channel_update_throttled_when_burst_exceeded(self):
        # Restart fiber2 with a tight inbound ChannelUpdate limit so we
        # can observe drops. burst=2 means only 2 updates per 60s per
        # (peer, outpoint, direction) tuple are accepted before drops kick
        # in.
        gossip_yaml = (
            "  gossip_policy:\n"
            "    inbound_channel_update:\n"
            "      interval_ms: 60000\n"
            "      burst: 2\n"
        )
        _restart_fiber_with_gossip_policy(
            self.fiber2, gossip_yaml, fnn_log_level=self.fnn_log_level
        )
        self.fiber1.connect_peer(self.fiber2)

        # Open a *public* channel — only public channels broadcast
        # ChannelUpdate via gossip (private ones don't trigger inbound
        # gossip on the peer).
        self.open_channel(
            self.fiber1,
            self.fiber2,
            CHANNEL_CAPACITY,
            0,
            fiber1_fee=1000,
            fiber2_fee=1000,
        )

        channel = _channel_for_peer(self.fiber1, self.fiber2.get_pubkey())
        channel_id = channel["channel_id"]
        # Wait until both nodes see the channel in their graph (post
        # ChannelAnnouncement + initial ChannelUpdates).
        # The graph_channels entry's channel_outpoint identifies the channel
        # but we don't have the funding tx hash in list_channels response;
        # fall back to picking the unique channel in graph.

        # First: poll fiber2 until it has at least one channel in its graph
        # (which means the burst-limited initial updates went through).
        deadline = time.time() + 30
        graph_entry = None
        while time.time() < deadline:
            chans = self.fiber2.get_client().graph_channels({}).get("channels", [])
            if chans:
                graph_entry = chans[0]
                break
            time.sleep(1)
        assert graph_entry is not None, (
            "fiber2's graph never saw the new channel — the initial "
            "ChannelAnnouncement + ChannelUpdate didn't propagate at all"
        )

        # Now spam 8 update_channel calls from fiber1 with strictly
        # increasing fee rates. Each call causes fiber1 to broadcast a new
        # ChannelUpdate. After fiber2 has accepted `burst` of them within
        # `interval_ms`, subsequent ones must be dropped.
        sent_fees = [2000, 3000, 4000, 5000, 6000, 7000, 8000, 9000]
        for fee in sent_fees:
            self.fiber1.get_client().update_channel(
                {
                    "channel_id": channel_id,
                    "tlc_fee_proportional_millionths": hex(fee),
                }
            )
            # No sleep between calls — we want them within one burst window.

        fiber1_pubkey = self.fiber1.get_pubkey()
        outpoint = graph_entry.get("channel_outpoint")
        min_sender_fee = sent_fees[-2]

        # graph_channels response shape (observed):
        #   {"node1": "<pubkey>", "node2": "<pubkey>",
        #    "update_info_of_node1": {"fee_rate": "0x..", "enabled": .., ...},
        #    "update_info_of_node2": {...}, ...}
        # The direction whose top-level node pubkey matches fiber1's pubkey
        # is the one being rate-limited.
        def _direction_fee(entry, pubkey):
            if entry.get("node1") == pubkey:
                upd = entry.get("update_info_of_node1")
            elif entry.get("node2") == pubkey:
                upd = entry.get("update_info_of_node2")
            else:
                return None
            if upd is None:
                return None
            return int(upd.get("fee_rate", "0x0"), 16)

        # Wait until the sender's own graph reflects the high-frequency
        # update burst. Depending on timestamp/order coalescing, the final
        # RPC update may not be the one that lands locally, so use the
        # sender's actual latest graph value as the comparison baseline.
        # update_channel is asynchronous, so a fixed sleep can observe the
        # previous value on slower CI runners.
        own = None
        own_fee = None
        for _ in range(30):
            own_chans = self.fiber1.get_client().graph_channels({}).get("channels", [])
            own = next(
                (c for c in own_chans if c.get("channel_outpoint") == outpoint), None
            )
            if own is not None:
                own_fee = _direction_fee(own, fiber1_pubkey)
                if own_fee is not None and own_fee >= min_sender_fee:
                    break
            time.sleep(1)
        assert own is not None, "fiber1 lost the channel from its own graph"
        assert (
            own_fee is not None and own_fee >= min_sender_fee
        ), f"fiber1's own graph fee={own_fee} did not reach expected burst updates"

        # Re-fetch fiber2's view. The latest fee rate seen on fiber1's
        # direction in fiber2's graph must be strictly less than the
        # sender's latest local value — otherwise the rate-limit isn't
        # working.
        chans = self.fiber2.get_client().graph_channels({}).get("channels", [])
        assert chans, "fiber2 lost the channel from its graph after updates"

        ours = None
        for c in chans:
            if c.get("channel_outpoint") == outpoint:
                ours = c
                break
        assert ours is not None, f"fiber2's graph no longer contains channel {outpoint}"

        fiber1_direction_fee = _direction_fee(ours, fiber1_pubkey)
        assert (
            fiber1_direction_fee is not None
        ), f"could not locate fiber1's direction in graph entry: {ours!r}"

        # Rate-limited: fiber2 must NOT have caught up with fiber1's latest
        # local graph value.
        assert fiber1_direction_fee < own_fee, (
            f"fiber2 absorbed the latest update fee={fiber1_direction_fee} "
            f"(sender latest={own_fee}); rate-limit appears not to be in effect"
        )
