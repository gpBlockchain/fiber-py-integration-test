"""Measure mainnet / testnet fiber graph sync progress until counts stabilize.

Starts one local mainnet node and one local testnet node, then every 5s queries
list_peers / graph_channels / graph_nodes. When both graph_channels and
graph_nodes stop changing for 1 minute on a node, record final counts and
elapsed time.
"""

import logging
import os
import time

from framework.basic import CkbTest
from framework.test_fiber import Fiber, FiberConfigPath
from framework.util import get_project_root
from framework.graph_sync_metrics import (
    read_positive_int_env,
    sample_nodes_graph_sync_until_stable,
)

# Written for CI / Discord; relative path is also used by the workflow.
SYNC_STATE_RESULTS_FILE = "report/sync_state_results.txt"


LOGGER = logging.getLogger(__name__)

# Local test-only keys; nodes talk to public CKB RPCs but do not fund channels.
ACCOUNT_PRIVATE_MAINNET = (
    "0xaae4515b745efcd6f00c1b40aaeef3dd66c82d75f8f43d0f18e1a1eecb90ada4"
)
ACCOUNT_PRIVATE_TESTNET = (
    "0x518d76bbfe5ffe3a8ef3ad486e784ec333749575fb3c697126cdaa8084d42532"
)

# Mainnet template has no bootnode_addrs; connect explicitly after start.
MAINNET_BOOTNODES = [
    "/ip4/43.199.24.44/tcp/8228/p2p/QmZ2gCTfEF6vKsiYFF2STPeA2rRLRim9nMtzfwiE7uMQ4v",
    "/ip4/54.255.71.126/tcp/8228/p2p/QmcMLnWraRyxd7PFRgvn1QeYRQS2DGsP6fPFCQjtfMs5b2",
]


class TestSyncState(CkbTest):
    mainnet_fiber: Fiber
    testnet_fiber: Fiber

    @classmethod
    def setup_class(cls):
        super().setup_class()

        # 1) mainnet node
        cls.mainnet_fiber = Fiber.init_by_port(
            FiberConfigPath.CURRENT_MAINNET,
            ACCOUNT_PRIVATE_MAINNET,
            "fiber/sync-state-mainnet",
            "8345",
            "8346",
        )
        cls.mainnet_fiber.prepare()
        cls.mainnet_fiber.start()
        for address in MAINNET_BOOTNODES:
            try:
                cls.mainnet_fiber.get_client().connect_peer({"address": address})
            except Exception as exc:
                LOGGER.warning("mainnet connect_peer %s failed: %s", address, exc)

        # 2) testnet node (bootnodes come from testnet_config_3.yml.j2)
        cls.testnet_fiber = Fiber.init_by_port(
            FiberConfigPath.CURRENT_TESTNET,
            ACCOUNT_PRIVATE_TESTNET,
            "fiber/sync-state-testnet",
            "8347",
            "8348",
        )
        cls.testnet_fiber.prepare()
        cls.testnet_fiber.start()

        # brief settle so first peer handshakes can start
        time.sleep(3)

    @classmethod
    def teardown_class(cls):
        for fiber, name in (
            (getattr(cls, "mainnet_fiber", None), "mainnet"),
            (getattr(cls, "testnet_fiber", None), "testnet"),
        ):
            if fiber is None:
                continue
            try:
                fiber.stop()
            except Exception as exc:
                LOGGER.warning("stop %s fiber failed: %s", name, exc)
            try:
                fiber.clean()
            except Exception as exc:
                LOGGER.warning("clean %s fiber failed: %s", name, exc)
        super().teardown_class()

    def test_mainnet_and_testnet_graph_sync_until_stable(self):
        """Poll both networks every 5s until graph counts stay flat for 1 min."""
        sample_seconds = read_positive_int_env("FIBER_SYNC_STATE_SAMPLE_SECONDS", 5)
        stable_seconds = read_positive_int_env("FIBER_SYNC_STATE_STABLE_SECONDS", 60)
        max_duration_seconds = read_positive_int_env(
            "FIBER_SYNC_STATE_MAX_SECONDS",
            7200,
        )

        summaries = sample_nodes_graph_sync_until_stable(
            [
                {
                    "client": self.mainnet_fiber.get_client(),
                    "label": "main_net",
                },
                {
                    "client": self.testnet_fiber.get_client(),
                    "label": "test_net",
                },
            ],
            sample_interval_seconds=sample_seconds,
            stable_seconds=stable_seconds,
            max_duration_seconds=max_duration_seconds,
        )

        assert set(summaries.keys()) == {"main_net", "test_net"}
        result_lines = []
        # Stable order for Discord / CI parsing
        for label in ("main_net", "test_net"):
            summary = summaries[label]
            assert summary is not None, f"{label} missing summary"
            assert summary["elapsed_seconds"] > 0
            assert "final_graph_channels_count" in summary
            assert "final_graph_nodes_count" in summary
            assert "final_list_peers_count" in summary
            assert len(summary["samples"]) >= 1
            line = (
                "[{}] RESULT elapsed={:.2f}s graph_channels={} "
                "graph_nodes={} list_peers={} reason={}".format(
                    label,
                    summary["elapsed_seconds"],
                    summary["final_graph_channels_count"],
                    summary["final_graph_nodes_count"],
                    summary["final_list_peers_count"],
                    summary["reason"],
                )
            )
            print(line)
            result_lines.append(line)

        self._write_sync_state_results(result_lines)

    @staticmethod
    def _write_sync_state_results(result_lines):
        """Persist RESULT lines for GitHub Actions Discord notification."""
        paths = [
            SYNC_STATE_RESULTS_FILE,
            os.path.join(get_project_root(), SYNC_STATE_RESULTS_FILE),
        ]
        # Deduplicate if project root == cwd
        unique_paths = list(dict.fromkeys(os.path.abspath(p) for p in paths))
        body = "\n".join(result_lines) + "\n"
        for path in unique_paths:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(body)
            print(f"wrote sync state results to {path}")
