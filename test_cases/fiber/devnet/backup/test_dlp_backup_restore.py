"""Regression suite for fiber PR #1197 — Data Loss Protection (DLP).

PR features:
  * RPC `info.backup_now()` — manually trigger a backup (no args; backups go
    under `<fiber_base_dir>/backups/<timestamp_ms>/`).
  * CLI `fnn --restore <BACKUP_PATH>` — restore from disk and exit, marking
    every previously-open channel as `ChannelState::Stale`.
  * `StoreActor` — periodic + event-driven backups; on startup the first
    deadline is forced ~now so a backup is produced shortly after boot.
  * `ChannelState::Stale` — passive audit handshake on reestablish.

History note: the initial PR shipped with a "Backup directory already
exists" sequencing bug that prevented the RocksDB checkpoint from being
created. The fix landed as commits `de6e38ca` (reorder + per-call timestamp
subdir) and `d583933e` (drop `target_path` arg from `backup_now`); these
tests target that final API.
"""

import os
import time

import pytest

from framework.basic_fiber import FiberTest
from framework.util import get_project_root, run_command


def _backups_root(fiber) -> str:
    """The parent directory that holds per-call timestamped backups."""
    return os.path.join(fiber.tmp_path, "fiber", "backups")


def _list_backups(fiber) -> list:
    root = _backups_root(fiber)
    if not os.path.isdir(root):
        return []
    entries = []
    for name in os.listdir(root):
        full = os.path.join(root, name)
        if os.path.isdir(full):
            entries.append(full)
    entries.sort(key=lambda p: os.path.getmtime(p))
    return entries


def _latest_backup(fiber) -> str:
    entries = _list_backups(fiber)
    return entries[-1] if entries else ""


def _wait_for_backup_with_db(fiber, timeout: int = 60) -> str:
    """Wait until at least one `<backups>/<ts>/db/` checkpoint exists. Returns
    that backup path, or empty string on timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for bak in reversed(_list_backups(fiber)):
            if os.path.isdir(os.path.join(bak, "db")):
                return bak
        time.sleep(1)
    return ""


def _trigger_backup_now(fiber) -> str:
    """Call the `backup_now` RPC (no args) and return the produced backup dir."""
    before = set(_list_backups(fiber))
    fiber.get_client().call("backup_now", [])
    deadline = time.time() + 30
    while time.time() < deadline:
        current = set(_list_backups(fiber))
        new_dirs = current - before
        for bak in sorted(new_dirs, key=os.path.getmtime, reverse=True):
            if os.path.isdir(os.path.join(bak, "db")):
                return bak
        time.sleep(1)
    return ""


def _run_restore(fiber, backup_path: str) -> None:
    """Stop fiber, run `fnn --restore <bak>` (which exits when done), restart."""
    fiber.stop()
    # Give RocksDB a moment to release file handles before re-opening for restore.
    time.sleep(2)
    fnn_bin = f"{get_project_root()}/{fiber.fiber_config_enum.fiber_bin_path}"
    cmd = (
        f"FIBER_SECRET_KEY_PASSWORD='password0' "
        f"RUST_LOG=info,fnn=info "
        f"{fnn_bin} -c {fiber.fiber_config_path} -d {fiber.tmp_path} "
        f"--restore {backup_path} 2>&1"
    )
    # Use a plain subprocess call so we can surface fnn's output on failure.
    import subprocess

    completed = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if completed.returncode != 0:
        raise AssertionError(
            f"fnn --restore failed (exit={completed.returncode}):\n"
            f"{completed.stdout}"
        )
    fiber.start()


def _wait_state(client, pubkey, expected: str, timeout: int = 90) -> str:
    """Poll list_channels until first channel reaches `expected` state."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        chs = client.list_channels({"pubkey": pubkey, "include_closed": False})[
            "channels"
        ]
        if chs:
            last = chs[0]["state"]["state_name"]
            if last == expected:
                return last
        time.sleep(2)
    return last or "<no channel>"


@pytest.mark.skip("https://github.com/nervosnetwork/fiber/pull/1197")
class TestDlpBackupAndRestore(FiberTest):
    """Regression suite for nervosnetwork/fiber PR #1197."""

    # ---------------------------------------------------------------- backup

    def test_store_actor_backs_up_key_files(self):
        """The auto-backup wiring must copy `key` and `sk` into the timestamped
        backup directory shortly after node startup."""
        self.open_channel(self.fiber1, self.fiber2, 200 * 100000000, 100 * 100000000)

        bak = _wait_for_backup_with_db(self.fiber1, timeout=60)
        assert bak, "no backup produced under <base>/backups/<timestamp>/db"
        assert os.path.isfile(os.path.join(bak, "key")), f"missing {bak}/key"
        assert os.path.isfile(os.path.join(bak, "sk")), f"missing {bak}/sk"

    def test_store_actor_creates_rocksdb_checkpoint(self):
        """Auto-backup should produce a RocksDB checkpoint at
        `<base>/backups/<timestamp>/db/`."""
        self.open_channel(self.fiber1, self.fiber2, 200 * 100000000, 0)
        bak = _wait_for_backup_with_db(self.fiber1, timeout=60)
        assert bak, "no RocksDB checkpoint produced under <base>/backups/<ts>/db"
        # Sanity-check: a RocksDB checkpoint must have CURRENT + at least one SST or MANIFEST.
        db_dir = os.path.join(bak, "db")
        contents = os.listdir(db_dir)
        assert (
            "CURRENT" in contents
        ), f"checkpoint at {db_dir} missing CURRENT: {contents}"

    def test_backup_now_rpc_returns_success(self):
        """Manual `backup_now` (no args) must return success and produce a
        new timestamped backup containing key/sk/db."""
        self.open_channel(self.fiber1, self.fiber2, 200 * 100000000, 0)
        bak = _trigger_backup_now(self.fiber1)
        assert bak, "backup_now did not produce a new backup directory"
        assert os.path.isdir(os.path.join(bak, "db"))
        assert os.path.isfile(os.path.join(bak, "key"))
        assert os.path.isfile(os.path.join(bak, "sk"))

    def test_backup_now_rpc_is_registered(self):
        """Sanity check that the RPC is registered (no MethodNotFound) and
        the node continues serving other RPCs afterwards."""
        self.open_channel(self.fiber1, self.fiber2, 200 * 100000000, 0)
        # Must not raise MethodNotFound; either succeeds or returns a
        # backup-domain error.
        try:
            self.fiber1.get_client().call("backup_now", [])
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            assert "method not found" not in msg, f"backup_now not registered: {exc}"
        assert self.fiber1.get_client().node_info()["pubkey"]

    # --------------------------------------------------------------- restore

    def test_restore_marks_channel_stale(self):
        """`fnn --restore <bak>` must mark the previously open channel as
        `Stale`. By design `is_risk_of_penalty()` returns true for any channel
        in `ChannelReady` / `ShuttingDown`.

        Per PR #1197 author: when commitment numbers match on both sides, the
        peer-initiated `ReestablishChannel` immediately audits the channel
        back to `ChannelReady` — Stale is only stably observable while the
        peer is offline (no audit handshake in flight). We isolate fiber2
        across the restore to capture that window."""
        self.open_channel(self.fiber1, self.fiber2, 200 * 100000000, 100 * 100000000)

        bak = _trigger_backup_now(self.fiber1)
        assert bak, "backup_now did not produce a new backup directory"

        # Cache fiber2's pubkey now — once force_stopped, its RPC is unreachable.
        fiber2_pubkey = self.fiber2.get_pubkey()

        # Take the peer offline so its reestablish handshake cannot auto-heal
        # the channel back to ChannelReady immediately after restore.
        self.fiber2.force_stop()
        time.sleep(2)

        _run_restore(self.fiber1, bak)

        chs = self.fiber1.get_client().list_channels(
            {"pubkey": fiber2_pubkey, "include_closed": False}
        )["channels"]
        assert len(chs) == 1, f"expected 1 channel after restore, got {chs}"
        assert (
            chs[0]["state"]["state_name"] == "Stale"
        ), f"expected Stale, got {chs[0]['state']}"

        # Bring fiber2 back up so teardown can shut it down cleanly.
        self.fiber2.start()

    def test_restore_marks_channel_stale_after_payment(self):
        """Stronger Stale precondition: peer has actually advanced its
        commitment number via a payment between backup and restore."""
        self.open_channel(self.fiber1, self.fiber2, 500 * 100000000, 200 * 100000000)

        bak = _trigger_backup_now(self.fiber1)
        assert bak, "backup_now did not produce a new backup directory"

        payment_hash = self.send_payment(self.fiber1, self.fiber2, 5 * 100000000)
        self.wait_payment_state(self.fiber1, payment_hash, "Success")

        _run_restore(self.fiber1, bak)

        chs = self.fiber1.get_client().list_channels(
            {"pubkey": self.fiber2.get_pubkey(), "include_closed": False}
        )["channels"]
        assert len(chs) == 1, f"expected 1 channel after restore, got {chs}"
        assert (
            chs[0]["state"]["state_name"] == "Stale"
        ), f"expected Stale, got {chs[0]['state']}"

    def test_restore_then_peer_reestablish_resumes_channel_ready(self):
        """Design (per PR #1197 author): a Stale node never proactively sends
        `ReestablishChannel` — that would leak data-loss status and invite a
        sniping attack. Recovery requires the peer (which still has fresh
        state) to initiate the handshake. When commitment numbers match on
        both sides the audit succeeds immediately and the channel transitions
        back to `ChannelReady`.

        We take fiber2 offline across the restore so we can observe the
        Stale window, then bring fiber2 back up to trigger the
        peer-initiated audit and assert recovery."""
        self.open_channel(self.fiber1, self.fiber2, 200 * 100000000, 100 * 100000000)

        bak = _trigger_backup_now(self.fiber1)
        assert bak, "backup_now did not produce a new backup directory"

        # Cache fiber2's pubkey now — once force_stopped, its RPC is unreachable.
        fiber2_pubkey = self.fiber2.get_pubkey()

        # Isolate fiber2 so the post-restore Stale window is observable.
        self.fiber2.force_stop()
        time.sleep(2)

        _run_restore(self.fiber1, bak)

        chs = self.fiber1.get_client().list_channels(
            {"pubkey": fiber2_pubkey, "include_closed": False}
        )["channels"]
        assert len(chs) == 1, f"expected 1 channel after restore, got {chs}"
        assert chs[0]["state"]["state_name"] == "Stale"

        # Bring fiber2 back; on reconnect it initiates ReestablishChannel and
        # fiber1 audits its Stale channel back to ChannelReady.
        self.fiber2.start()
        self.fiber2.connect_peer(self.fiber1)

        final = _wait_state(
            self.fiber1.get_client(),
            fiber2_pubkey,
            "ChannelReady",
            timeout=120,
        )
        assert (
            final == "ChannelReady"
        ), f"channel did not resume after peer-initiated audit, final state={final}"

        # Sanity check: payment should now work end-to-end.
        payment_hash = self.send_payment(self.fiber1, self.fiber2, 5 * 100000000)
        self.wait_payment_state(self.fiber1, payment_hash, "Success")
