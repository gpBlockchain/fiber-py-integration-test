"""ClusterClock backends for components that cannot use libfaketime.

Official bitcoind honors ``setmocktime`` (frozen, not persistent across
restart). Official LND is statically linked and ignores LD_PRELOAD; CCH
tests that enable ClusterClock must run a test-only file-offset-clock
build of LND that reads the same +N timestamp file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from framework.cluster_clock import ClockPropagationError, ClusterClock
from framework.util import get_project_root


def resolve_lnd_file_clock_binaries():
    """Return (lnd, lncli) for the test-only file-offset-clock build."""
    explicit = Path(
        __import__("os").environ.get("FIBER_TEST_LND_FILE_CLOCK_BIN", "")
    ).expanduser()
    if explicit.is_file():
        lncli = explicit.parent / "lncli"
        _require_features(explicit.parent)
        if not lncli.is_file():
            raise FileNotFoundError(f"lncli not found next to {explicit}")
        return str(explicit.resolve()), str(lncli.resolve())

    dest = Path(get_project_root()) / "download" / "lnd-file-clock"
    lnd = dest / "lnd"
    lncli = dest / "lncli"
    if not lnd.is_file() or not lncli.is_file():
        raise FileNotFoundError(
            "ClusterClock CCH tests need the test-only LND file-offset-clock "
            "binary (official LND ignores libfaketime). Build it with "
            "tools/build_lnd_file_clock.sh or set FIBER_TEST_LND_FILE_CLOCK_BIN."
        )
    _require_features(dest)
    return str(lnd.resolve()), str(lncli.resolve())


def _require_features(directory: Path):
    features = directory / "FEATURES"
    if not features.is_file() or "file-offset-clock" not in features.read_text():
        raise FileNotFoundError(
            f"{directory} is not a file-offset-clock LND build "
            "(missing FEATURES marker). Official LND cannot jump wall time."
        )


def apply_lnd_file_clock_env(lnd_node, clock: ClusterClock):
    """Inject the timestamp file into LND without LD_PRELOAD."""
    env = getattr(lnd_node, "extra_env", None)
    if env is None:
        lnd_node.extra_env = {}
        env = lnd_node.extra_env
    env.update(clock.file_clock_env())
    env.pop("LD_PRELOAD", None)
    env.pop("DYLD_INSERT_LIBRARIES", None)
    return env


class BitcoindMocktimeBackend:
    """Apply ClusterClock to bitcoind via setmocktime.

    setmocktime is frozen (the daemon does not tick) and is lost on
    restart; callers must propagate again after bitcoind starts. Offset 0
    does not call setmocktime, so default-mode time stays real and moving.
    """

    def __init__(self, btc_node):
        self.btc_node = btc_node

    def name(self) -> str:
        return f"bitcoind:{getattr(self.btc_node, 'tmp_path', '?')}"

    def propagate(self, clock: ClusterClock):
        if clock.offset_seconds <= 0:
            return
        unix = clock.now_s()
        self.btc_node.set_mocktime(unix)
        observed = self.btc_node.get_mocktime_seconds()
        if observed is None or abs(observed - unix) > 2:
            raise ClockPropagationError(
                f"bitcoind setmocktime({unix}) did not stick; observed={observed}"
            )


class LndFileOffsetBackend:
    """Verify a file-offset-clock LND invoice timestamp after a jump."""

    def __init__(self, lnd_node, invoice_fn: Optional[Callable[[], Mapping]] = None):
        self.lnd_node = lnd_node
        self.invoice_fn = invoice_fn

    def name(self) -> str:
        return f"lnd:{getattr(self.lnd_node, 'rpc_port', '?')}"

    def propagate(self, clock: ClusterClock):
        if clock.offset_seconds <= 0:
            return
        decode = self._decode_probe_invoice()
        timestamp = int(decode["timestamp"])
        expected = clock.now_s()
        if abs(timestamp - expected) > 3:
            raise ClockPropagationError(
                f"LND invoice timestamp {timestamp} is not on ClusterClock "
                f"(expected ~{expected}, host would be ~{int(__import__('time').time())}). "
                "Official LND binaries cannot jump; use the file-offset-clock build."
            )

    def _decode_probe_invoice(self) -> Mapping[str, Any]:
        if self.invoice_fn is not None:
            return self.invoice_fn()
        invoice = self.lnd_node.addinvoice(amt=1000, memo="cluster-clock-probe")
        return self.lnd_node.ln_cli_with_cmd(
            f"decodepayreq {invoice['payment_request']}"
        )
