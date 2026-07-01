import os
import time

from framework.basic_fiber import FiberTest
import concurrent.futures

from framework.config import DEFAULT_MIN_DEPOSIT_CKB
from framework.test_wasm_fiber import WasmFiber


def _collect_stable_futures(
    pending_set,
    completed_tasks,
    failed_tasks,
    tasks_submitted,
    start_time,
    logger,
    times,
    completed_counts,
    wait_all=False,
    timeout=0,
):
    if not pending_set:
        return pending_set, completed_tasks, failed_tasks, 0
    done, pending_set = concurrent.futures.wait(
        pending_set,
        timeout=timeout,
        return_when=(
            concurrent.futures.ALL_COMPLETED
            if wait_all
            else concurrent.futures.FIRST_COMPLETED
        ),
    )
    for future in done:
        completed_tasks += 1
        try:
            future.result()
            logger.debug(f"Task {completed_tasks}/{tasks_submitted} completed")
        except Exception as e:
            failed_tasks += 1
            logger.error(f"Task {completed_tasks}/{tasks_submitted} failed: {e}")
        elapsed_time = time.time() - start_time
        times.append(elapsed_time)
        completed_counts.append(completed_tasks)
    return pending_set, completed_tasks, failed_tasks, len(done)


class TestStableStress(FiberTest):
    fnn_log_level = "info"

    # def test_conn(self):
    #     self.
    def test_stable_stress(self):
        """
        a->b->c->d(wasm)->a
            1. a->a  ckb and udt
            2. b->b  ckb and udt
            3. c->c  ckb and udt
            4. d->d  ckb and udt (wasm node)
        Returns:
        """
        self.fiber3 = self.start_new_fiber(
            self.generate_account(10000, self.fiber1.account_private, 10000 * 100000000)
        )

        # Create wasm fiber4 (d node)
        account_private_wasm = self.generate_account(
            10000, self.fiber1.account_private, 10000 * 100000000
        )
        WasmFiber.reset()
        self.fiber4 = WasmFiber(
            account_private_wasm,
            "0201010101010101010101010101010101010101010101010101010101010101",
            "devnet",
        )
        self.fiber4.account_private = account_private_wasm
        self.fibers.append(self.fiber4)

        self.faucet(
            self.fiber2.account_private,
            0,
            self.fiber1.account_private,
            10000 * 100000000,
        )
        self.faucet(
            self.fiber1.account_private,
            0,
            self.fiber1.account_private,
            10000 * 100000000,
        )
        # a <-> b (CKB + UDT)
        self.open_channel(
            self.fiber1, self.fiber2, 1000 * 100000000, 1000 * 100000000, 0, 0
        )
        self.open_channel(
            self.fiber1,
            self.fiber2,
            1000 * 100000000,
            1000 * 100000000,
            0,
            0,
            self.get_account_udt_script(self.fiber1.account_private),
        )

        # b <-> c (CKB + UDT)
        self.open_channel(
            self.fiber2, self.fiber3, 1000 * 100000000, 1000 * 100000000, 0, 0
        )
        self.open_channel(
            self.fiber2,
            self.fiber3,
            1000 * 100000000,
            1000 * 100000000,
            0,
            0,
            self.get_account_udt_script(self.fiber1.account_private),
        )

        # c <-> d(wasm) (CKB + UDT) — wasm as fiber1 to avoid get_pubkey issue
        self.open_channel(
            self.fiber4, self.fiber3, 1000 * 100000000, 1000 * 100000000, 0, 0
        )
        self.open_channel(
            self.fiber4,
            self.fiber3,
            1000 * 100000000,
            1000 * 100000000,
            0,
            0,
            self.get_account_udt_script(self.fiber1.account_private),
        )

        # d(wasm) <-> a (CKB + UDT)
        self.open_channel(
            self.fiber4, self.fiber1, 1000 * 100000000, 1000 * 100000000, 0, 0
        )
        self.open_channel(
            self.fiber4,
            self.fiber1,
            1000 * 100000000,
            1000 * 100000000,
            0,
            0,
            self.get_account_udt_script(self.fiber1.account_private),
        )

        time.sleep(10)
        udt = self.get_account_udt_script(self.fiber1.account_private)
        duration_seconds = int(os.environ.get("STABLE_DURATION", 300))
        tasks_submitted = 0
        start_time = time.time()
        times = []
        completed_counts = []
        max_inflight = 16
        completed_tasks = 0
        failed_tasks = 0
        last_progress_time = start_time
        no_progress_timeout = int(os.environ.get("STABLE_NO_PROGRESS_TIMEOUT", 300))
        final_drain_timeout = int(os.environ.get("STABLE_FINAL_DRAIN_TIMEOUT", 300))

        tps_interval = 10  # print TPS every 10 seconds
        last_tps_time = start_time
        last_tps_completed = 0

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=16)
        pending = set()
        try:
            while time.time() - start_time < duration_seconds:
                # Drain completed futures to keep in-flight count bounded
                while len(pending) >= max_inflight:
                    (
                        pending,
                        completed_tasks,
                        failed_tasks,
                        finished_count,
                    ) = _collect_stable_futures(
                        pending,
                        completed_tasks,
                        failed_tasks,
                        tasks_submitted,
                        start_time,
                        self.logger,
                        times,
                        completed_counts,
                        timeout=1,
                    )
                    if finished_count > 0:
                        last_progress_time = time.time()
                    elif time.time() - last_progress_time >= no_progress_timeout:
                        raise TimeoutError(
                            f"stable test stuck: {len(pending)} tasks pending, "
                            f"no task completed for {no_progress_timeout}s"
                        )

                # Submit one round of payment tasks (8 tasks)
                pending.add(
                    executor.submit(self.send_payment, self.fiber1, self.fiber1, 1)
                )
                pending.add(
                    executor.submit(
                        self.send_payment, self.fiber1, self.fiber1, 1, True, udt
                    )
                )
                pending.add(
                    executor.submit(self.send_payment, self.fiber2, self.fiber2, 1)
                )
                pending.add(
                    executor.submit(
                        self.send_payment, self.fiber2, self.fiber2, 1, True, udt
                    )
                )
                pending.add(
                    executor.submit(self.send_payment, self.fiber3, self.fiber3, 1)
                )
                pending.add(
                    executor.submit(
                        self.send_payment, self.fiber3, self.fiber3, 1, True, udt
                    )
                )
                pending.add(
                    executor.submit(self.send_payment, self.fiber4, self.fiber4, 1)
                )
                pending.add(
                    executor.submit(
                        self.send_payment, self.fiber4, self.fiber4, 1, True, udt
                    )
                )
                tasks_submitted += 8

                # Periodic TPS report
                now = time.time()
                if now - last_tps_time >= tps_interval:
                    elapsed = now - start_time
                    interval_completed = completed_tasks - last_tps_completed
                    interval_duration = now - last_tps_time
                    interval_tps = (
                        interval_completed / interval_duration
                        if interval_duration > 0
                        else 0
                    )
                    overall_tps = completed_tasks / elapsed if elapsed > 0 else 0
                    self.logger.info(
                        f"[TPS] elapsed={elapsed:.0f}s | interval({tps_interval}s): {interval_tps:.2f} tps | "
                        f"overall: {overall_tps:.2f} tps | completed={completed_tasks} submitted={tasks_submitted} in-flight={len(pending)}"
                    )
                    last_tps_time = now
                    last_tps_completed = completed_tasks

            # Duration reached — drain remaining in-flight tasks with timeout
            self.logger.info(
                f"Duration {duration_seconds}s reached. Waiting for {len(pending)} remaining tasks..."
            )
            drain_deadline = time.time() + final_drain_timeout
            while pending and time.time() < drain_deadline:
                (
                    pending,
                    completed_tasks,
                    failed_tasks,
                    finished_count,
                ) = _collect_stable_futures(
                    pending,
                    completed_tasks,
                    failed_tasks,
                    tasks_submitted,
                    start_time,
                    self.logger,
                    times,
                    completed_counts,
                    timeout=1,
                )
                if finished_count > 0:
                    last_progress_time = time.time()
            if pending:
                raise TimeoutError(
                    f"stable test drain timed out: {len(pending)} tasks still pending "
                    f"after {final_drain_timeout}s"
                )

            total_time = time.time() - start_time
            speed = completed_tasks / total_time if total_time > 0 else 0
            self.logger.info(
                f"Completed {completed_tasks}/{tasks_submitted} tasks in {total_time:.2f} seconds. Final speed: {speed:.2f} tasks/second"
            )

            self.logger.info(
                f"finished, duration: {duration_seconds}s, tasks: {tasks_submitted}, failed: {failed_tasks}"
            )
        finally:
            for future in pending:
                future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
        self.get_fibers_balance_message()
        for fiber in self.fibers:
            message = self.get_fiber_balance(fiber)
            assert message["ckb"] == {
                "local_balance": 200000000000,
                "offered_tlc_balance": 0,
                "received_tlc_balance": 0,
            }

        assert self.get_fiber_balance(self.fiber1)[udt["args"]] == {
            "local_balance": 200000000000 + DEFAULT_MIN_DEPOSIT_CKB,
            "offered_tlc_balance": 0,
            "received_tlc_balance": 0,
        }
        assert self.get_fiber_balance(self.fiber2)[udt["args"]] == {
            "local_balance": 200000000000 + DEFAULT_MIN_DEPOSIT_CKB,
            "offered_tlc_balance": 0,
            "received_tlc_balance": 0,
        }
        assert self.get_fiber_balance(self.fiber3)[udt["args"]] == {
            "local_balance": 200000000000,
            "offered_tlc_balance": 0,
            "received_tlc_balance": 0,
        }
        assert self.get_fiber_balance(self.fiber4)[udt["args"]] == {
            "local_balance": 200000000000 + DEFAULT_MIN_DEPOSIT_CKB * 2,
            "offered_tlc_balance": 0,
            "received_tlc_balance": 0,
        }
