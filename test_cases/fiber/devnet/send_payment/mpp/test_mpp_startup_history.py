"""Issue 1705：真实历史数据启动采样；SLA 未确认时只验功能并输出完整分布。

MPP1705_PERF_BUILDS 为 JSON 文件：四项 label=base/head、backend=rocksdb/sqlite，
每项含 binary(绝对路径)、commit、profile、flags；不拿一种后端冒充另一种。
"""

import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import threading
import time

from test_cases.fiber.devnet.send_payment.mpp import mpp_fixture as fixture


class TestMppStartupHistory(fixture.MppFixture):
    # TEST-MAP: MPP1705-12
    def test_startup_history_matrix_collects_all_samples(self):
        """
        1. 校验 base/head、RocksDB/SQLite 四份正式构建身份和相同编译配置。
        2. 公开接口生成 0/1000/8000 笔终态历史，固定通道并停机保存双方快照。
        3. 每个规模从同一快照复制运行十次，采集启动、真实新付款、RSS 和失败样本。
        4. 检查历史集合与余额不被重放破坏，输出分布；仅在提供确认阈值后判性能验收。
        """
        # 1. 构建 manifest 缺失或身份不符即失败，不以 skip 或当前二进制填补矩阵。
        manifest = os.environ.get("MPP1705_PERF_BUILDS")
        assert manifest, "设置 MPP1705_PERF_BUILDS 指向四份构建的 JSON manifest"
        builds = json.loads(Path(manifest).read_text())
        assert len(builds) == 4 and {(b["label"], b["backend"]) for b in builds} == {
            (label, backend)
            for label in ("base", "head")
            for backend in ("rocksdb", "sqlite")
        }
        assert (
            len({b["profile"] for b in builds}) == 1
            and builds[0]["profile"] == "release"
        )
        assert len({json.dumps(b["flags"], sort_keys=True) for b in builds}) == 1
        for build in builds:
            build["config"] = fixture.binary_config(build["binary"])
            build["version"] = subprocess.check_output(
                [build["binary"], "--version"], text=True
            ).strip()
            assert build["commit"][:7] in build["version"], build
            build["sha256"] = hashlib.sha256(
                Path(build["binary"]).read_bytes()
            ).hexdigest()
        for backend in ("rocksdb", "sqlite"):
            pair = [b for b in builds if b["backend"] == backend]
            assert (
                pair[0]["commit"] != pair[1]["commit"]
                and pair[0]["sha256"] != pair[1]["sha256"]
            )
        output = (
            Path(fixture.get_project_root())
            / "reports"
            / ("mpp1705-startup-" + time.strftime("%Y%m%dT%H%M%S"))
        )
        output.mkdir(parents=True, exist_ok=False)
        raw = output / "samples.jsonl"
        samples = []
        thresholds = (
            json.loads(os.environ["MPP1705_PERF_THRESHOLDS"])
            if os.environ.get("MPP1705_PERF_THRESHOLDS")
            else None
        )
        metadata = {k: v for k, v in builds[0].items() if k != "config"}
        self._record(
            raw,
            dict(
                kind="environment",
                machine=platform.platform(),
                cpu=platform.processor(),
                cache="warm/unflushed OS cache; no privileged cache purge",
                profile=metadata["profile"],
                flags=metadata["flags"],
                thresholds=thresholds,
                performance_acceptance=(
                    "pending" if thresholds is None else "configured"
                ),
            ),
        )

        # 2. 各构建创建独立账户/数据目录；历史全部经真实付款 API 产生。
        for build in builds:
            p = self.start_new_fiber(
                self.generate_account(30000), fiber_version=build["config"]
            )
            r = self.start_new_fiber(
                self.generate_account(30000), fiber_version=build["config"]
            )
            for f in (p, r):
                f.get_client().try_count, f.get_client().timeout = 1, 2
            edge = self._edge(p, r, 20000 * fixture.CKB)
            expected = {}
            for size in (0, 1000, 8000):
                prepare_began = time.monotonic()
                try:
                    while len(expected) < size:
                        row = self._pay(p, r)
                        assert row["payment_hash"] not in expected
                        expected[row["payment_hash"]] = row["payment_preimage"]
                    baseline = self._balances(edge)
                    assert self._history(p) == expected
                except Exception as error:
                    self._record(
                        raw,
                        dict(
                            kind="preparation_failure",
                            label=build["label"],
                            backend=build["backend"],
                            size=size,
                            created=len(expected),
                            error=repr(error),
                        ),
                    )
                    raise
                self._record(
                    raw,
                    dict(
                        kind="corpus",
                        label=build["label"],
                        backend=build["backend"],
                        size=size,
                        success=size,
                        failed=0,
                        requested_max_parts=1,
                        observed_tlcs_per_payment=1,
                        preparation_seconds=time.monotonic() - prepare_began,
                        binary_sha256=build["sha256"],
                        version=build["version"],
                        balances=baseline,
                    ),
                )
                p.stop()
                r.stop()
                self._backend_check(p, build["backend"])
                snapshot = output / f"{build['label']}-{build['backend']}-{size}"
                snapshot.mkdir()
                for name, f in (("payer", p), ("receiver", r)):
                    shutil.copytree(f.tmp_path, snapshot / name)

                # 3. 同一停机快照复制十次，失败保留原始样本并继续，不用重跑替换失败。
                for trial in range(10):
                    sample = dict(
                        kind="sample",
                        label=build["label"],
                        backend=build["backend"],
                        size=size,
                        trial=trial,
                        cache="warm/uncontrolled",
                        binary_sha256=build["sha256"],
                    )
                    try:
                        self._restore_pair(p, r, snapshot)
                        r.start(fnn_log_level="info", timeout=300)
                        sample.update(
                            self._measure_start_and_payment(
                                p, r, build, edge, baseline, expected, sample
                            )
                        )
                        sample["status"] = "passed"
                    except Exception as error:
                        sample.update(status="failed", error=repr(error))
                    finally:
                        for f in (p, r):
                            # 只清理本次启动的节点；失败不允许遗留占用下一次的端口。
                            f.force_stop()
                        self._record(raw, sample)
                        samples.append(sample)
                # 下一规模从准备快照延续，不包含十次探针的历史。
                self._restore_pair(p, r, snapshot)
                p.start(fnn_log_level="info", timeout=300)
                r.start(fnn_log_level="info", timeout=300)
                p.connect_peer(r)
            p.stop()
            r.stop()

        # 4. 功能失败与性能预算独立；阈值未定义时明确输出 pending。
        self._distributions(raw, samples, thresholds)
        assert len(samples) == 4 * 3 * 10
        assert all(s["status"] == "passed" for s in samples), f"失败样本已保留: {raw}"
        print(
            f'启动原始样本: {raw}; 性能验收: {"pending" if thresholds is None else "configured"}',
            flush=True,
        )

    @staticmethod
    def _record(path, record):
        with path.open("a") as out:
            out.write(json.dumps(record, sort_keys=True) + "\n")
            out.flush()

    def _pay(self, p, r, deadline=None):
        deadline = deadline or time.monotonic() + 120

        def remaining():
            value = deadline - time.monotonic()
            assert value > 0, "真实新付款耗尽观察窗口"
            return value

        preimage = self.generate_random_preimage()
        payment_hash = fixture.payment_hash(preimage, "ckb_hash")
        invoice = r.get_client().new_invoice(
            {
                "amount": hex(1000000),
                "currency": "Fibd",
                "payment_hash": payment_hash,
                "hash_algorithm": "ckb_hash",
            }
        )
        row = p.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "max_parts": "0x1",
                "max_fee_amount": "0x0",
            }
        )
        assert row["payment_hash"] == payment_hash
        self.wait_invoice_state(r, payment_hash, "Received", timeout=remaining())

        def single_committed():
            tlcs = [
                t
                for c in p.get_client().list_channels({})["channels"]
                for t in c["pending_tlcs"]
                if t["payment_hash"] == payment_hash
            ]
            return len(tlcs) == 1 and tlcs[0]["status"] == {"Outbound": "Committed"}

        self._wait("历史付款实际单片提交", single_committed, remaining())
        r.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )
        self.wait_payment_state(p, payment_hash, "Success", timeout=remaining())
        result = p.get_client().get_payment({"payment_hash": payment_hash})
        assert (
            result["status"] == "Success" and result.get("payment_preimage") == preimage
        )
        assert not result.get("failed_error")
        self._wait(
            "历史付款单通道无锁定",
            lambda: all(
                int(c[key], 16) == 0
                for f in (p, r)
                for c in f.get_client().list_channels({})["channels"]
                for key in ("offered_tlc_balance", "received_tlc_balance")
            ),
            remaining(),
        )
        return result

    def _history(self, payer):
        found, cursor = {}, None
        for _ in range(1000):
            params = {"limit": hex(100)}
            if cursor:
                params["after"] = cursor
            page = payer.get_client().list_payments(params)
            if not page["payments"]:
                return found
            for row in page["payments"]:
                assert row["payment_hash"] not in found and row["status"] == "Success"
                assert not row.get("failed_error") and row.get("payment_preimage")
                found[row["payment_hash"]] = row["payment_preimage"]
            assert page["last_cursor"] != cursor
            cursor = page["last_cursor"]
        raise AssertionError("历史遍历未结束")

    def _balances(self, edge):
        rows = self._open(edge)
        assert all(
            int(c[k], 16) == 0
            for c in rows
            for k in ("offered_tlc_balance", "received_tlc_balance")
        )
        return [int(c["local_balance"], 16) for c in rows]

    @staticmethod
    def _restore_pair(p, r, snapshot):
        for name, node in (("payer", p), ("receiver", r)):
            shutil.rmtree(node.tmp_path)
            shutil.copytree(snapshot / name, node.tmp_path)

    @staticmethod
    def _backend_check(node, backend):
        store = Path(node.tmp_path) / "fiber" / "store"
        if backend == "sqlite":
            with (store / "data.sqlite").open("rb") as db:
                assert db.read(16) == b"SQLite format 3\x00"
        else:
            assert (store / "CURRENT").is_file() and list(store.glob("MANIFEST-*"))
            assert not (store / "data.sqlite").exists()

    def _measure_start_and_payment(self, p, r, build, edge, baseline, expected, sample):
        log = (Path(p.tmp_path) / "node.log").open("a")
        env = {
            **os.environ,
            "FIBER_SECRET_KEY_PASSWORD": "password0",
            "RUST_LOG": "info,fnn=info",
        }
        start = time.monotonic()
        process = subprocess.Popen(
            [build["binary"], "-c", p.fiber_config_path, "-d", p.tmp_path],
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
        )
        stop = threading.Event()
        rss, errors = [], []

        def sample_rss():
            while not stop.wait(0.1):
                result = subprocess.run(
                    ["ps", "-p", str(process.pid), "-o", "rss="],
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0 and result.stdout.strip():
                    rss.append(int(result.stdout.strip()))
                elif process.poll() is None:
                    errors.append(result.stderr)

        monitor = threading.Thread(target=sample_rss, daemon=True)
        monitor.start()
        try:
            deadline = start + 300
            while True:
                assert process.poll() is None, "FNN 在 node_info 前退出"
                assert time.monotonic() < deadline, "进程启动到 node_info 超过 300 秒"
                try:
                    info = p.get_client().node_info()
                    break
                except Exception:
                    time.sleep(0.1)
            ready = time.monotonic() - start
            assert build["commit"].startswith(info["commit_hash"]) or info[
                "commit_hash"
            ].startswith(build["commit"])
            p.connect_peer(r)
            assert self._balances(edge) == baseline, "启动重放改变本金"
            result = self._pay(p, r, deadline=deadline)
            first_payment = time.monotonic() - start
            assert first_payment <= 300, "进程启动到真实新付款超过 300 秒"
            stop.set()
            monitor.join(timeout=5)
            # 计时后完整核对旧集合；不把读历史成本混入新付款时延。
            after = self._history(p)
            assert after == {
                **expected,
                result["payment_hash"]: result["payment_preimage"],
            }
            assert self._balances(edge) == [
                baseline[0] - 1000000,
                baseline[1] + 1000000,
            ]
            assert rss and not errors, errors
            return dict(
                node_info_seconds=ready,
                first_payment_seconds=first_payment,
                sampled_peak_rss_kib=max(rss),
                rss_sample_count=len(rss),
                new_payment_hash=result["payment_hash"],
            )
        finally:
            stop.set()
            monitor.join(timeout=5)
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            sample.update(
                observed_seconds=time.monotonic() - start,
                sampled_peak_rss_kib=max(rss) if rss else None,
                rss_sample_count=len(rss),
                rss_errors=errors,
            )
            log.close()

    def _distributions(self, raw, samples, thresholds):
        groups = {}
        for label in ("base", "head"):
            for backend in ("rocksdb", "sqlite"):
                for size in (0, 1000, 8000):
                    rows = [
                        s
                        for s in samples
                        if (s["label"], s["backend"], s["size"])
                        == (label, backend, size)
                    ]
                    good = [s for s in rows if s["status"] == "passed"]
                    metrics = {}
                    for key in (
                        "node_info_seconds",
                        "first_payment_seconds",
                        "sampled_peak_rss_kib",
                    ):
                        values = [s[key] for s in good]
                        metrics[key] = (
                            dict(
                                median=statistics.median(values),
                                minimum=min(values),
                                maximum=max(values),
                            )
                            if values
                            else None
                        )
                    record = dict(
                        kind="distribution",
                        label=label,
                        backend=backend,
                        size=size,
                        successful_samples=len(good),
                        failed_samples=len(rows) - len(good),
                        metrics=metrics,
                    )
                    groups[label, backend, size] = record
                    self._record(raw, record)
        for backend in ("rocksdb", "sqlite"):
            for size in (0, 1000, 8000):
                base, head = (
                    groups[label, backend, size]["metrics"]
                    for label in ("base", "head")
                )
                ratios = {
                    key: head[key]["median"] / base[key]["median"]
                    for key in base
                    if base[key] and head[key] and base[key]["median"] > 0
                }
                self._record(
                    raw,
                    dict(
                        kind="comparison",
                        backend=backend,
                        size=size,
                        median_head_base_ratio=ratios,
                        acceptance="pending" if thresholds is None else "configured",
                    ),
                )
                if thresholds:
                    for key in (
                        "node_info_seconds",
                        "first_payment_seconds",
                        "sampled_peak_rss_kib",
                    ):
                        assert (
                            head[key]
                            and head[key]["maximum"] <= thresholds["max_" + key]
                        )
                        assert ratios[key] <= thresholds["max_head_base_ratio"]
