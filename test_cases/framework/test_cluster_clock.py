"""Framework-only checks for the opt-in shared FNN/CKB wall clock."""

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from framework.basic_clock_fiber import BasicClockFiber
from framework.basic_share_fiber import SharedFiberTest
from framework.cluster_clock import ClusterClock, resolve_faketime_library
from framework.helper.miner import (
    block_template_transfer_to_submit_block,
    get_hex_timestamp,
)
from framework.test_fiber import Fiber, FiberConfigPath
from framework.test_node import CkbNode


def test_resolve_faketime_library_from_explicit_path(tmp_path, monkeypatch):
    library = tmp_path / "libfaketime.1.dylib"
    library.touch()
    monkeypatch.setenv("FIBER_TEST_FAKETIME_LIB", str(library))

    assert resolve_faketime_library() == str(library)

    monkeypatch.setenv("FIBER_TEST_FAKETIME_LIB", str(tmp_path / "missing.dylib"))
    with pytest.raises(FileNotFoundError, match="FIBER_TEST_FAKETIME_LIB"):
        resolve_faketime_library()


def test_resolve_faketime_library_from_homebrew_prefix(tmp_path, monkeypatch):
    library = tmp_path / "opt/libfaketime/lib/libfaketime.1.dylib"
    library.parent.mkdir(parents=True)
    library.touch()
    monkeypatch.delenv("FIBER_TEST_FAKETIME_LIB", raising=False)
    monkeypatch.setenv("HOMEBREW_PREFIX", str(tmp_path))

    with patch("framework.cluster_clock.platform.system", return_value="Darwin"):
        assert resolve_faketime_library() == str(library)


@pytest.mark.parametrize(
    ("system", "library", "injection_key"),
    [
        ("Darwin", "libfaketime.1.dylib", "DYLD_INSERT_LIBRARIES"),
        ("Linux", "libfaketime.so.1", "LD_PRELOAD"),
    ],
)
def test_cluster_clock_env_and_forward_advance(
    tmp_path, system, library, injection_key
):
    library_path = tmp_path / library
    library_path.touch()
    with patch("framework.cluster_clock.platform.system", return_value=system):
        clock = ClusterClock(library_path)
    try:
        env = clock.process_env()
        assert env[injection_key] == str(library_path)
        assert env["FAKETIME_TIMESTAMP_FILE"] == str(clock.timestamp_file)
        assert env["FAKETIME_DISABLE_SHM"] == "1"
        assert env["FAKETIME_DONT_FAKE_MONOTONIC"] == "1"
        assert clock.timestamp_file.read_text() == "+0\n"

        with patch("framework.cluster_clock.time.time", return_value=1000.0):
            assert clock.advance_seconds(3600) == 4_600_000
            assert get_hex_timestamp(clock) == hex(4_600_000)
            assert clock.advance_to_ms(4_601_001) == 4_602_000
        assert clock.timestamp_file.read_text() == "+3602\n"
        with pytest.raises(ValueError, match="positive"):
            clock.advance_seconds(0)
    finally:
        clock.close()
    assert not clock.timestamp_file.exists()


def test_cluster_clock_persistent_file_resumes_offset(tmp_path):
    library = tmp_path / "libfaketime.1.dylib"
    library.touch()
    clock_file = tmp_path / "state" / "clock.rc"
    with patch("framework.cluster_clock.platform.system", return_value="Darwin"):
        first = ClusterClock(library, timestamp_file=clock_file)
        first.advance_seconds(14_400)
        first.close()
        assert clock_file.read_text() == "+14400\n"

        resumed = ClusterClock(library, timestamp_file=clock_file, reuse=True)
        assert resumed._offset_seconds == 14_400
        resumed.advance_seconds(60)
        resumed.close()
        assert clock_file.read_text() == "+14460\n"

        reset = ClusterClock(library, timestamp_file=clock_file)
        assert reset._offset_seconds == 0
        reset.close()
        assert clock_file.read_text() == "+0\n"


def test_basic_clock_fiber_debug_reuses_matching_processes(tmp_path):
    class DebugClock(BasicClockFiber):
        debug = True

    clock_file = tmp_path / "clock.rc"
    clock_file.write_text("+14400\n")
    state = {
        "library": str(tmp_path / "libfaketime.1.dylib"),
        "ckb_rpc_port": DebugClock.ckb_rpc_port,
        "fiber1_rpc_port": DebugClock.fiber1_rpc_port,
        "fiber2_rpc_port": DebugClock.fiber2_rpc_port,
        "ckb_pid": 101,
        "ckb_miner_pid": 104,
        "fiber1_pid": 102,
        "fiber2_pid": 103,
    }
    clock_file.with_suffix(".json").write_text(json.dumps(state))
    with (
        patch.object(DebugClock, "_debug_clock_path", return_value=clock_file),
        patch(
            "framework.basic_clock_fiber.resolve_faketime_library",
            return_value=state["library"],
        ),
        patch("framework.basic_clock_fiber.check_port", return_value=True),
        patch("framework.basic_clock_fiber.os.kill") as kill,
        patch.object(SharedFiberTest, "setup_class") as parent_setup,
    ):
        DebugClock.setup_class()

    assert DebugClock.virtual_clock_reuse is True
    assert DebugClock.virtual_clock_timestamp_file == clock_file
    parent_setup.assert_called_once_with()
    assert kill.call_count == 4


def test_basic_clock_fiber_debug_restarts_with_saved_offset(tmp_path):
    class DebugClock(BasicClockFiber):
        debug = True

    clock_file = tmp_path / "clock.rc"
    clock_file.write_text("+14400\n")
    state = {
        "library": str(tmp_path / "libfaketime.1.dylib"),
        "ckb_rpc_port": DebugClock.ckb_rpc_port,
        "fiber1_rpc_port": DebugClock.fiber1_rpc_port,
        "fiber2_rpc_port": DebugClock.fiber2_rpc_port,
    }
    clock_file.with_suffix(".json").write_text(json.dumps(state))
    with (
        patch.object(DebugClock, "_debug_clock_path", return_value=clock_file),
        patch(
            "framework.basic_clock_fiber.resolve_faketime_library",
            return_value=state["library"],
        ),
        patch("framework.basic_clock_fiber.check_port", return_value=False),
        patch.object(DebugClock, "_record_debug_cluster") as record,
        patch.object(SharedFiberTest, "setup_class") as parent_setup,
    ):
        DebugClock.setup_class()

    assert DebugClock.virtual_clock_reuse is True
    parent_setup.assert_called_once_with()
    record.assert_called_once_with()


def test_basic_clock_fiber_debug_rejects_old_processes_without_clock(tmp_path):
    class DebugClock(BasicClockFiber):
        debug = True

    with (
        patch.object(
            DebugClock, "_debug_clock_path", return_value=tmp_path / "missing.rc"
        ),
        patch(
            "framework.basic_clock_fiber.resolve_faketime_library",
            return_value="library",
        ),
        patch("framework.basic_clock_fiber.check_port", return_value=True),
        patch.object(SharedFiberTest, "setup_class") as parent_setup,
    ):
        with pytest.raises(RuntimeError, match="no persistent cluster clock state"):
            DebugClock.setup_class()
    parent_setup.assert_not_called()


def test_ckb_node_and_miner_receive_same_clock_env(tmp_path):
    library_path = tmp_path / "libfaketime.1.dylib"
    library_path.touch()
    with patch("framework.cluster_clock.platform.system", return_value="Darwin"):
        clock = ClusterClock(library_path)
    try:
        node = object.__new__(CkbNode)
        node.ckb_dir = str(tmp_path)
        node.ckb_miner_pid = -1
        node.virtual_clock = clock
        with (
            patch("framework.test_node.subprocess.Popen") as popen,
            patch("framework.test_node.time.sleep"),
        ):
            popen.return_value = Mock(pid=456)
            node.start()
            node.start_miner()

        file_path = str(clock.timestamp_file)
        assert popen.call_count == 2
        for call in popen.call_args_list:
            assert call.kwargs["env"]["FAKETIME_TIMESTAMP_FILE"] == file_path
        assert popen.call_args_list[0].args[0][:2] == ["./ckb", "run"]
        assert popen.call_args_list[1].args[0] == ["./ckb", "miner"]
        assert node.ckb_miner_pid == 456
    finally:
        clock.close()


def test_python_block_header_uses_cluster_clock():
    block = {
        "cellbase": {"data": "0x"},
        "transactions": [],
        "compact_target": "0x1",
        "dao": "0x0",
        "epoch": "0x0",
        "number": "0x1",
        "parent_hash": "0x0",
        "extension": None,
        "proposals": [],
    }
    clock = Mock()
    clock.now_ms.return_value = 4_600_000

    submitted = block_template_transfer_to_submit_block(block, clock=clock)

    assert submitted["header"]["timestamp"] == hex(4_600_000)
    clock.now_ms.assert_called_once_with()


def test_fnn_start_command_receives_clock_env(tmp_path):
    library_path = tmp_path / "libfaketime.1.dylib"
    library_path.touch()
    with patch("framework.cluster_clock.platform.system", return_value="Darwin"):
        clock = ClusterClock(library_path)
    try:
        fiber = object.__new__(Fiber)
        fiber.extra_env = clock.process_env()
        fiber.fiber_config_enum = FiberConfigPath.CURRENT_DEV
        fiber.tmp_path = str(tmp_path)
        fiber.rpc_port = "8227"
        with (
            patch("framework.test_fiber.subprocess.Popen") as popen,
            patch("framework.test_fiber.wait_for_port"),
        ):
            popen.return_value = Mock(pid=789)
            fiber.start()
        assert popen.call_args.kwargs["env"]["FAKETIME_TIMESTAMP_FILE"] == str(
            clock.timestamp_file
        )
        assert popen.call_args.kwargs["env"]["DYLD_INSERT_LIBRARIES"] == str(
            library_path
        )
        expected_fnn = (
            Path(__file__).resolve().parents[2]
            / FiberConfigPath.CURRENT_DEV.fiber_bin_path
        )
        assert Path(popen.call_args.args[0][0]) == expected_fnn
        assert fiber.pid == 789
    finally:
        clock.close()


def test_basic_clock_fiber_advances_and_mines_requested_epochs():
    suite = BasicClockFiber()
    suite.cluster_clock = Mock()
    suite.cluster_clock.advance_to_ms.return_value = 7_000
    suite.cluster_clock.advance_seconds.return_value = 8_000
    suite.node = Mock()

    assert suite.advance_time_to(6_000, mine_epochs=2) == 7_000
    suite.cluster_clock.advance_to_ms.assert_called_once_with(6_000)
    suite.node.getClient().generate_epochs.assert_called_once_with("0x2", 0)

    assert suite.advance_time_by() == 8_000
    suite.cluster_clock.advance_seconds.assert_called_once_with(4 * 60 * 60)
    suite.node.getClient().generate_epochs.assert_called_with("0x1", 0)
    with pytest.raises(ValueError, match="mine_epochs"):
        suite.advance_time_by(1, mine_epochs=-1)


def test_basic_clock_fiber_waits_for_chain_median_time():
    suite = BasicClockFiber()
    suite.node = Mock()
    client = suite.node.getClient.return_value
    client.get_tip_header.return_value = {"hash": "0xabc"}
    client.get_block_median_time.return_value = hex(10_000)

    assert suite.wait_chain_median_time(9_000) == 10_000
    client.get_block_median_time.assert_called_once_with("0xabc")


def test_cluster_clock_rejects_rate_absolute_and_rollback(tmp_path):
    library = tmp_path / "libfaketime.so.1"
    library.touch()
    clock_file = tmp_path / "clock.rc"
    with patch("framework.cluster_clock.platform.system", return_value="Linux"):
        for value in ("x2", "+0 x2", "@2020-01-01 00:00:00", "-3600", "+1.5", ""):
            clock_file.write_text(value + "\n")
            with pytest.raises(ValueError, match="Invalid cluster clock offset"):
                ClusterClock(library, timestamp_file=clock_file, reuse=True)

        clock = ClusterClock(library)
        try:
            assert "FAKETIME" not in clock.process_env()
            assert clock.timestamp_file.read_text() == "+0\n"
            with patch("framework.cluster_clock.time.time", return_value=1000.0):
                clock.advance_seconds(60)
                assert clock.offset_seconds == 60
                with pytest.raises(ValueError, match="later"):
                    clock.advance_to_ms(clock.now_ms() - 1)
                with pytest.raises(ValueError, match="positive"):
                    clock.advance_seconds(-1)
        finally:
            clock.close()


def test_merge_env_prepends_existing_preload_and_skips_rate(tmp_path):
    library = tmp_path / "libfaketime.so.1"
    library.touch()
    with patch("framework.cluster_clock.platform.system", return_value="Linux"):
        clock = ClusterClock(library)
    try:
        env = clock.merge_env({"PATH": "/bin", "LD_PRELOAD": "other.so"})
        assert env["LD_PRELOAD"] == f"{library}:other.so"
        assert env["PATH"] == "/bin"
        assert env.get("FAKETIME") is None
        extra = {"LD_PRELOAD": "extra.so"}
        clock.apply_to_env(extra)
        assert extra["LD_PRELOAD"].startswith(str(library))
        assert extra["LD_PRELOAD"].endswith("extra.so")
    finally:
        clock.close()


def test_change_time_blocked_when_cluster_clock_active(tmp_path):
    from framework.util import change_time, restore_time

    library = tmp_path / "libfaketime.1.dylib"
    library.touch()
    with patch("framework.cluster_clock.platform.system", return_value="Darwin"):
        clock = ClusterClock(library)
    try:
        with pytest.raises(RuntimeError, match="cannot be mixed"):
            change_time(1)
        with pytest.raises(RuntimeError, match="cannot be mixed"):
            restore_time()
    finally:
        clock.close()


def test_add_time_routes_to_cluster_clock_or_host_change_time(tmp_path):
    from framework.basic_fiber import FiberTest

    library = tmp_path / "libfaketime.1.dylib"
    library.touch()
    with patch("framework.cluster_clock.platform.system", return_value="Darwin"):
        clock = ClusterClock(library)
    try:
        suite = object.__new__(FiberTest)
        suite.cluster_clock = clock
        with patch("framework.basic_fiber.change_time") as change:
            suite._advance_business_hours(1)
            change.assert_not_called()
            assert clock.offset_seconds == 3600
            with pytest.raises(ValueError, match="backwards"):
                suite._advance_business_hours(-1)

        host = object.__new__(FiberTest)
        host.cluster_clock = None
        FiberTest._clock_origin = None
        with patch("framework.basic_fiber.change_time") as change:
            host._advance_business_hours(2)
            change.assert_called_once_with(2)
            assert FiberTest._clock_origin is not None
        FiberTest._clock_origin = None
    finally:
        clock.close()


def test_restore_time_skips_host_clock_when_cluster_clock_active(tmp_path):
    from framework.basic_fiber import FiberTest

    library = tmp_path / "libfaketime.1.dylib"
    library.touch()
    with patch("framework.cluster_clock.platform.system", return_value="Darwin"):
        clock = ClusterClock(library)
    try:
        FiberTest.cluster_clock = clock
        FiberTest._clock_origin = (1.0, 2.0)
        with patch("framework.util.restore_time") as restore:
            FiberTest.restore_time()
            restore.assert_not_called()
        assert FiberTest._clock_origin is None
    finally:
        FiberTest.cluster_clock = None
        FiberTest._clock_origin = None
        clock.close()


def test_fnn_start_default_does_not_inject_faketime(tmp_path, monkeypatch):
    monkeypatch.delenv("FAKETIME_TIMESTAMP_FILE", raising=False)
    monkeypatch.delenv("LD_PRELOAD", raising=False)
    monkeypatch.delenv("DYLD_INSERT_LIBRARIES", raising=False)
    fiber = object.__new__(Fiber)
    fiber.extra_env = {}
    fiber.fiber_config_enum = FiberConfigPath.CURRENT_DEV
    fiber.tmp_path = str(tmp_path)
    fiber.rpc_port = "8227"
    with (
        patch("framework.test_fiber.subprocess.Popen") as popen,
        patch("framework.test_fiber.wait_for_port"),
    ):
        popen.return_value = Mock(pid=11)
        fiber.start()
    env = popen.call_args.kwargs["env"]
    assert "FAKETIME_TIMESTAMP_FILE" not in env
    assert "LD_PRELOAD" not in env
    assert "DYLD_INSERT_LIBRARIES" not in env
    assert fiber.pid == 11


def test_fnn_restart_inherits_existing_clock_env(tmp_path):
    library_path = tmp_path / "libfaketime.1.dylib"
    library_path.touch()
    with patch("framework.cluster_clock.platform.system", return_value="Darwin"):
        clock = ClusterClock(library_path)
    try:
        fiber = object.__new__(Fiber)
        fiber.extra_env = clock.process_env()
        fiber.fiber_config_enum = FiberConfigPath.CURRENT_DEV
        fiber.tmp_path = str(tmp_path)
        fiber.rpc_port = "8227"
        with (
            patch("framework.test_fiber.subprocess.Popen") as popen,
            patch("framework.test_fiber.wait_for_port"),
        ):
            popen.return_value = Mock(pid=12)
            fiber.start()
            fiber.start()
        assert popen.call_count == 2
        for call in popen.call_args_list:
            assert call.kwargs["env"]["FAKETIME_TIMESTAMP_FILE"] == str(
                clock.timestamp_file
            )
            assert call.kwargs["env"]["DYLD_INSERT_LIBRARIES"] == str(library_path)
    finally:
        clock.close()


def test_ckb_default_start_does_not_inject_clock_env(tmp_path):
    node = object.__new__(CkbNode)
    node.ckb_dir = str(tmp_path)
    node.virtual_clock = None
    with (
        patch("framework.test_node.subprocess.Popen") as popen,
        patch("framework.test_node.time.sleep"),
    ):
        popen.return_value = Mock(pid=13)
        node.start()
    assert popen.call_args.kwargs["env"] is None
    assert node.ckb_pid == 13


def test_ckb_restart_inherits_virtual_clock(tmp_path):
    library_path = tmp_path / "libfaketime.1.dylib"
    library_path.touch()
    with patch("framework.cluster_clock.platform.system", return_value="Darwin"):
        clock = ClusterClock(library_path)
    try:
        node = object.__new__(CkbNode)
        node.ckb_dir = str(tmp_path)
        node.ckb_miner_pid = -1
        node.virtual_clock = clock
        with (
            patch.object(node, "stop"),
            patch.object(node, "stop_miner"),
            patch("framework.test_node.subprocess.Popen") as popen,
            patch("framework.test_node.time.sleep"),
        ):
            popen.return_value = Mock(pid=14)
            node.restart()
        assert node.virtual_clock is clock
        assert popen.call_args.kwargs["env"]["FAKETIME_TIMESTAMP_FILE"] == str(
            clock.timestamp_file
        )
    finally:
        clock.close()


def test_cch_expiry_assert_refuses_active_cluster_clock(tmp_path):
    from framework.helper.cch_lnd_expiry import assert_receive_btc_lnd_expiry

    library = tmp_path / "libfaketime.1.dylib"
    library.touch()
    with patch("framework.cluster_clock.platform.system", return_value="Darwin"):
        clock = ClusterClock(library)
    try:
        with pytest.raises(RuntimeError, match="ClusterClock"):
            assert_receive_btc_lnd_expiry(
                {"timestamp": 100, "expiry": 10},
                order_expiry_delta_seconds=10,
                min_outgoing_invoice_expiry_delta_seconds=5,
                call_started_at=100.0,
                call_finished_at=100.2,
            )
    finally:
        clock.close()


def test_fiber_cch_test_rejects_virtual_clock_library():
    from framework.basic_fiber_with_cch import FiberCchTest

    class BadCch(FiberCchTest):
        virtual_clock_library = "/tmp/libfaketime.so.1"

    with pytest.raises(RuntimeError, match="cannot enable ClusterClock"):
        BadCch.setup_class()


def test_wait_chain_median_time_timeout_is_scheduling_time():
    suite = BasicClockFiber()
    suite.node = Mock()
    client = suite.node.getClient.return_value
    client.get_tip_header.return_value = {"hash": "0xabc"}
    client.get_block_median_time.return_value = hex(1_000)

    now = {"t": 0.0}

    def fake_now():
        return now["t"]

    def fake_sleep(seconds):
        now["t"] += seconds

    with (
        patch("framework.basic_clock_fiber.scheduling_now", side_effect=fake_now),
        patch("framework.basic_clock_fiber.time.sleep", side_effect=fake_sleep),
        patch("framework.basic_clock_fiber.time.time", return_value=10_000_000),
    ):
        with pytest.raises(TimeoutError, match="below"):
            suite.wait_chain_median_time(9_000, timeout=1, interval=0.5)
    assert now["t"] >= 1
