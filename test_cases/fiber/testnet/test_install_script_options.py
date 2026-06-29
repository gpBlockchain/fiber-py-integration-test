"""PR #1262 installer regression: CLI options, output formatting, and re-install backup."""

import os
import pty
import re
import select
import shlex
import subprocess
import tarfile
import textwrap
import time

import pytest

from test_install_script import (
    ACCOUNT_1,
    ACCOUNT_PRIVATE_1,
    account_info_for_private_key,
    install_sh_path,
    make_fake_ckb_cli,
    run_installer,
    write_executable,
)

MAINNET_GENESIS_HASH = (
    "0x92b197aa1fba0f63633922c61c92375c9c074a93e85963554f5499fe1450d0e5"
)
TESTNET_GENESIS_HASH = (
    "0x10639e0895502b5688a6be8cf69460d76541bfa4821629d86d62ba0aae3f9606"
)


def run_help(script):
    return subprocess.run(
        ["bash", str(script), "--help"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
        check=False,
    )


def run_installer_args(script, *args, input_text=""):
    return subprocess.run(
        ["bash", str(script), *args],
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
        check=False,
    )


def installer_env(tmp_path, fake_bin, private_key=ACCOUNT_PRIVATE_1):
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
    return env


def minimal_config(network):
    rpc_url = (
        "https://mainnet.ckb.dev/"
        if network == "mainnet"
        else "https://testnet.ckbapp.dev/"
    )
    return textwrap.dedent(f"""
        fiber:
          listening_addr: "/ip4/127.0.0.1/tcp/8228"
          announce_listening_addr: true
          announced_addrs: []
          chain: {network}

        rpc:
          listening_addr: "127.0.0.1:8227"

        ckb:
          rpc_url: "{rpc_url}"

        services:
          - fiber
          - rpc
          - ckb
        """).lstrip()


def make_fake_local_fnn_bundle(tmp_path):
    bundle_dir = tmp_path / "local-fnn"
    bundle_dir.mkdir()
    write_executable(
        bundle_dir / "fnn",
        """
        #!/bin/sh
        echo "fake fnn invoked: $*"
        exit 0
        """,
    )
    write_executable(
        bundle_dir / "fnn-cli",
        """
        #!/bin/sh
        echo "fake fnn-cli invoked: $*"
        exit 0
        """,
    )
    write_executable(
        bundle_dir / "fnn-migrate",
        """
        #!/bin/sh
        echo "fake fnn-migrate invoked: $*"
        exit 0
        """,
    )
    for network in ("testnet", "mainnet"):
        config_dir = bundle_dir / "config" / network
        config_dir.mkdir(parents=True)
        (config_dir / "config.yml").write_text(minimal_config(network))
    return bundle_dir / "fnn"


def make_fake_release_bundle(tmp_path):
    source_dir = tmp_path / "fake-release"
    source_dir.mkdir()
    local_fnn = make_fake_local_fnn_bundle(source_dir)
    bundle_path = tmp_path / "fake-fnn-release.tar.gz"
    with tarfile.open(bundle_path, "w:gz") as archive:
        archive.add(local_fnn.parent, arcname="fnn-release")
    return bundle_path


def make_fake_curl(fake_bin, response, bundle_path=None):
    if bundle_path:
        output_action = f'cp {shlex.quote(str(bundle_path))} "$output"'
    else:
        output_action = 'printf \'%s\\n\' "$response" > "$output"'

    write_executable(
        fake_bin / "curl",
        f"""
        #!/bin/sh
        output=""
        while [ "$#" -gt 0 ]; do
          if [ "$1" = "-o" ]; then
            shift
            output="$1"
          fi
          shift
        done

        response={shlex.quote(response)}
        if [ -n "$output" ]; then
          {output_action}
          exit 0
        fi

        printf '%s\\n' "$response"
        """,
    )


def run_installer_pty(script, args, input_text, env, timeout=120):
    master_fd, slave_fd = pty.openpty()
    proc = None
    output = bytearray()

    try:
        proc = subprocess.Popen(
            ["bash", str(script), *args],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            env=env,
            close_fds=True,
        )
        os.close(slave_fd)
        slave_fd = None
        os.write(master_fd, input_text.encode())

        deadline = time.time() + timeout
        while time.time() < deadline:
            readable, _, _ = select.select([master_fd], [], [], 0.1)
            if readable:
                try:
                    chunk = os.read(master_fd, 4096)
                except OSError:
                    chunk = b""
                if chunk:
                    output.extend(chunk)

            if proc.poll() is not None:
                while True:
                    readable, _, _ = select.select([master_fd], [], [], 0)
                    if not readable:
                        break
                    try:
                        chunk = os.read(master_fd, 4096)
                    except OSError:
                        break
                    if not chunk:
                        break
                    output.extend(chunk)
                break
        else:
            proc.kill()
            raise subprocess.TimeoutExpired(proc.args, timeout, output)

        return subprocess.CompletedProcess(
            proc.args, proc.returncode, output.decode(errors="replace")
        )
    finally:
        if proc and proc.poll() is None:
            proc.kill()
        if slave_fd is not None:
            os.close(slave_fd)
        os.close(master_fd)


@pytest.mark.skipif(os.name != "posix", reason="install.sh is a Unix script")
class TestPR1262InstallScriptOptions:
    def test_help_outputs_usage(self):
        script = install_sh_path()
        result = run_help(script)
        print(result.stdout)
        assert result.returncode == 0, result.stdout
        assert "Fiber Network Node (FNN) installer" in result.stdout
        assert "Usage:" in result.stdout
        assert "--local-binary" in result.stdout
        assert "--mode" in result.stdout

    def test_invalid_network_rejected(self, tmp_path):
        script = install_sh_path()
        fnn = make_fake_local_fnn_bundle(tmp_path)
        fake_bin = tmp_path / "fake-bin"
        make_fake_ckb_cli(fake_bin)
        make_fake_curl(
            fake_bin,
            f'{{"id":2,"jsonrpc":"2.0","result":"{TESTNET_GENESIS_HASH}"}}',
        )
        env = installer_env(tmp_path, fake_bin)

        result = subprocess.run(
            [
                "bash",
                str(script),
                "--local-binary",
                str(fnn),
                str(tmp_path / "node"),
                "foonet",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            timeout=60,
            check=False,
        )
        print(result.stdout)
        assert result.returncode != 0
        assert "Invalid network" in result.stdout
        assert "foonet" in result.stdout

    def test_invalid_mode_rejected(self, tmp_path):
        script = install_sh_path()
        result = run_installer_args(
            script, "--mode", "weird", str(tmp_path / "node"), "testnet"
        )
        print(result.stdout)
        assert result.returncode != 0
        assert "Invalid mode" in result.stdout

    def test_unknown_option_rejected(self, tmp_path):
        script = install_sh_path()
        result = run_installer_args(script, "--definitely-not-an-option")
        print(result.stdout)
        assert result.returncode != 0
        assert "Unknown option" in result.stdout

    def test_mode_requires_value(self):
        script = install_sh_path()
        result = run_installer_args(script, "--mode")
        print(result.stdout)
        assert result.returncode != 0
        assert "--mode requires a value" in result.stdout

    def test_local_binary_requires_value(self):
        script = install_sh_path()
        result = run_installer_args(script, "--local-binary")
        print(result.stdout)
        assert result.returncode != 0
        assert "--local-binary requires a path" in result.stdout

    def test_missing_local_binary_rejected(self, tmp_path):
        script = install_sh_path()
        fake_bin = tmp_path / "fake-bin"
        make_fake_ckb_cli(fake_bin)
        make_fake_curl(
            fake_bin,
            f'{{"id":2,"jsonrpc":"2.0","result":"{TESTNET_GENESIS_HASH}"}}',
        )
        env = installer_env(tmp_path, fake_bin)

        result = subprocess.run(
            [
                "bash",
                str(script),
                "--local-binary",
                str(tmp_path / "missing-fnn"),
                str(tmp_path / "node"),
                "testnet",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            timeout=60,
            check=False,
        )
        print(result.stdout)
        assert result.returncode != 0
        assert "Local fnn binary not found" in result.stdout

    def test_bootstrap_mode_installs_default_testnet_bundle(self, tmp_path):
        script = install_sh_path()
        fake_bin = tmp_path / "fake-bin"
        make_fake_ckb_cli(fake_bin)
        release_bundle = make_fake_release_bundle(tmp_path)
        make_fake_curl(
            fake_bin,
            f'{{"id":2,"jsonrpc":"2.0","result":"{TESTNET_GENESIS_HASH}"}}',
            bundle_path=release_bundle,
        )
        env = installer_env(tmp_path, fake_bin)

        result = subprocess.run(
            ["bash", str(script), "--mode", "bootstrap"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            timeout=120,
            check=False,
        )
        print(result.stdout)
        install_dir = tmp_path / "home" / ".fiber"
        assert result.returncode == 0, result.stdout
        assert "Running in non-interactive mode with defaults" in result.stdout
        assert "Network: testnet" in result.stdout
        assert (install_dir / ".fiber-network").read_text().strip() == "testnet"
        assert (install_dir / "tools" / "install" / "install.sh").exists()
        assert (install_dir / "fiber").is_dir()
        assert (install_dir / "fnn").exists()
        assert (install_dir / "fnn-cli").exists()
        assert (install_dir / "fnn-migrate").exists()
        config = (install_dir / "config.yml").read_text()
        assert "chain: testnet" in config

    def test_install_output_has_no_literal_ansi_escape(self, tmp_path):
        """Regression for ANSI literal `\\033[0m` text in formatted sections."""
        script = install_sh_path()
        fnn = make_fake_local_fnn_bundle(tmp_path)
        fake_bin = tmp_path / "fake-bin"
        make_fake_ckb_cli(fake_bin)
        make_fake_curl(
            fake_bin,
            f'{{"id":2,"jsonrpc":"2.0","result":"{TESTNET_GENESIS_HASH}"}}',
        )
        install_dir = tmp_path / "node"

        result = run_installer(
            script, fnn, install_dir, fake_bin, tmp_path, ACCOUNT_PRIVATE_1
        )
        print(result.stdout)
        assert result.returncode == 0, result.stdout
        assert "Installation Complete" in result.stdout
        # The installer must not leak literal escape sequences in its output.
        assert "\\033[" not in result.stdout, (
            "literal ANSI escape sequence found in installer output:\n" + result.stdout
        )
        assert "\\e[" not in result.stdout

    def test_existing_install_dir_is_backed_up(self, tmp_path):
        """Re-running the installer on a non-empty dir should back it up
        (non-interactive path) and produce a fresh install."""
        script = install_sh_path()
        fnn = make_fake_local_fnn_bundle(tmp_path)
        fake_bin = tmp_path / "fake-bin"
        make_fake_ckb_cli(fake_bin)
        make_fake_curl(
            fake_bin,
            f'{{"id":2,"jsonrpc":"2.0","result":"{TESTNET_GENESIS_HASH}"}}',
        )
        install_dir = tmp_path / "node"

        first = run_installer(
            script, fnn, install_dir, fake_bin, tmp_path, ACCOUNT_PRIVATE_1
        )
        assert first.returncode == 0, first.stdout
        first_key = (install_dir / "ckb" / "key").read_text().strip()

        second = run_installer(
            script, fnn, install_dir, fake_bin, tmp_path, ACCOUNT_PRIVATE_1
        )
        print(second.stdout)
        assert second.returncode == 0, second.stdout
        assert "Backed up existing install path" in second.stdout

        backups = sorted(tmp_path.glob("node.backup-*"))
        assert backups, f"expected backup dir, got: {list(tmp_path.iterdir())}"
        assert (backups[-1] / "ckb" / "key").exists()

        # Fresh install should still be valid.
        assert (install_dir / "fnn").exists()
        assert (install_dir / "start-node.sh").exists()
        assert (install_dir / "config.yml").exists()
        assert (install_dir / "ckb" / "key").read_text().strip() == first_key

    def test_existing_install_dir_preserves_ckb_cli_after_backup(self, tmp_path):
        script = install_sh_path()
        fnn = make_fake_local_fnn_bundle(tmp_path)
        install_dir = tmp_path / "node"
        install_dir.mkdir()
        write_executable(
            install_dir / "ckb-cli",
            """
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
              while [ "$#" -gt 0 ]; do
                if [ "$1" = "--extended-privkey-path" ]; then
                  shift
                  output="$1"
                fi
                shift
              done
              mkdir -p "$(dirname "$output")"
              printf '%s\\n' "$STUB_CKB_PRIVATE_KEY" > "$output"
              exit 0
            fi

            echo "unexpected ckb-cli call: $*" >&2
            exit 1
            """,
        )
        (install_dir / "old-data").write_text("old")

        fake_bin = tmp_path / "fake-bin"
        fake_bin.mkdir()
        make_fake_curl(
            fake_bin,
            f'{{"id":2,"jsonrpc":"2.0","result":"{TESTNET_GENESIS_HASH}"}}',
        )
        env = installer_env(tmp_path, fake_bin)

        result = subprocess.run(
            [
                "bash",
                str(script),
                "--local-binary",
                str(fnn),
                str(install_dir),
                "testnet",
            ],
            input=f"2\n{ACCOUNT_1['lock_arg']}\nn\n",
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            timeout=120,
            check=False,
        )
        print(result.stdout)
        assert result.returncode == 0, result.stdout
        assert "Backed up existing install path" in result.stdout
        assert "Preserved existing ckb-cli" in result.stdout
        assert (install_dir / "ckb-cli").exists()
        assert (install_dir / "ckb" / "key").read_text().strip() == ACCOUNT_PRIVATE_1

    def test_generated_start_node_script_uses_install_dir(self, tmp_path):
        """start-node.sh should resolve INSTALL_DIR relative to itself
        (so users can re-run it from any cwd after install)."""
        script = install_sh_path()
        fnn = make_fake_local_fnn_bundle(tmp_path)
        fake_bin = tmp_path / "fake-bin"
        make_fake_ckb_cli(fake_bin)
        make_fake_curl(
            fake_bin,
            f'{{"id":2,"jsonrpc":"2.0","result":"{TESTNET_GENESIS_HASH}"}}',
        )
        install_dir = tmp_path / "node"

        result = run_installer(
            script, fnn, install_dir, fake_bin, tmp_path, ACCOUNT_PRIVATE_1
        )
        assert result.returncode == 0, result.stdout

        start_script = (install_dir / "start-node.sh").read_text()
        assert 'INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"' in start_script
        assert "FIBER_SECRET_KEY_PASSWORD" in start_script
        assert "./fnn -c config.yml" in start_script
        # Network marker is written/checked so users can't mix networks by mistake.
        assert ".fiber-network" in start_script
        # Sanity: no unresolved bash variable from the heredoc escape.
        assert not re.search(r"\$\{?NETWORK_MARKER_FILE_NAME\}?", start_script)

    def test_generated_start_node_rejects_network_marker_mismatch(self, tmp_path):
        script = install_sh_path()
        fnn = make_fake_local_fnn_bundle(tmp_path)
        fake_bin = tmp_path / "fake-bin"
        make_fake_ckb_cli(fake_bin)
        make_fake_curl(
            fake_bin,
            f'{{"id":2,"jsonrpc":"2.0","result":"{TESTNET_GENESIS_HASH}"}}',
        )
        install_dir = tmp_path / "node"

        result = run_installer(
            script, fnn, install_dir, fake_bin, tmp_path, ACCOUNT_PRIVATE_1
        )
        assert result.returncode == 0, result.stdout
        assert (install_dir / ".fiber-network").read_text().strip() == "testnet"

        config_path = install_dir / "config.yml"
        config_path.write_text(
            config_path.read_text().replace("chain: testnet", "chain: mainnet")
        )

        env = os.environ.copy()
        env["FIBER_SECRET_KEY_PASSWORD"] = "password0"
        start = subprocess.run(
            ["bash", str(install_dir / "start-node.sh")],
            cwd=str(tmp_path),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            timeout=30,
            check=False,
        )
        print(start.stdout)
        assert start.returncode != 0
        assert "marked for testnet" in start.stdout
        assert "config.yml is set to mainnet" in start.stdout

    def test_ckb_rpc_preflight_warns_on_wrong_network_genesis(self, tmp_path):
        script = install_sh_path()
        fnn = make_fake_local_fnn_bundle(tmp_path)
        fake_bin = tmp_path / "fake-bin"
        make_fake_ckb_cli(fake_bin)
        make_fake_curl(
            fake_bin,
            f'{{"id":2,"jsonrpc":"2.0","result":"{MAINNET_GENESIS_HASH}"}}',
        )
        install_dir = tmp_path / "node"

        result = run_installer(
            script, fnn, install_dir, fake_bin, tmp_path, ACCOUNT_PRIVATE_1
        )
        print(result.stdout)
        assert result.returncode == 0, result.stdout
        assert "Skipping automatic startup" in result.stdout
        assert "does not appear to be a testnet node" in result.stdout
        assert "Update ckb.rpc_url" in result.stdout

    def test_mainnet_public_node_configures_announced_fields(self, tmp_path):
        script = install_sh_path()
        fnn = make_fake_local_fnn_bundle(tmp_path)
        fake_bin = tmp_path / "fake-bin"
        make_fake_ckb_cli(fake_bin)
        make_fake_curl(
            fake_bin,
            f'{{"id":2,"jsonrpc":"2.0","result":"{MAINNET_GENESIS_HASH}"}}',
        )
        env = installer_env(tmp_path, fake_bin)
        install_dir = tmp_path / "node"
        ckb_rpc_url = "http://127.0.0.1:8114/"
        announced_addr = "/ip4/203.0.113.10/tcp/8228"
        announced_name = "Node Name"

        result = run_installer_pty(
            script,
            [
                "--local-binary",
                str(fnn),
                str(install_dir),
                "mainnet",
            ],
            (
                f"{ckb_rpc_url}\n"
                f"y\n"
                f"{announced_addr}\n"
                f"{announced_name}\n"
                f"2\n"
                f"{ACCOUNT_1['lock_arg']}\n"
                f"n\n"
            ),
            env,
        )
        print(result.stdout)
        assert result.returncode == 0, result.stdout
        assert "Configured this mainnet node as a public Fiber node" in result.stdout

        config = (install_dir / "config.yml").read_text()
        assert f'rpc_url: "{ckb_rpc_url}"' in config
        assert "chain: mainnet" in config
        assert "auto_announce_node: true" in config
        assert "announce_listening_addr: true" in config
        assert f'    - "{announced_addr}"' in config
        assert f'announced_node_name: "{announced_name}"' in config
