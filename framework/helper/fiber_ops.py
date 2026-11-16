"""公开 Fiber 操作：等待、付款哈希、通道查询与保留数据的启停/升级。"""

import hashlib
import os
from pathlib import Path
import socket
import subprocess
import time

from framework.util import ckb_hash, get_project_root


def calculate_payment_hash(preimage, algorithm):
    """按发票指定算法计算付款哈希，不依赖节点返回的结果。"""
    return (
        "0x" + hashlib.sha256(bytes.fromhex(preimage[2:])).hexdigest()
        if algorithm == "sha256"
        else ckb_hash(preimage)
    )


def clear_bootnodes(path):
    """只编辑 fiber.bootnode_addrs；不让 YAML 重序列化改变哈希/epoch 的类型。"""
    lines = Path(path).read_text().splitlines(keepends=True)
    result, in_fiber, skipping, found = [], False, False, False
    for line in lines:
        if line.rstrip() == "fiber:":
            assert not found, "重复 fiber 配置段"
            found, in_fiber = True, True
            result.extend([line, "  bootnode_addrs: []\n"])
            continue
        if (
            in_fiber
            and line.strip()
            and not line[0].isspace()
            and not line.startswith("#")
        ):
            in_fiber, skipping = False, False
        if in_fiber and line.startswith("  bootnode_addrs:"):
            skipping = True
            continue
        if skipping:
            if not line.strip() or line.startswith("    ") or line.startswith("  -"):
                continue
            skipping = False
        result.append(line)
    assert found, "缺少 fiber 配置段"
    Path(path).write_text("".join(result))


def get_channel(fiber, channel_id):
    """按完整 ID 查询原通道，包括已关闭通道。"""
    return next(
        c
        for c in fiber.get_client().list_channels({"include_closed": True})["channels"]
        if c["channel_id"] == channel_id
    )


def wait_for_result(label, read, timeout=120):
    """等待查询结果为真；RPC 或断言错误直接向外抛出。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = read()
        if result:
            return result
        time.sleep(1)
    raise AssertionError(f"{label}: {timeout} 秒内未完成")


def start_fiber(fiber, peers=(), *, log_level="info", timeout=120):
    """保留数据启动，清除 bootstrap 地址后仅主动连接指定对端。"""
    # 运维配置中清除 bootstrap；隔离用例还会逐次断言 list_peers 为空。
    path = Path(fiber.tmp_path) / "config.yml"
    clear_bootnodes(path)
    fiber.start(fnn_log_level=log_level, timeout=timeout)
    wait_for_result(
        "node_info 可用", lambda: fiber.get_client().node_info(), timeout=timeout
    )
    for peer in peers:
        fiber.connect_peer(peer)
    return time.monotonic()


def restart_fiber(fiber, peers=(), *, log_level="info", timeout=120):
    """正常停启同一数据目录，只重连调用方明确指定的对端。"""
    fiber.stop()
    return start_fiber(fiber, peers, log_level=log_level, timeout=timeout)


def assert_upstream_payment(
    payer, trampoline, channel_id, payment_hash, amount, *, held
):
    rows = [get_channel(f, channel_id) for f in (payer, trampoline)]
    assert all(c["state"]["state_name"] == "ChannelReady" for c in rows), rows
    payment = payer.get_client().get_payment({"payment_hash": payment_hash})
    assert (
        payment["status"] != "Success" and payment.get("payment_preimage") is None
    ), payment
    if held:
        assert payment["status"] == "Inflight", payment
        assert int(rows[0]["offered_tlc_balance"], 16) == amount, rows
        assert int(rows[1]["received_tlc_balance"], 16) == amount, rows
    return rows, payment


def start_with_confirm(
    fiber,
    confirm="y",
    password="password0",
    fnn_log_level="debug",
    timeout=300,
):
    """启动旧库升级节点并确认迁移；沿用完整数据目录和原 RPC 端口。"""
    env = os.environ.copy()
    env["FIBER_SECRET_KEY_PASSWORD"] = password
    env["RUST_LOG"] = f"info,fnn={fnn_log_level}"
    env.update(getattr(fiber, "extra_env", {}))
    log_path = Path(fiber.tmp_path) / "node.log"
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
                f"{log_path.read_text(errors="replace")[-4000:]}"
            )
        time.sleep(0.3)

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    raise TimeoutError(
        f"Port {fiber.rpc_port} did not become open within {timeout}s.\n"
        f"--- node.log tail ---\n{log_path.read_text(errors="replace")[-4000:]}"
    )
