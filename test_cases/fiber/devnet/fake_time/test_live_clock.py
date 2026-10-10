"""Process-scoped fake-time smoke test; future devnet time cases live here."""

import os
import select
import subprocess
import sys

import pytest

from framework.basic_clock_fiber import BasicClockFiber
from framework.cluster_clock import ClusterClock, resolve_faketime_library


def test_real_library_advances_live_process():
    """CI smoke: an already-running child sees the same four-hour jump."""
    try:
        library = resolve_faketime_library()
    except FileNotFoundError as exc:
        pytest.skip(str(exc))

    clock = ClusterClock(library)
    program = (
        "import sys, time\n"
        "for _ in sys.stdin:\n"
        "    print(int(time.time()), flush=True)\n"
    )
    env = os.environ.copy()
    env.update(clock.process_env())
    child = subprocess.Popen(
        [sys.executable, "-u", "-c", program],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )

    def read_time():
        child.stdin.write("tick\n")
        child.stdin.flush()
        readable, _, _ = select.select([child.stdout], [], [], 5)
        assert readable, "time probe did not respond in five real seconds"
        line = child.stdout.readline()
        assert line, f"time probe exited: {child.stderr.read()}"
        return int(line.strip())

    try:
        before = read_time()
        clock.advance_seconds(BasicClockFiber.EPOCH_SECONDS)
        after = read_time()
        assert 14_395 <= after - before <= 14_405
    finally:
        child.terminate()
        child.wait(timeout=5)
        clock.close()
