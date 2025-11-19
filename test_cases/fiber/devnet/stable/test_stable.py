import time

from framework.basic_fiber import FiberTest
import concurrent.futures

from framework.config import DEFAULT_MIN_DEPOSIT_CKB


class TestStableStress(FiberTest):
    fnn_log_level = "info"

    # def test_conn(self):
    #     self.
    def test_stable_stress(self):
        """
        a->b->c->a
            1. a->a  ckb and udt
            2. b->b  ckb and udt
            3. c->c  ckb and udt
        Returns:
        """
        self.fiber3 = self.start_new_fiber(
            self.generate_account(10000, self.fiber1.account_private, 10000 * 100000000)
        )
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

        self.open_channel(
            self.fiber3, self.fiber1, 1000 * 100000000, 1000 * 100000000, 0, 0
        )
        self.open_channel(
            self.fiber3,
            self.fiber1,
            1000 * 100000000,
            1000 * 100000000,
            0,
            0,
            self.get_account_udt_script(self.fiber1.account_private),
        )

        time.sleep(10)
        udt = self.get_account_udt_script(self.fiber1.account_private)
        send_tx_size = 5 * 60 * 60 * 2
        tasks_submitted = 0
        start_time = time.time()
        times = []
        completed_counts = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            futures = []

            for i in range(send_tx_size):
                # Submit payment tasks for fiber1
                futures.append(
                    executor.submit(self.send_payment, self.fiber1, self.fiber1, 1)
                )
                futures.append(
                    executor.submit(
                        self.send_payment, self.fiber1, self.fiber1, 1, True, udt
                    )
                )

                futures.append(
                    executor.submit(self.send_payment, self.fiber2, self.fiber2, 1)
                )
                futures.append(
                    executor.submit(
                        self.send_payment, self.fiber2, self.fiber2, 1, True, udt
                    )
                )

                futures.append(
                    executor.submit(self.send_payment, self.fiber3, self.fiber3, 1)
                )
                futures.append(
                    executor.submit(
                        self.send_payment, self.fiber3, self.fiber3, 1, True, udt
                    )
                )
                tasks_submitted += 6

            # Wait for all tasks to complete
            # concurrent.futures.wait(futures)
            completed_tasks = 0
            for future in concurrent.futures.as_completed(futures):
                completed_tasks += 1
                try:
                    result = future.result()
                    self.logger.debug(
                        f"Task {completed_tasks}/{tasks_submitted} completed"
                    )
                except Exception as e:
                    self.logger.error(
                        f"Task {completed_tasks}/{tasks_submitted} failed: {e}"
                    )

                # Record time and completed tasks
                elapsed_time = time.time() - start_time
                times.append(elapsed_time)
                completed_counts.append(completed_tasks)

                if completed_tasks % 100 == 0:
                    speed = completed_tasks / elapsed_time if elapsed_time > 0 else 0
                    self.logger.info(
                        f"Completed {completed_tasks}/{tasks_submitted} tasks in {elapsed_time:.2f} seconds. Speed: {speed:.2f} tasks/second"
                    )

            total_time = time.time() - start_time
            speed = completed_tasks / total_time if total_time > 0 else 0
            self.logger.info(
                f"Completed {completed_tasks}/{tasks_submitted} tasks in {total_time:.2f} seconds. Final speed: {speed:.2f} tasks/second"
            )

            self.logger.info(f"finished :{send_tx_size}")
        self.get_fibers_balance_message()
        for fiber in self.fibers:
            message = self.get_fiber_balance(fiber)
            assert message["ckb"] == {
                "local_balance": 200000000000,
                "offered_tlc_balance": 0,
                "received_tlc_balance": 0,
            }
            assert message[udt["args"]] == {
                "local_balance": 200000000000 + DEFAULT_MIN_DEPOSIT_CKB,
                "offered_tlc_balance": 0,
                "received_tlc_balance": 0,
            }
