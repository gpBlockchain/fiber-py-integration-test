"""Measure mainnet / testnet Fiber graph sync progress until counts stabilize.

Starts one local mainnet node and one local testnet node, then queries peer,
graph, and node status RPCs. Each sample logs graph deltas, per-RPC latency,
peer identity, sync status, and time since the last graph change. Full JSON and
FNN logs are saved under report/ for CI diagnosis.
"""

import json
import logging
import os
import shutil
import time

from framework.basic import CkbTest
from framework.graph_sync_metrics import (
    read_positive_int_env,
    sample_nodes_graph_sync_until_stable,
)
from framework.test_fiber import Fiber, FiberConfigPath
from framework.util import get_project_root

# Written for CI / Discord; relative paths are also used by the workflow.
SYNC_STATE_RESULTS_FILE = "report/sync_state_results.txt"
SYNC_STATE_DIAGNOSTICS_FILE = "report/sync_state_diagnostics.json"
SYNC_STATE_NODE_LOG_DIR = "report/sync_state_node_logs"


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
        mainnet_start = time.monotonic()
        cls.mainnet_fiber = Fiber.init_by_port(
            FiberConfigPath.CURRENT_MAINNET,
            ACCOUNT_PRIVATE_MAINNET,
            "fiber/sync-state-mainnet",
            "8345",
            "8346",
        )
        cls.mainnet_fiber.prepare()
        cls.mainnet_fiber.start()
        print(
            "[main_net] BOOT rpc_ready={:.3f}s node_log={}".format(
                time.monotonic() - mainnet_start,
                os.path.join(cls.mainnet_fiber.tmp_path, "node.log"),
            )
        )
        for address in MAINNET_BOOTNODES:
            connect_started = time.monotonic()
            try:
                cls.mainnet_fiber.get_client().connect_peer({"address": address})
                print(
                    "[main_net] CONNECT_PEER address={} elapsed={:.3f}s result=ok".format(
                        address,
                        time.monotonic() - connect_started,
                    )
                )
            except Exception as exc:
                LOGGER.warning("mainnet connect_peer %s failed: %s", address, exc)
                print(
                    "[main_net] CONNECT_PEER address={} elapsed={:.3f}s "
                    "result=failed error={}".format(
                        address,
                        time.monotonic() - connect_started,
                        " ".join(str(exc).split())[:240],
                    )
                )

        # 2) testnet node (bootnodes come from testnet_config_3.yml.j2)
        testnet_start = time.monotonic()
        cls.testnet_fiber = Fiber.init_by_port(
            FiberConfigPath.CURRENT_TESTNET,
            ACCOUNT_PRIVATE_TESTNET,
            "fiber/sync-state-testnet",
            "8347",
            "8348",
        )
        cls.testnet_fiber.prepare()
        cls.testnet_fiber.start()
        print(
            "[test_net] BOOT rpc_ready={:.3f}s node_log={}".format(
                time.monotonic() - testnet_start,
                os.path.join(cls.testnet_fiber.tmp_path, "node.log"),
            )
        )

        # Brief settle so first peer handshakes can start.
        time.sleep(3)

    @classmethod
    def teardown_class(cls):
        cls._collect_node_artifacts()
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
        """Poll both networks until graph counts stay flat for the stable window."""
        sample_seconds = read_positive_int_env("FIBER_SYNC_STATE_SAMPLE_SECONDS", 5)
        stable_seconds = read_positive_int_env("FIBER_SYNC_STATE_STABLE_SECONDS", 60)
        max_duration_seconds = read_positive_int_env(
            "FIBER_SYNC_STATE_MAX_SECONDS",
            7200,
        )
        print(
            "[sync_state] CONFIG sample_seconds={} stable_seconds={} "
            "max_duration_seconds={}".format(
                sample_seconds,
                stable_seconds,
                max_duration_seconds,
            )
        )

        try:
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
        finally:
            self._collect_node_artifacts()

        assert set(summaries.keys()) == {"main_net", "test_net"}
        result_lines = []
        # Stable order for Discord / CI parsing.
        for label in ("main_net", "test_net"):
            summary = summaries[label]
            assert summary is not None, f"{label} missing summary"
            assert summary["elapsed_seconds"] > 0
            assert "final_graph_channels_count" in summary
            assert "final_graph_nodes_count" in summary
            assert "final_list_peers_count" in summary
            assert len(summary["samples"]) >= 1
            rpc_max = max(
                stats["max_seconds"] for stats in summary["rpc_stats"].values()
            )
            line = (
                "[{}] RESULT elapsed={:.2f}s graph_channels={} "
                "graph_nodes={} list_peers={} reason={} last_change={:.2f}s "
                "stable_for={:.2f}s changes={} peer_changes={} rpc_failures={} "
                "rpc_max={:.3f}s signals={}".format(
                    label,
                    summary["elapsed_seconds"],
                    summary["final_graph_channels_count"],
                    summary["final_graph_nodes_count"],
                    summary["final_list_peers_count"],
                    summary["reason"],
                    summary["last_change_elapsed_seconds"],
                    summary["stable_for_seconds"],
                    summary["graph_change_events"],
                    summary["peer_change_events"],
                    summary["query_failure_count"],
                    rpc_max,
                    ",".join(summary["diagnostic_signals"]),
                )
            )
            print(line)
            result_lines.append(line)

        self._write_sync_state_results(result_lines, summaries)

    @staticmethod
    def _report_paths(relative_path):
        paths = [relative_path, os.path.join(get_project_root(), relative_path)]
        # Deduplicate if project root == cwd.
        return list(dict.fromkeys(os.path.abspath(path) for path in paths))

    @classmethod
    def _write_sync_state_results(cls, result_lines, summaries):
        """Persist compact RESULT lines and complete sample diagnostics."""
        body = "\n".join(result_lines) + "\n"
        for path in cls._report_paths(SYNC_STATE_RESULTS_FILE):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(body)
            print(f"wrote sync state results to {path}")

        for path in cls._report_paths(SYNC_STATE_DIAGNOSTICS_FILE):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(summaries, fh, indent=2, sort_keys=True)
                fh.write("\n")
            print(f"wrote sync state diagnostics to {path}")

    @classmethod
    def _collect_node_artifacts(cls):
        """Copy full FNN logs before cleanup so Actions can upload them."""
        fibers = (
            ("main_net", getattr(cls, "mainnet_fiber", None)),
            ("test_net", getattr(cls, "testnet_fiber", None)),
        )
        for report_root in cls._report_paths(SYNC_STATE_NODE_LOG_DIR):
            os.makedirs(report_root, exist_ok=True)
            for label, fiber in fibers:
                if fiber is None:
                    continue
                source = os.path.join(fiber.tmp_path, "node.log")
                destination = os.path.join(report_root, f"{label}.node.log")
                if not os.path.isfile(source):
                    print(f"[{label}] node log missing: {source}")
                    continue
                shutil.copyfile(source, destination)
                print(
                    "[{}] copied node log source={} destination={} bytes={}".format(
                        label,
                        source,
                        destination,
                        os.path.getsize(destination),
                    )
                )
