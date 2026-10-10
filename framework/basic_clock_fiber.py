"""Reusable shared Fiber/devnet base for tests that advance cluster wall time."""

import json
import os
from pathlib import Path
import time

import framework.config as framework_config
from framework.basic_fiber import check_port
from framework.basic_share_fiber import SharedFiberTest
from framework.cluster_clock import resolve_faketime_library
from framework.util import get_project_root


class BasicClockFiber(SharedFiberTest):
    """Run FNN and CKB with one process-scoped virtual wall clock.

    Install libfaketime or set FIBER_TEST_FAKETIME_LIB to its library path.
    Each test should derive its target time from the state it creates; time only
    moves forward and the class shares its chain/FNN state across test methods.
    """

    EPOCH_SECONDS = 4 * 60 * 60
    # Keep fake-time chain data separate from ordinary devnet debug sessions.
    tmp_path_name = "tmp/clock-fiber"

    @classmethod
    def setup_class(cls):
        cls.virtual_clock_library = resolve_faketime_library()
        cls.virtual_clock_timestamp_file = None
        cls.virtual_clock_reuse = False
        started_new_debug_cluster = False
        if cls.debug:
            cls.virtual_clock_timestamp_file = cls._debug_clock_path()
            ckb_running = check_port(cls.ckb_rpc_port)
            clock_file = cls.virtual_clock_timestamp_file
            state_file = cls._debug_state_path()
            if ckb_running:
                cls._check_debug_cluster(running=True)
                cls.virtual_clock_reuse = True
            else:
                if check_port(cls.fiber1_rpc_port) or check_port(cls.fiber2_rpc_port):
                    raise RuntimeError("Fiber debug ports are occupied without CKB")
                if clock_file.exists() or state_file.exists():
                    cls._check_debug_cluster(running=False)
                    cls.virtual_clock_reuse = True
                started_new_debug_cluster = True
        super().setup_class()
        if started_new_debug_cluster:
            cls._record_debug_cluster()

    @classmethod
    def teardown_class(cls):
        keep_processes = cls.debug or cls.first_debug
        try:
            super().teardown_class()
        finally:
            if keep_processes:
                if cls.tmp_path_name is not None:
                    framework_config.TMP_PATH = cls._original_tmp_path_name
                cls.cluster_clock.close()

    @classmethod
    def _debug_clock_path(cls):
        tmp_name = cls.tmp_path_name or cls.Config.TMP_PATH
        name = (
            f"ckb-{cls.ckb_rpc_port}-"
            f"fiber-{cls.fiber1_rpc_port}-{cls.fiber2_rpc_port}.rc"
        )
        return Path(get_project_root()) / tmp_name / "cluster-clock" / name

    @classmethod
    def _debug_state_path(cls):
        return cls.virtual_clock_timestamp_file.with_suffix(".json")

    @classmethod
    def _check_debug_cluster(cls, *, running):
        clock_file = cls.virtual_clock_timestamp_file
        state_file = cls._debug_state_path()
        if not clock_file.is_file() or not state_file.is_file():
            raise RuntimeError(
                "Existing debug nodes have no persistent cluster clock state. "
                "Stop those nodes once before starting this clock test in debug mode."
            )
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
            expected = {
                "library": cls.virtual_clock_library,
                "ckb_rpc_port": cls.ckb_rpc_port,
                "fiber1_rpc_port": cls.fiber1_rpc_port,
                "fiber2_rpc_port": cls.fiber2_rpc_port,
            }
            if any(state.get(key) != value for key, value in expected.items()):
                raise ValueError("clock configuration changed")
            if running:
                for key in ("ckb_pid", "ckb_miner_pid", "fiber1_pid", "fiber2_pid"):
                    pid = int(state[key])
                    if pid <= 0:
                        raise ValueError(f"invalid {key}")
                    os.kill(pid, 0)
                if not check_port(cls.fiber1_rpc_port) or not check_port(
                    cls.fiber2_rpc_port
                ):
                    raise ValueError("a Fiber RPC port is closed")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise RuntimeError(
                "Saved debug cluster does not match the clock or running nodes. "
                "Stop old nodes before starting this clock test in debug mode."
            ) from exc

    @classmethod
    def _record_debug_cluster(cls):
        state = {
            "library": cls.virtual_clock_library,
            "ckb_rpc_port": cls.ckb_rpc_port,
            "fiber1_rpc_port": cls.fiber1_rpc_port,
            "fiber2_rpc_port": cls.fiber2_rpc_port,
            "ckb_pid": cls.node.ckb_pid,
            "ckb_miner_pid": cls.node.ckb_miner_pid,
            "fiber1_pid": cls.fiber1.pid,
            "fiber2_pid": cls.fiber2.pid,
        }
        state_file = cls._debug_state_path()
        temporary = state_file.with_suffix(".new")
        temporary.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        os.replace(temporary, state_file)

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
