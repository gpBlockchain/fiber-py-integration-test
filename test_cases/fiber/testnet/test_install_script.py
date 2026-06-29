"""PR #1262 testnet installer regression test.

Default runs cover the installer/startup smoke path. The full real testnet
channel/payment/close acceptance flow is opt-in because it opens and closes a
real public testnet channel.

Run the live flow explicitly with:
    FIBER_PR1262_RUN_LIVE_CHANNEL=1 pytest -q \
        test_cases/fiber/testnet/test_install_script.py::TestPR1262InstallScript::test_install_script_installs_starts_opens_channel_and_sends_payment -s
"""

import os
import re
import signal
import socket
import stat
import subprocess
import textwrap
import time
import urllib.request
from decimal import Decimal
from pathlib import Path
from typing import Optional, Tuple

import pytest

from framework.fiber_rpc import FiberRPCClient
from framework.rpc import RPCClient
from framework.test_fiber import FiberConfigPath
from framework.util import get_project_root

DEFAULT_ACCOUNT_1 = {
    "private_key": "ff86b08163503b9583cbcf48d70de24a1cbd7178f5a0107b976862a31e4b2007",
    "mainnet_address": "ckb1qzda0cr08m85hc8jlnfp3zer7xulejywt49kt2rr0vthywaa50xwsqfqrxdlsl87hs7ggycnapc9ztxrru472cgm63u4s",
    "testnet_address": "ckt1qzda0cr08m85hc8jlnfp3zer7xulejywt49kt2rr0vthywaa50xwsqfqrxdlsl87hs7ggycnapc9ztxrru472cg4g6nlg",
    "lock_arg": "0x20199bf87cfebc3c841313e870512cc31f2be561",
    "lock_hash": "0x4e3b33be6d88bb16ef2e651d8c104d8b71627c1ade2781df2f8193779bd5bc8f",
}
DEFAULT_ACCOUNT_2 = {
    "private_key": "bb063c2683104406b492573e1aa5714f433a9dbb5849f19a0a4fca7f5b6317b8",
    "mainnet_address": "ckb1qzda0cr08m85hc8jlnfp3zer7xulejywt49kt2rr0vthywaa50xwsqf36t5upsx5m25u4pnlhkcwwx5fqlssc0g8w8s5q",
    "testnet_address": "ckt1qzda0cr08m85hc8jlnfp3zer7xulejywt49kt2rr0vthywaa50xwsqf36t5upsx5m25u4pnlhkcwwx5fqlssc0gfuvl7c",
    "lock_arg": "0x31d2e9c0c0d4daa9ca867fbdb0e71a8907e10c3d",
    "lock_hash": "0xbd36bd29384889fa4990dde131ead4ae6cc85e4ce21dfa95b1cec36934ad1c96",
}


def configured_account(index, defaults):
    private_key = os.environ.get(
        f"FIBER_PR1262_ACCOUNT_PRIVATE_{index}", defaults["private_key"]
    ).replace("0x", "")
    use_defaults = private_key == defaults["private_key"]
    return {
        "index": index,
        "private_key": private_key,
        "mainnet_address": os.environ.get(
            f"FIBER_PR1262_ACCOUNT_MAINNET_ADDRESS_{index}",
            defaults["mainnet_address"] if use_defaults else "",
        ),
        "testnet_address": os.environ.get(
            f"FIBER_PR1262_ACCOUNT_TESTNET_ADDRESS_{index}",
            defaults["testnet_address"] if use_defaults else "",
        ),
        "lock_arg": os.environ.get(
            f"FIBER_PR1262_ACCOUNT_LOCK_ARG_{index}",
            defaults["lock_arg"] if use_defaults else "",
        ),
        "lock_hash": os.environ.get(
            f"FIBER_PR1262_ACCOUNT_LOCK_HASH_{index}",
            defaults["lock_hash"] if use_defaults else "",
        ),
    }


ACCOUNT_1 = configured_account(1, DEFAULT_ACCOUNT_1)
ACCOUNT_2 = configured_account(2, DEFAULT_ACCOUNT_2)
ACCOUNT_PRIVATE_1 = ACCOUNT_1["private_key"]
ACCOUNT_PRIVATE_2 = ACCOUNT_2["private_key"]
CKB = 100000000
PAYMENT_AMOUNT_SHANNON = 1000
TESTNET_CKB_RPC_URL = "https://testnet.ckb.dev"
RUN_LIVE_CHANNEL_ENV = "FIBER_PR1262_RUN_LIVE_CHANNEL"
REFRESH_INSTALL_SH_ENV = "FIBER_PR1262_REFRESH_INSTALL_SH"
INSTALL_SH_URL = (
    "https://raw.githubusercontent.com/nervosnetwork/fiber/pull/1262/head"
    "/tools/install/install.sh"
)
INSTALL_SH_CACHE_NAME = "nervosnetwork-fiber-pr-1262"
_INSTALL_SH_PATH_CACHE = {}


def install_sh_download_source():
    return INSTALL_SH_URL, INSTALL_SH_CACHE_NAME


def download_install_sh(url, cache_name):
    cache_key = (url, cache_name)
    cache_dir = Path(get_project_root()) / "download" / "fiber" / "install" / cache_name
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / "install.sh"
    refresh = os.environ.get(REFRESH_INSTALL_SH_ENV) == "1"

    cached_path = _INSTALL_SH_PATH_CACHE.get(cache_key)
    if cached_path and cached_path.exists() and not refresh:
        return cached_path

    if path.exists() and not refresh:
        _INSTALL_SH_PATH_CACHE[cache_key] = path
        return path

    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            path.write_bytes(response.read())
    except Exception as err:
        if path.exists():
            _INSTALL_SH_PATH_CACHE[cache_key] = path
            return path
        pytest.fail(f"install.sh download failed: {url}: {err}")

    _INSTALL_SH_PATH_CACHE[cache_key] = path
    return path


def install_sh_path():
    return download_install_sh(*install_sh_download_source())


def run_live_channel_test():
    return os.environ.get(RUN_LIVE_CHANNEL_ENV) == "1"


def local_fnn_path():
    project_root = Path(get_project_root())
    path = project_root / FiberConfigPath.CURRENT_TESTNET.fiber_bin_path
    if path.exists() and (path.parent / "config" / "testnet" / "config.yml").exists():
        return path

    pytest.fail(
        f"{FiberConfigPath.CURRENT_TESTNET.fiber_bin_path} with bundled testnet config not found"
    )


def free_tcp_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def write_executable(path, content):
    path.write_text(textwrap.dedent(content).lstrip())
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def account_info_for_private_key(private_key):
    private_key = private_key.replace("0x", "")
    for account in (ACCOUNT_1, ACCOUNT_2):
        if account["private_key"] == private_key:
            missing = [
                key
                for key in (
                    "mainnet_address",
                    "testnet_address",
                    "lock_arg",
                    "lock_hash",
                )
                if not account[key]
            ]
            if missing:
                missing_env_names = {
                    "mainnet_address": f"FIBER_PR1262_ACCOUNT_MAINNET_ADDRESS_{account['index']}",
                    "testnet_address": f"FIBER_PR1262_ACCOUNT_TESTNET_ADDRESS_{account['index']}",
                    "lock_arg": f"FIBER_PR1262_ACCOUNT_LOCK_ARG_{account['index']}",
                    "lock_hash": f"FIBER_PR1262_ACCOUNT_LOCK_HASH_{account['index']}",
                }
                pytest.fail(
                    "custom PR1262 test account requires metadata env vars: "
                    + ", ".join(missing_env_names[key] for key in missing)
                )
            return account
    pytest.fail("no PR1262 account metadata found for the requested private key")


def make_fake_ckb_cli(fake_bin):
    fake_bin.mkdir()
    ckb_cli = fake_bin / "ckb-cli"
    write_executable(
        ckb_cli,
        f"""
        #!/bin/sh
        if [ "$1" = "account" ] && [ "$2" = "list" ]; then
          cat <<EOF
        - "#": 0
          address:
            mainnet: $STUB_CKB_MAINNET_ADDRESS
            testnet: $STUB_CKB_TESTNET_ADDRESS
          lock_arg: $STUB_CKB_LOCK_ARG
          lock_hash: $STUB_CKB_LOCK_HASH
          source: Local File System
        EOF
          exit 0
        fi

        if [ "$1" = "account" ] && [ "$2" = "export" ]; then
          output=""
          requested_lock_arg=""
          while [ "$#" -gt 0 ]; do
            if [ "$1" = "--lock-arg" ]; then
              shift
              requested_lock_arg="$1"
            fi
            if [ "$1" = "--extended-privkey-path" ]; then
              shift
              output="$1"
            fi
            shift
          done
          if [ "$requested_lock_arg" != "$STUB_CKB_LOCK_ARG" ]; then
            echo "unknown lock_arg: $requested_lock_arg" >&2
            exit 1
          fi
          mkdir -p "$(dirname "$output")"
          printf '%s\\n' "$STUB_CKB_PRIVATE_KEY" > "$output"
          exit 0
        fi

        echo "unexpected ckb-cli call: $*" >&2
        exit 1
        """,
    )
    return ckb_cli


def patch_config_ports(config_path, p2p_port, rpc_port):
    content = config_path.read_text()
    content = re.sub(
        r'listening_addr:\s*"/ip4/[^"]+/tcp/\d+"',
        f'listening_addr: "/ip4/127.0.0.1/tcp/{p2p_port}"',
        content,
        count=1,
    )
    content = re.sub(
        r'listening_addr:\s*"127\.0\.0\.1:\d+"',
        f'listening_addr: "127.0.0.1:{rpc_port}"',
        content,
        count=1,
    )
    content = re.sub(
        r'rpc_url:\s*"https://testnet\.ckbapp\.dev/"',
        'rpc_url: "https://testnet.ckb.dev"',
        content,
    )
    config_path.write_text(content)


def run_installer(script, fnn, install_dir, fake_bin, tmp_path, private_key):
    account = account_info_for_private_key(private_key)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "HOME": str(tmp_path / "home"),
            "STUB_CKB_PRIVATE_KEY": private_key,
            "STUB_CKB_MAINNET_ADDRESS": account["mainnet_address"],
            "STUB_CKB_TESTNET_ADDRESS": account["testnet_address"],
            "STUB_CKB_LOCK_ARG": account["lock_arg"],
            "STUB_CKB_LOCK_HASH": account["lock_hash"],
        }
    )
    (tmp_path / "home").mkdir(exist_ok=True)

    return subprocess.run(
        ["bash", str(script), "--local-binary", str(fnn), str(install_dir), "testnet"],
        input=f"2\n{account['lock_arg']}\nn\n",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        timeout=120,
        check=False,
    )


def wait_log_contains(log_path, expected, label, timeout=150):
    seen = {value: False for value in expected}
    deadline = time.time() + timeout

    with open(log_path) as log_file:
        while time.time() < deadline:
            line = log_file.readline()
            if not line:
                time.sleep(0.2)
                continue

            print(f"[{label}] {line}", end="", flush=True)
            for value in expected:
                if value in line:
                    seen[value] = True

            if all(seen.values()):
                return log_path.read_text()

    full_output = log_path.read_text()
    assert all(seen.values()), full_output
    return full_output


def wait_startup_logs(proc, log_path, label):
    output = wait_log_contains(
        log_path,
        ["Started listening tentacle"],
        label,
    )
    assert proc.poll() is None, output
    return output


def stop_process(proc):
    if proc.poll() is not None:
        return

    os.killpg(proc.pid, signal.SIGINT)
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=10)


def start_node(install_dir, label):
    env = os.environ.copy()
    env["FIBER_SECRET_KEY_PASSWORD"] = "password0"
    log_path = install_dir / "node.log"
    log_file = open(log_path, "w")
    proc = subprocess.Popen(
        ["bash", str(install_dir / "start-node.sh")],
        cwd=str(install_dir),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        preexec_fn=os.setsid,
    )
    proc._fiber_log_file = log_file
    return proc, log_path, label


def close_node_log(proc):
    log_file = getattr(proc, "_fiber_log_file", None)
    if log_file:
        log_file.close()


def wait_peer_connected(client, pubkey, timeout=120):
    for i in range(timeout):
        peers = client.list_peers().get("peers", [])
        if any(peer["pubkey"] == pubkey for peer in peers):
            print(f"Peer {pubkey} connected")
            return
        print(f"Waiting for peer {pubkey} to connect, try count: {i}", flush=True)
        time.sleep(1)
    raise TimeoutError(f"Peer {pubkey} did not connect")


def get_channel_state(client: FiberRPCClient, pubkey: str) -> Tuple[str, Optional[str]]:
    channels = client.list_channels({"pubkey": pubkey, "include_closed": True})[
        "channels"
    ]
    if not channels:
        return "not found", None
    return channels[0]["state"]["state_name"], channels[0].get("channel_id")


def get_channel_by_id(client: FiberRPCClient, pubkey: str, channel_id: str):
    channels = client.list_channels({"pubkey": pubkey, "include_closed": True})[
        "channels"
    ]
    for channel in channels:
        if channel.get("channel_id") == channel_id:
            return channel
    raise AssertionError(f"channel {channel_id} not found")


def wait_channel_states(
    client1: FiberRPCClient,
    client1_peer_pubkey: str,
    client2: FiberRPCClient,
    client2_peer_pubkey: str,
    expected_state: str,
    timeout: int = 600,
) -> str:
    poll_interval = 2
    deadline = time.time() + timeout
    try_count = 0

    while time.time() < deadline:
        state1, channel_id1 = get_channel_state(client1, client1_peer_pubkey)
        state2, channel_id2 = get_channel_state(client2, client2_peer_pubkey)
        print(
            f"channel1 state: {state1}, channel2 state: {state2}, "
            f"try count={try_count}",
            flush=True,
        )
        if state1 == expected_state and state2 == expected_state:
            return channel_id1 or channel_id2 or ""
        try_count += 1
        time.sleep(poll_interval)
    raise TimeoutError(f"channels did not reach {expected_state}")


def wait_payment_success(client, payment_hash, timeout=300):
    for i in range(timeout):
        payment = client.get_payment({"payment_hash": payment_hash})
        status = payment["status"]
        print(
            f"Waiting for payment {payment_hash}: {status}, try count: {i}", flush=True
        )
        if status == "Success":
            return payment
        if status == "Failed":
            raise AssertionError(f"payment failed: {payment}")
        time.sleep(1)
    raise TimeoutError("payment did not become Success")


def get_ckb_capacity(script, rpc_url=TESTNET_CKB_RPC_URL):
    response = RPCClient(rpc_url).get_cells_capacity(
        {"script": script, "script_type": "lock", "script_search_mode": "exact"}
    )
    return int(response["capacity"], 16)


def wait_committed_transaction(tx_hash, rpc_url=TESTNET_CKB_RPC_URL, timeout=120):
    client = RPCClient(rpc_url)
    for i in range(timeout):
        response = client.get_transaction(tx_hash)
        if response and response.get("transaction"):
            status = response.get("tx_status", {}).get("status")
            if status == "committed":
                return response["transaction"]
        print(f"Waiting for committed tx {tx_hash}, try count: {i}", flush=True)
        time.sleep(1)
    raise TimeoutError(f"transaction {tx_hash} did not become committed")


def get_script_output_capacity_from_tx(tx, lock_script):
    output_total = 0
    for output in tx["outputs"]:
        if output["lock"] == lock_script:
            output_total += int(output["capacity"], 16)
    return output_total


def format_ckb(shannon):
    return f"{(Decimal(shannon) / Decimal(CKB)).quantize(Decimal('0.00000001'))} CKB"


def print_balance_change(label, before, after):
    delta = after - before
    print(
        f"{label} CKB balance: before={format_ckb(before)}, "
        f"after={format_ckb(after)}, delta={format_ckb(delta)}"
    )


def first_listen_addr(startup_output):
    match = re.search(r'(/ip4/127\.0\.0\.1/tcp/\d+/p2p/[^",\s]+)', startup_output)
    assert match, startup_output
    return match.group(1)


def install_node(script, fnn, tmp_path, name, private_key, p2p_port, rpc_port):
    fake_bin = tmp_path / f"fake-bin-{name}"
    make_fake_ckb_cli(fake_bin)

    install_dir = tmp_path / name
    install_result = run_installer(
        script, fnn, install_dir, fake_bin, tmp_path, private_key
    )

    print(install_result.stdout)
    assert install_result.returncode == 0, install_result.stdout
    assert "Installation Complete" in install_result.stdout
    assert "To start your node, run:" in install_result.stdout
    assert (install_dir / "fnn").exists()
    assert (install_dir / "fnn-cli").exists()
    assert (install_dir / "config.yml").exists()
    assert (install_dir / "start-node.sh").exists()

    key_path = install_dir / "ckb" / "key"
    assert key_path.read_text().strip() == private_key
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600

    patch_config_ports(install_dir / "config.yml", p2p_port, rpc_port)
    return install_dir


@pytest.mark.skipif(os.name != "posix", reason="start-node.sh is a Unix script")
class TestPR1262InstallScript:
    def test_install_script_installs_and_starts_node(self, tmp_path):
        script = install_sh_path()
        fnn = local_fnn_path()
        node_p2p_port = free_tcp_port()
        node_rpc_port = free_tcp_port()

        node_dir = install_node(
            script,
            fnn,
            tmp_path,
            "node1",
            ACCOUNT_PRIVATE_1,
            node_p2p_port,
            node_rpc_port,
        )

        node = start_node(node_dir, "node1")
        node_output = ""
        try:
            node_output = wait_startup_logs(*node)
            client = FiberRPCClient(f"http://127.0.0.1:{node_rpc_port}")
            node_info = client.node_info()
            assert "pubkey" in node_info
            assert "default_funding_lock_script" in node_info
            listen_addr = first_listen_addr(node_output)
            assert f"/tcp/{node_p2p_port}/p2p/" in listen_addr
        finally:
            stop_process(node[0])
            close_node_log(node[0])

        assert "Starting Fiber Network Node" in node_output
        assert "Started listening tentacle" in node_output

    @pytest.mark.skipif(
        not run_live_channel_test(),
        reason=(
            f"set {RUN_LIVE_CHANNEL_ENV}=1 to run the real testnet "
            "channel/payment/close acceptance flow"
        ),
    )
    def test_install_script_installs_starts_opens_channel_and_sends_payment(
        self, tmp_path
    ):
        """Manual live testnet acceptance flow.

        Run with FIBER_PR1262_RUN_LIVE_CHANNEL=1. This opens a real public
        testnet channel, sends a keysend payment, and closes the channel.
        """
        script = install_sh_path()
        fnn = local_fnn_path()
        node1_p2p_port = free_tcp_port()
        node1_rpc_port = free_tcp_port()
        node2_p2p_port = free_tcp_port()
        node2_rpc_port = free_tcp_port()

        node1_dir = install_node(
            script,
            fnn,
            tmp_path,
            "node1",
            ACCOUNT_PRIVATE_1,
            node1_p2p_port,
            node1_rpc_port,
        )
        node2_dir = install_node(
            script,
            fnn,
            tmp_path,
            "node2",
            ACCOUNT_PRIVATE_2,
            node2_p2p_port,
            node2_rpc_port,
        )

        node1 = start_node(node1_dir, "node1")
        node2 = start_node(node2_dir, "node2")
        processes = [node1[0], node2[0]]
        node1_output = ""
        node2_output = ""
        node1_client = None
        channel_id = None
        closed_channel_id = None
        node1_close_script = None
        node2_close_script = None
        try:
            node1_output = wait_startup_logs(*node1)
            node2_output = wait_startup_logs(*node2)

            node1_client = FiberRPCClient(f"http://127.0.0.1:{node1_rpc_port}")
            node2_client = FiberRPCClient(f"http://127.0.0.1:{node2_rpc_port}")
            node1_info = node1_client.node_info()
            node2_info = node2_client.node_info()
            node1_close_script = node1_info["default_funding_lock_script"]
            node2_close_script = node2_info["default_funding_lock_script"]
            node2_addr = first_listen_addr(node2_output)
            node1_balance_before_open = get_ckb_capacity(node1_close_script)
            node2_balance_before_open = get_ckb_capacity(node2_close_script)
            print(
                f"node1 CKB balance before open: {format_ckb(node1_balance_before_open)}"
            )
            print(
                f"node2 CKB balance before open: {format_ckb(node2_balance_before_open)}"
            )

            print(f"Connecting node1 to node2: {node2_addr}")
            node1_client.connect_peer({"address": node2_addr})
            wait_peer_connected(node1_client, node2_info["pubkey"])

            print("Opening real testnet channel1 from node1 to node2")
            open_result = node1_client.open_channel(
                {
                    "pubkey": node2_info["pubkey"],
                    "funding_amount": hex(200 * CKB),
                    "public": True,
                }
            )
            assert "temporary_channel_id" in open_result
            print(
                f"channel1 temporary_channel_id: {open_result['temporary_channel_id']}"
            )
            channel_id = wait_channel_states(
                node1_client,
                node2_info["pubkey"],
                node2_client,
                node1_info["pubkey"],
                "ChannelReady",
            )
            node2_channel_before_payment = get_channel_by_id(
                node2_client, node1_info["pubkey"], channel_id
            )
            node2_local_before_payment = int(
                node2_channel_before_payment["local_balance"], 16
            )

            print(
                "Sending real keysend payment from node1 to node2: "
                f"{format_ckb(PAYMENT_AMOUNT_SHANNON)}"
            )
            payment = node1_client.send_payment(
                {
                    "amount": hex(PAYMENT_AMOUNT_SHANNON),
                    "target_pubkey": node2_info["pubkey"],
                    "keysend": True,
                    "udt_type_script": None,
                }
            )
            payment = wait_payment_success(node1_client, payment["payment_hash"])
            assert payment["status"] == "Success"
            node2_channel_after_payment = get_channel_by_id(
                node2_client, node1_info["pubkey"], channel_id
            )
            node2_local_after_payment = int(
                node2_channel_after_payment["local_balance"], 16
            )
            print(
                "node2 channel balance before shutdown fee: "
                f"before={format_ckb(node2_local_before_payment)}, "
                f"after={format_ckb(node2_local_after_payment)}, "
                f"delta={format_ckb(node2_local_after_payment - node2_local_before_payment)}"
            )
            assert node2_local_after_payment == (
                node2_local_before_payment + PAYMENT_AMOUNT_SHANNON
            )

            print("channel1 shutdown request")
            shutdown_result = node1_client.shutdown_channel(
                {
                    "channel_id": channel_id,
                    "close_script": node1_close_script,
                    "fee_rate": "0x3FC",
                }
            )
            print(f"channel1 shutdown rpc returned: {shutdown_result}")
            closed_channel_id = wait_channel_states(
                node1_client,
                node2_info["pubkey"],
                node2_client,
                node1_info["pubkey"],
                "Closed",
                timeout=600,
            )
            closed_channel = get_channel_by_id(
                node2_client, node1_info["pubkey"], channel_id
            )
            shutdown_tx_hash = closed_channel.get("shutdown_transaction_hash")
            print(f"channel1 shutdown_transaction_hash: {shutdown_tx_hash}")
            assert shutdown_tx_hash
            shutdown_tx = wait_committed_transaction(shutdown_tx_hash)
            node2_close_tx_output_capacity = get_script_output_capacity_from_tx(
                shutdown_tx, node2_close_script
            )

            node1_balance_after_close = get_ckb_capacity(node1_close_script)
            node2_balance_after_close = get_ckb_capacity(node2_close_script)
            print_balance_change(
                "node1", node1_balance_before_open, node1_balance_after_close
            )
            print_balance_change(
                "node2", node2_balance_before_open, node2_balance_after_close
            )
            node2_wallet_delta_after_close = (
                node2_balance_after_close - node2_balance_before_open
            )
            node2_close_fee_share = (
                node2_local_after_payment
                - node2_local_before_payment
                - node2_wallet_delta_after_close
            )
            print(
                "node2 close settlement: "
                f"wallet delta={format_ckb(node2_wallet_delta_after_close)}, "
                f"close tx output={format_ckb(node2_close_tx_output_capacity)}, "
                f"inferred close fee share={format_ckb(node2_close_fee_share)}"
            )
            assert node2_close_tx_output_capacity > 0
            channel_id = None
        finally:
            if node1_client and channel_id and node1_close_script:
                try:
                    print("channel1 cleanup shutdown request")
                    shutdown_result = node1_client.shutdown_channel(
                        {
                            "channel_id": channel_id,
                            "close_script": node1_close_script,
                            "fee_rate": "0x3FC",
                        }
                    )
                    print(f"channel1 cleanup shutdown rpc returned: {shutdown_result}")
                except Exception as err:
                    print(f"Failed to request shutdown for channel {channel_id}: {err}")

            for proc in processes:
                stop_process(proc)
                close_node_log(proc)

        assert "Starting Fiber Network Node" in node1_output
        assert "Started listening tentacle" in node1_output
        assert "Starting Fiber Network Node" in node2_output
        assert "Started listening tentacle" in node2_output
        assert closed_channel_id
