#!/usr/bin/env python3
"""Native pytest entry for Issue 1705, including isolated port/data allocation.

Use --group fast/slow/upgrade/batch/performance/all. Slow uses real expiry.
Performance requires --performance-builds and never silently skips missing builds.
"""

import argparse
import fcntl
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--group",
        nargs="+",
        choices=("fast", "slow", "upgrade", "batch", "performance", "all"),
        default=["fast"],
    )
    parser.add_argument("--port-offset", type=int, default=0)
    parser.add_argument("--base-fnn", default=str(ROOT / "download/fiber/current/fnn"))
    parser.add_argument(
        "--head-fnn", default=str(ROOT / "download/fiber/fix-tp-mpp/fnn")
    )
    parser.add_argument("--performance-builds")
    parser.add_argument("--junitxml")
    parser.add_argument("--collect-only", action="store_true")
    args = parser.parse_args()
    assert 0 <= args.port_offset <= 20000
    # 现有 ckb-cli 辅助使用固定 /tmp/tmp.data；端口隔离不等于可并行运行。
    guard = open("/tmp/mpp1705-public-cli.lock", "a")
    try:
        fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("另一个 MPP1705 运行器正在使用公共 CLI 临时文件，请串行运行")
    os.chdir(ROOT)
    os.environ["MPP1705_BASE_FNN"] = args.base_fnn
    if args.performance_builds:
        os.environ["MPP1705_PERF_BUILDS"] = str(Path(args.performance_builds).resolve())
    import pytest
    from test_cases.fiber.devnet.send_payment.mpp import mpp_fixture as fixture
    from test_cases.fiber.devnet.trampoline_routing import (
        test_trampoline_timeout_refund as refund,
    )
    from test_cases.fiber.devnet.trampoline_routing import (
        test_trampoline_timeout_refund_restart as restart,
    )

    config = fixture.binary_config(args.head_fnn)
    # 07/08 的额外节点辅助默认参数引用此配置对象；同批所有节点使用所选 head。
    from framework.test_fiber import FiberConfigPath

    FiberConfigPath.CURRENT_DEV.fiber_bin_path = config.fiber_bin_path
    for cls in (
        fixture.MppFixture,
        refund.TestTrampolineTimeoutRefund,
        restart.TestTrampolineTimeoutRefundRestart,
    ):
        cls.fiber_version = config
        cls.tmp_path_name = f"tmp/mpp1705-run-{args.port_offset}"
        for name, value in (
            ("ckb_rpc_port", 18114),
            ("ckb_p2p_port", 18125),
            ("fiber1_rpc_port", 18228),
            ("fiber1_p2p_port", 18227),
            ("fiber2_rpc_port", 18229),
            ("fiber2_p2p_port", 18230),
            ("extra_fiber_rpc_port", 18251),
            ("extra_fiber_p2p_port", 18402),
        ):
            setattr(cls, name, value + args.port_offset)
    base = "test_cases/fiber/devnet/send_payment/mpp/"
    failure = base + "test_mpp_failure_boundaries.py::TestMppFailureBoundaries::"
    groups = {
        "fast": [
            base + "test_mpp_success_recovery.py::TestMppSuccessRecovery",
            failure + "test_unknown_chain_results_do_not_refund_upstream",
            failure + "test_first_failed_split_waits_for_isolated_split_result",
        ],
        "slow": [
            "test_cases/fiber/devnet/trampoline_routing/test_trampoline_timeout_refund.py",
            "test_cases/fiber/devnet/trampoline_routing/test_trampoline_timeout_refund_restart.py",
            failure + "test_direct_timeout_refund_then_sender_restart_and_new_payment",
        ],
        "upgrade": [base + "test_mpp_success_recovery.py::TestMppUpgrade"],
        "batch": [base + "test_mpp_batch_recovery.py"],
        "performance": [base + "test_mpp_startup_history.py"],
    }
    targets = (
        sum(groups.values(), [])
        if "all" in args.group
        else sum((groups[g] for g in args.group), [])
    )
    assert len(targets) == len(set(targets)), "运行组不要重复"
    argv = targets + ["-q", "--log-cli-level=WARNING"]
    if args.junitxml:
        argv += ["--junitxml", str(Path(args.junitxml).resolve())]
    if args.collect_only:
        argv.append("--collect-only")
    return pytest.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
