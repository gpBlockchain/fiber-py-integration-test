"""Shared helpers for the PR #1323 (Unified Migration System) regression tests.

These tests cover the new auto-migration flow introduced in fnn v0.9.x:
- A `fnn` binary built after PR #1323 no longer ships a standalone `fnn-migrate`
  for migrations >= INIT_DB_VERSION; instead it auto-migrates on startup with a
  confirm/progress callback (`MigrateConfirmFn` / `MigrateProgressFn`).
- For databases older than INIT_DB_VERSION (anything from <= v0.7.x), the user
  must first run the legacy `fnn-migrate` from the v0.8.x release line.
- The first migration that runs through the new system is the channel actor data
  migration (0.8.1 -> 0.9.0-rc2), which adds `connectivity_state` and
  `external_funding` fields to every persisted `ChannelActorData`.

Helpers in this file deliberately stay shell-driven so that they exercise
exactly what a real operator would do (`echo y | fnn ...`).
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import time
from pathlib import Path

import requests

from framework.basic_fiber import FiberTest, XUDT_TX_HASH
from framework.config import DEFAULT_MIN_DEPOSIT_CKB
from framework.helper.udt_contract import UdtContract
from framework.util import get_project_root

MIG_VERSION_KEY_FILE_HINT = "db-version"
INIT_DB_VERSION = "20260302100001"
LATEST_DB_VERSION_AFTER_PR1323 = "20260518120000"


class MigrationFiberTest(FiberTest):
    """FiberTest variant for migration tests that start their own nodes."""

    def setup_method(self, method):
        print("setup_method")
        self.did_pass = None
        self.beginNum = hex(self.node.getClient().get_tip_block_number())
        self.fibers = []
        self.new_fibers = []
        self.udtContract = UdtContract(XUDT_TX_HASH, 0)
        if self.debug:
            return
        self.node.getClient().clear_tx_pool()
        self.node.start_miner()
        self.logger.debug(f"\nSetting up method:{method.__name__}")


def fiber_store_dir(fiber) -> Path:
    """Path to the RocksDB store directory of a Fiber node."""
    return Path(fiber.tmp_path) / "fiber" / "store"


def node_log_path(fiber) -> Path:
    return Path(fiber.tmp_path) / "node.log"


def read_node_log(fiber) -> str:
    p = node_log_path(fiber)
    if not p.exists():
        return ""
    return p.read_text(errors="replace")


def assert_log_contains(fiber, needle: str):
    log = read_node_log(fiber)
    assert needle in log, (
        f"expected substring not found in node.log: {needle!r}\n"
        f"--- log tail ---\n{log[-4000:]}"
    )


def assert_log_matches(fiber, pattern: str):
    log = read_node_log(fiber)
    assert re.search(pattern, log), (
        f"expected regex not matched in node.log: {pattern!r}\n"
        f"--- log tail ---\n{log[-4000:]}"
    )


def wait_log_matches(fiber, pattern: str, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        log = read_node_log(fiber)
        if re.search(pattern, log):
            return
        time.sleep(0.2)
    assert_log_matches(fiber, pattern)


def fiber_bin_exists(rel_path: str) -> bool:
    return os.path.isfile(os.path.join(get_project_root(), rel_path))


def rpc_call_with_timeout(fiber, method, params, timeout=10):
    response = requests.post(
        f"http://127.0.0.1:{fiber.rpc_port}",
        json={"id": 42, "jsonrpc": "2.0", "method": method, "params": params},
        headers={"content-type": "application/json"},
        timeout=timeout,
    ).json()
    if "error" in response:
        raise Exception(response["error"].get("message", response["error"]))
    return response.get("result")


def list_channels_with_timeout(fiber, timeout=10):
    return rpc_call_with_timeout(fiber, "list_channels", [{}], timeout)["channels"]


def wait_peer_connected(fiber, min_count=1, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        peers = fiber.get_client().list_peers()["peers"]
        if len(peers) >= min_count:
            return peers
        time.sleep(0.2)
    peers = fiber.get_client().list_peers()["peers"]
    raise TimeoutError(
        "expected at least {} connected peers, got {}".format(min_count, peers)
    )


def wait_channels_ready(fiber, expected_count=1, timeout=60):
    ready_states = {"ChannelReady", "CHANNEL_READY"}
    deadline = time.time() + timeout
    while time.time() < deadline:
        channels = list_channels_with_timeout(fiber)
        ready = [c for c in channels if c["state"]["state_name"] in ready_states]
        if len(ready) >= expected_count:
            return channels
        time.sleep(1)
    channels = list_channels_with_timeout(fiber)
    raise TimeoutError(
        "expected at least {} ready channels, got {}".format(expected_count, channels)
    )


def open_v070_channel(opener, balance):
    peer_id = opener.get_client().list_peers()["peers"][0]["peer_id"]
    opener.get_client().open_channel(
        {
            "peer_id": peer_id,
            "funding_amount": hex(balance + DEFAULT_MIN_DEPOSIT_CKB),
            "public": True,
        }
    )


def send_invoice_payment_with_timeout(test_case, fiber1, fiber2, amount, timeout=60):
    invoice = rpc_call_with_timeout(
        fiber2,
        "new_invoice",
        [
            {
                "amount": hex(amount),
                "currency": "Fibd",
                "description": "migration liveness check invoice",
                "payment_preimage": test_case.generate_random_preimage(),
                "hash_algorithm": "sha256",
                "allow_mpp": True,
            }
        ],
    )
    payment = rpc_call_with_timeout(
        fiber1,
        "send_payment",
        [
            {
                "invoice": invoice["invoice_address"],
                "allow_self_payment": True,
                "max_parts": hex(12),
                "max_fee_rate": hex(1000000000000000),
            }
        ],
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = rpc_call_with_timeout(
            fiber1,
            "get_payment",
            [{"payment_hash": payment["payment_hash"]}],
        )
        if result["status"] == "Success":
            return payment["payment_hash"]
        if result["status"] == "Failed":
            raise Exception(f"payment failed: {result}")
        time.sleep(1)
    raise TimeoutError(
        "payment:{} status did not reach Success within {}s".format(
            payment["payment_hash"], timeout
        )
    )


def send_invoice_payment_with_retry(test_case, fiber1, fiber2, amount, timeout=120):
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            return send_invoice_payment_with_timeout(
                test_case, fiber1, fiber2, amount, timeout=30
            )
        except Exception as e:
            last_error = e
            time.sleep(1)
    raise TimeoutError(f"payment did not succeed within {timeout}s: {last_error}")


def start_with_confirm(
    fiber,
    confirm="y",
    password="password0",
    fnn_log_level="debug",
    timeout=300,
):
    """Start fnn and answer the migration confirm prompt.

    This is local to migration tests because normal Fiber tests should keep
    using `fiber.start()`.
    """
    env = os.environ.copy()
    env["FIBER_SECRET_KEY_PASSWORD"] = password
    env["RUST_LOG"] = f"info,fnn={fnn_log_level}"
    log_path = node_log_path(fiber)
    cmd = [
        f"{get_project_root()}/{fiber.fiber_config_enum.fiber_bin_path}",
        "-c",
        f"{fiber.tmp_path}/config.yml",
        "-d",
        fiber.tmp_path,
    ]

    log_file = open(log_path, "ab")
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=env,
    )
    log_file.close()

    if proc.stdin is not None:
        try:
            proc.stdin.write(f"{confirm}\n".encode())
            proc.stdin.close()
        except BrokenPipeError:
            pass

    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            if s.connect_ex(("127.0.0.1", int(fiber.rpc_port))) == 0:
                return

        exit_code = proc.poll()
        if exit_code is not None:
            raise RuntimeError(
                f"Fiber exited before RPC port {fiber.rpc_port} opened "
                f"(exit code {exit_code}).\n--- node.log tail ---\n"
                f"{read_node_log(fiber)[-4000:]}"
            )
        time.sleep(0.3)

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    raise TimeoutError(
        f"Port {fiber.rpc_port} did not become open within {timeout}s.\n"
        f"--- node.log tail ---\n{read_node_log(fiber)[-4000:]}"
    )


def start_blocking(
    fiber,
    confirm="n",
    password="password0",
    fnn_log_level="info",
    timeout=60,
):
    cmd = (
        f"echo {confirm} | FIBER_SECRET_KEY_PASSWORD='{password}' "
        f"RUST_LOG=info,fnn={fnn_log_level} "
        f"{get_project_root()}/{fiber.fiber_config_enum.fiber_bin_path} "
        f"-c {fiber.tmp_path}/config.yml -d {fiber.tmp_path}"
    )
    proc = subprocess.run(
        cmd,
        shell=True,
        timeout=timeout,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    out = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
    return proc.returncode, out
