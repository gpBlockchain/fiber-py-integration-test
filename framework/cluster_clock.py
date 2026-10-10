"""Opt-in wall-clock offset shared by local FNN and CKB test processes.

Requires a platform-matching libfaketime dynamic library. The pytest process
keeps real time; callers must use ``now_ms`` when constructing block headers.
"""

import math
import os
from pathlib import Path
import platform
import shutil
import sysconfig
import tempfile
import time


def resolve_faketime_library():
    """Find the installed library, unless an explicit path was supplied."""
    explicit = os.environ.get("FIBER_TEST_FAKETIME_LIB")
    if explicit:
        library = Path(explicit).expanduser()
        if not library.is_file():
            raise FileNotFoundError(f"FIBER_TEST_FAKETIME_LIB does not exist: {library}")
        return str(library.resolve())

    system = platform.system()
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


class ClusterClock:
    def __init__(self, library_path):
        self.library_path = Path(library_path).expanduser().resolve(strict=True)
        self.system = platform.system()
        suffix = {"Darwin": ".dylib", "Linux": ".so.1"}.get(self.system)
        if suffix is None or not str(self.library_path).endswith(suffix):
            raise ValueError(f"Unsupported libfaketime library for {self.system}")

        self._directory = tempfile.TemporaryDirectory(prefix="fiber-cluster-clock-")
        self.timestamp_file = Path(self._directory.name) / "faketime.rc"
        self._offset_seconds = 0
        self._write_offset()

    def _write_offset(self):
        temporary = self.timestamp_file.with_suffix(".new")
        temporary.write_text(f"+{self._offset_seconds}\n", encoding="ascii")
        os.replace(temporary, self.timestamp_file)

    def process_env(self):
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

    def now_ms(self):
        return int(time.time() * 1000) + self._offset_seconds * 1000

    def advance_seconds(self, seconds):
        if not isinstance(seconds, int) or seconds <= 0:
            raise ValueError(
                "Clock advances must be a positive whole number of seconds"
            )
        self._offset_seconds += seconds
        self._write_offset()
        return self.now_ms()

    def advance_to_ms(self, target_ms):
        remaining_ms = target_ms - self.now_ms()
        if remaining_ms <= 0:
            raise ValueError("Target must be later than the current virtual time")
        return self.advance_seconds(math.ceil(remaining_ms / 1000))

    def close(self):
        self._directory.cleanup()
