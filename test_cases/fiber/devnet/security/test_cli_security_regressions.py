import os
from pathlib import Path
import stat
import subprocess

import pytest

from framework.basic_fiber import FiberTest
from framework.util import get_project_root

FNN_CLI = os.path.join(get_project_root(), "download/fiber/current/fnn-cli")

# Skip the CLI-binary tests (rather than erroring) when fnn-cli is not present,
# e.g. running locally without the prepare/download step.
requires_fnn_cli = pytest.mark.skipif(
    not os.path.exists(FNN_CLI), reason=f"fnn-cli not found at {FNN_CLI}"
)


@requires_fnn_cli
def test_auth_token_rejects_implicit_plaintext_remote_http():
    result = subprocess.run(
        [
            FNN_CLI,
            "--url",
            "example.com:8227",
            "--auth-token",
            "super-secret-token",
            "info",
            "node_info",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert "implicit plaintext HTTP" in combined
    assert "https://example.com:8227/" in combined
    assert "super-secret-token" not in combined


@requires_fnn_cli
def test_interactive_history_redacts_secret_values(tmp_path):
    secret = "0x" + "aa" * 32
    payment_hash = "0x" + "11" * 32
    command = (
        "invoice settle_invoice "
        f"--payment-hash {payment_hash} "
        f"--payment-preimage {secret}\n"
        "exit\n"
    )
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)

    result = subprocess.run(
        [FNN_CLI, "--url", "http://127.0.0.1:19999"],
        input=command,
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )

    history_path = tmp_path / ".fnn_cli_history"
    assert history_path.exists(), result.stderr
    history = history_path.read_text()
    assert secret not in history
    assert "--payment-preimage REDACTED" in history

    mode = stat.S_IMODE(history_path.stat().st_mode)
    assert mode == 0o600


def private_mode(path):
    return stat.S_IMODE(path.stat().st_mode)


class TestFilesystemPermissions(FiberTest):
    def test_fiber_store_paths_are_private(self):
        for fiber in (self.fiber1, self.fiber2):
            fiber_base = Path(fiber.tmp_path) / "fiber"
            store = fiber_base / "store"

            assert private_mode(fiber_base) == 0o700
            assert private_mode(store) == 0o700

            # The security property is "private" = no group/other access. Assert
            # that directly instead of a fixed 0o600, which would falsely fail on
            # RocksDB sub-directories (0o700) and is umask/platform sensitive.
            for entry in store.iterdir():
                assert private_mode(entry) & 0o077 == 0, (
                    entry,
                    oct(private_mode(entry)),
                )
