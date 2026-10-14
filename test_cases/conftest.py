import os
import re

import pytest

REPORT_DIR = os.environ.get("FIBER_REPORT_DIR", "report")


def pytest_addoption(parser):
    parser.addoption(
        "--run-remote",
        action="store_true",
        default=False,
        help="Run tests that require access to remote authenticated Fiber nodes",
    )


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()

    if report.when == "call" and hasattr(item, "_testcase"):
        item._testcase.did_pass = report.passed

    # Preserve node logs of failed/errored cases before teardown removes tmp folders.
    # Setup failures are included: pytest skips teardown_method when setup_method raises.
    if report.failed and report.when in ("setup", "call"):
        inst = getattr(item, "instance", None)
        case_dir = os.path.join(
            REPORT_DIR, "failed-cases", re.sub(r"[^\w.-]+", "_", item.nodeid)
        )
        for fiber in getattr(inst, "fibers", None) or []:
            try:
                fiber.save_failure_data(case_dir)
            except Exception as e:
                print(f"save_failure_data failed: {e}")
