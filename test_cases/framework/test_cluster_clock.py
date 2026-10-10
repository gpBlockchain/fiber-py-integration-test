"""Framework-only checks for the opt-in shared FNN/CKB wall clock."""

from unittest.mock import Mock, patch

import pytest

from framework.basic_clock_fiber import BasicClockFiber
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
        assert popen.call_args.args[0][0].endswith("/download/fiber/current/fnn")
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
