"""Opt-in wall-clock offset shared by local FNN and CKB test processes.

Requires a platform-matching libfaketime dynamic library. The pytest process
keeps real scheduling time (``time.monotonic``); callers must use ``now_ms``
when constructing block headers or reading business wall time.

This clock jumps a whole-second offset. It does not implement a faketime
rate (``x2``) and does not move backwards. Monotonic timers in Tokio/CKB
and pytest wait loops stay on real time.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
import platform
import re
import shutil
import sysconfig
import tempfile
import time
from typing import Mapping, MutableMapping, Optional

# libfaketime offset file: only "+<non-negative seconds>" is accepted.
_OFFSET_RE = re.compile(r"^\+(\d+)$")

# Process-scoped registry so host-clock helpers can refuse to mix mechanisms.
_ACTIVE_CLOCKS: list["ClusterClock"] = []


def scheduling_now():
    """Real elapsed time for waits, sleeps, and RPC timeouts."""
    return time.monotonic()


def resolve_faketime_library():
    """Find the installed library, unless an explicit path was supplied."""
    explicit = os.environ.get("FIBER_TEST_FAKETIME_LIB")
    if explicit:
        library = Path(explicit).expanduser()
        if not library.is_file():
            raise FileNotFoundError(
                f"FIBER_TEST_FAKETIME_LIB does not exist: {library}"
            )
        return str(library.resolve())

    # CI prepare ships a jemalloc-safe build with the test binaries.
    root = Path(__file__).resolve().parents[1]
    system = platform.system()
    bundled = (
        root
        / "download"
        / "libfaketime"
        / ("libfaketime.1.dylib" if system == "Darwin" else "libfaketime.so.1")
    )
    if bundled.is_file():
        return str(bundled.resolve())

    if system == "Darwin":
        prefixes = [os.environ.get("HOMEBREW_PREFIX")]
        brew = shutil.which("brew")
        if brew:
            prefixes.append(str(Path(brew).parent.parent))
        prefixes.extend(("/opt/homebrew", "/usr/local"))
        for prefix in dict.fromkeys(filter(None, prefixes)):
            install_dir = Path(prefix) / "opt" / "libfaketime"
            if install_dir.is_dir():
                for library in install_dir.rglob("libfaketime.1.dylib"):
                    if library.is_file():
                        return str(library.resolve())
        install_hint = "brew install libfaketime"
    elif system == "Linux":
        multiarch = sysconfig.get_config_var("MULTIARCH")
        directories = [Path("/usr/lib/faketime"), Path("/usr/local/lib/faketime")]
        if multiarch:
            directories.insert(0, Path("/usr/lib") / multiarch / "faketime")
        for directory in directories:
            library = directory / "libfaketime.so.1"
            if library.is_file():
                return str(library.resolve())
        install_hint = "sudo apt-get install libfaketime"
    else:
        raise RuntimeError(f"Unsupported libfaketime platform: {system}")

    raise FileNotFoundError(
        f"libfaketime was not found on {system}. Install it with "
        f"`{install_hint}` or set FIBER_TEST_FAKETIME_LIB to its library path."
    )


def parse_offset_text(value: str) -> int:
    """Parse a timestamp-file body. Rate and absolute faketime formats are rejected."""
    text = value.strip()
    match = _OFFSET_RE.fullmatch(text)
    if not match:
        raise ValueError(
            f"Invalid cluster clock offset: {value!r}. Only a whole-second "
            "offset like '+14400' is supported; libfaketime rate (x2) and "
            "absolute (@...) formats are not used."
        )
    return int(match.group(1))


def has_active_cluster_clock() -> bool:
    return bool(_ACTIVE_CLOCKS)


def require_real_host_clock(action: str = "this operation"):
    """Refuse helpers that change the host clock while a ClusterClock is live."""
    if _ACTIVE_CLOCKS:
        raise RuntimeError(
            f"{action} cannot be mixed with ClusterClock. "
            "Use cluster_clock.advance_seconds() or "
            "BasicClockFiber.advance_time_by() for business time, and keep "
            "pytest waits on real monotonic time."
        )


class ClockPropagationError(RuntimeError):
    """Raised when a jump could not be applied or verified on every backend."""


class ClusterClock:
    def __init__(self, library_path, *, timestamp_file=None, reuse=False):
        self.library_path = Path(library_path).expanduser().resolve(strict=True)
        self.system = platform.system()
        suffix = {"Darwin": ".dylib", "Linux": ".so.1"}.get(self.system)
        if suffix is None or not str(self.library_path).endswith(suffix):
            raise ValueError(f"Unsupported libfaketime library for {self.system}")

        if reuse and timestamp_file is None:
            raise ValueError("Reusing a clock requires a persistent timestamp file")
        self._directory = None
        if timestamp_file is None:
            self._directory = tempfile.TemporaryDirectory(prefix="fiber-cluster-clock-")
            self.timestamp_file = Path(self._directory.name) / "faketime.rc"
        else:
            self.timestamp_file = Path(timestamp_file).expanduser().resolve()
            self.timestamp_file.parent.mkdir(parents=True, exist_ok=True)

        if reuse:
            value = self.timestamp_file.read_text(encoding="ascii")
            self._offset_seconds = parse_offset_text(value)
        else:
            self._offset_seconds = 0
            self._write_offset()
        self._backends = []
        _ACTIVE_CLOCKS.append(self)

    @property
    def offset_seconds(self) -> int:
        return self._offset_seconds

    def _write_offset(self):
        temporary = self.timestamp_file.with_suffix(".new")
        temporary.write_text(f"+{self._offset_seconds}\n", encoding="ascii")
        os.replace(temporary, self.timestamp_file)

    def process_env(self):
        """Clock variables only; use merge_env to combine with a process environment."""
        env = {
            "FAKETIME_TIMESTAMP_FILE": str(self.timestamp_file),
            "FAKETIME_NO_CACHE": "1",
            # macOS sandboxed runners may reject shm_open; the timestamp file
            # remains the shared source of truth for already-running nodes.
            "FAKETIME_DISABLE_SHM": "1",
            # Keep Tokio/CKB scheduling on real monotonic time. This clock
            # advances wall time; it does not accelerate existing timers.
            "FAKETIME_DONT_FAKE_MONOTONIC": "1",
        }
        if self.system == "Darwin":
            env.update(
                DYLD_INSERT_LIBRARIES=str(self.library_path),
                DYLD_FORCE_FLAT_NAMESPACE="1",
            )
        else:
            env["LD_PRELOAD"] = str(self.library_path)
        return env

    def file_clock_env(self):
        """Timestamp-file variables only; no LD_PRELOAD (for static LND)."""
        return {
            "FAKETIME_TIMESTAMP_FILE": str(self.timestamp_file),
            "FAKETIME_NO_CACHE": "1",
        }

    def attach(self, backend):
        """Register a component backend and apply the current offset to it."""
        if backend not in self._backends:
            self._backends.append(backend)
        if self._offset_seconds > 0:
            backend.propagate(self)
        return backend

    def propagate(self):
        """Re-apply the current offset to every attached backend.

        Used after a jump and after a component restart (bitcoind
        setmocktime does not survive restart). Partial failures raise
        ClockPropagationError; the offset file is not rolled back
        (advances are one-way).
        """
        errors = []
        for backend in list(self._backends):
            try:
                backend.propagate(self)
            except Exception as exc:  # noqa: BLE001 - collect all backends
                name = getattr(backend, "name", lambda: type(backend).__name__)()
                errors.append(f"{name}: {exc}")
        if errors:
            raise ClockPropagationError(
                "ClusterClock did not propagate to every component: "
                + "; ".join(errors)
            )

    def apply_to_env(self, env: MutableMapping[str, str]) -> MutableMapping[str, str]:
        """Inject clock vars into an extra_env mapping without copying os.environ."""
        clock_env = self.process_env()
        for key in ("LD_PRELOAD", "DYLD_INSERT_LIBRARIES"):
            injected = clock_env.get(key)
            existing = env.get(key)
            if injected and existing and injected not in existing.split(":"):
                clock_env[key] = f"{injected}:{existing}"
        env.update(clock_env)
        return env

    def merge_env(
        self, base: Optional[Mapping[str, str]] = None, **overrides
    ) -> MutableMapping[str, str]:
        """Copy ``base`` (default ``os.environ``) and inject libfaketime safely.

        Existing LD_PRELOAD / DYLD_INSERT_LIBRARIES values are preserved by
        prepending the faketime library so child processes still inherit both.
        """
        env = dict(os.environ if base is None else base)
        self.apply_to_env(env)
        env.update(overrides)
        return env

    def now_ms(self):
        """Business wall time in milliseconds (real UTC + offset)."""
        return int(time.time() * 1000) + self._offset_seconds * 1000

    def now_s(self):
        """Business wall time in whole seconds."""
        return self.now_ms() // 1000

    def advance_seconds(self, seconds):
        if not isinstance(seconds, int) or seconds <= 0:
            raise ValueError(
                "Clock advances must be a positive whole number of seconds"
            )
        self._offset_seconds += seconds
        self._write_offset()
        self.propagate()
        return self.now_ms()

    def advance_to_ms(self, target_ms):
        remaining_ms = target_ms - self.now_ms()
        if remaining_ms <= 0:
            raise ValueError("Target must be later than the current virtual time")
        return self.advance_seconds(math.ceil(remaining_ms / 1000))

    def close(self):
        if self in _ACTIVE_CLOCKS:
            _ACTIVE_CLOCKS.remove(self)
        if self._directory is not None:
            self._directory.cleanup()
            self._directory = None
