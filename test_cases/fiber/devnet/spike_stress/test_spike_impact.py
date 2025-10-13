import time

from framework.basic_fiber import FiberTest
import concurrent.futures


class TestSpikeStress(FiberTest):

    debug = True

    def test_00(self):
        self.start_new_mock_fiber("")
        # self.fiber3 = self.start_new_fiber("")
        # self.generate_account(10000, self.fiber1.account_private, 10000 * 100000000))
        self.get_fibers_balance_message()

    def test_spike_stress(self):
        """
        a->b->c->a

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
        send_tx_size = 1000
        tasks_submitted = 0
        start_time = time.time()
        times = []
        completed_counts = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=400) as executor:
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
            successful_tasks = 0
            for future in concurrent.futures.as_completed(futures):
                completed_tasks += 1
                try:
                    result = future.result()
                    successful_tasks += 1
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
                    sucessful_speed = (
                        successful_tasks / elapsed_time if elapsed_time > 0 else 0
                    )
                    self.logger.info(
                        f"Completed {completed_tasks}/{tasks_submitted} tasks in {elapsed_time:.2f} seconds. Speed: {speed:.2f} tasks/second, sucessful speed: {sucessful_speed:.2f} tasks/second"
                    )

            total_time = time.time() - start_time
            speed = completed_tasks / total_time if total_time > 0 else 0
            self.logger.info(
                f"Completed {completed_tasks}/{tasks_submitted} tasks in {total_time:.2f} seconds. Final speed: {speed:.2f} tasks/second, sucessful speed: {successful_tasks / total_time:.2f} tasks/second"
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
                "local_balance": 206200000000,
                "offered_tlc_balance": 0,
                "received_tlc_balance": 0,
            }

    def test_spike_stress_3(self):
        self.fiber3 = self.start_new_mock_fiber("")
        # self.generate_account(10000, self.fiber1.account_private, 10000 * 100000000))
        # self.faucet(self.fiber2.account_private, 0, self.fiber1.account_private, 10000 * 100000000)
        # self.faucet(self.fiber1.account_private, 0, self.fiber1.account_private, 10000 * 100000000)
        # self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1000 * 100000000, 0, 0)
        # self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1000 * 100000000, 0, 0,
        #                   self.get_account_udt_script(self.fiber1.account_private))
        #
        # self.open_channel(self.fiber2, self.fiber3, 1000 * 100000000, 1000 * 100000000, 0, 0)
        # self.open_channel(self.fiber2, self.fiber3, 1000 * 100000000, 1000 * 100000000, 0, 0,
        #                   self.get_account_udt_script(self.fiber1.account_private))
        #
        # self.open_channel(self.fiber3, self.fiber1, 1000 * 100000000, 1000 * 100000000, 0, 0)
        # self.open_channel(self.fiber3, self.fiber1, 1000 * 100000000, 1000 * 100000000, 0, 0,
        #                   self.get_account_udt_script(self.fiber1.account_private))

        # time.sleep(10)
        udt = self.get_account_udt_script(self.fiber1.account_private)
        send_tx_size = 10000
        for i in range(send_tx_size):
            print("current i:", i)
            # Submit payment tasks for fiber1
            try:
                self.send_payment(self.fiber1, self.fiber1, 1, False, None, 0)
            except Exception as e:
                pass
            try:
                self.send_payment(self.fiber1, self.fiber1, 1, False, udt, 0)
            except Exception as e:
                pass
            try:
                self.send_payment(self.fiber2, self.fiber2, 1, False, None, 0)
            except Exception as e:
                pass
            try:
                self.send_payment(self.fiber2, self.fiber2, 1, False, udt, 0)
            except Exception as e:
                pass
            try:
                self.send_payment(self.fiber3, self.fiber3, 1, False, None, 0)
            except Exception as e:
                pass
            try:
                self.send_payment(self.fiber3, self.fiber3, 1, False, udt, 0)
            except Exception as e:
                pass

    def test_bb222b(self):
        self.fiber3 = self.start_new_mock_fiber(
            # self.generate_account(10000, self.fiber1.account_private, 10000 * 100000000)
            ""
        )
        for i in range(1000000):
            print("------")
            self.get_fibers_balance_message()
            time.sleep(1)

    def test_00000(self):
        self.fiber3 = self.start_new_mock_fiber("")
        # self.generate_account(10000, self.fiber1.account_private, 10000 * 100000000))

        time.sleep(10)
        udt = self.get_account_udt_script(self.fiber1.account_private)
        send_tx_size = 1000
        tasks_submitted = 0
        start_time = time.time()
        times = []
        completed_counts = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=400) as executor:
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
            successful_tasks = 0
            for future in concurrent.futures.as_completed(futures):
                completed_tasks += 1
                try:
                    result = future.result()
                    successful_tasks += 1
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
                    sucessful_speed = (
                        successful_tasks / elapsed_time if elapsed_time > 0 else 0
                    )
                    self.logger.info(
                        f"Completed {completed_tasks}/{tasks_submitted} tasks in {elapsed_time:.2f} seconds. Speed: {speed:.2f} tasks/second, sucessful speed: {sucessful_speed:.2f} tasks/second"
                    )

            total_time = time.time() - start_time
            speed = completed_tasks / total_time if total_time > 0 else 0
            self.logger.info(
                f"Completed {completed_tasks}/{tasks_submitted} tasks in {total_time:.2f} seconds. Final speed: {speed:.2f} tasks/second, sucessful speed: {successful_tasks / total_time:.2f} tasks/second"
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
                "local_balance": 206200000000,
                "offered_tlc_balance": 0,
                "received_tlc_balance": 0,
            }

    def test_00001(self):
        self.fiber3 = self.start_new_mock_fiber("")
        udt = self.get_account_udt_script(self.fiber1.account_private)
        for i in range(10):
            for fiber in self.fibers:
                self.send_payment(fiber, fiber, 1000, True)
                self.send_payment(fiber, fiber, 1000, True, udt)

    def test_bbb(self):
        self.fiber3 = self.start_new_mock_fiber("")
        self.get_fibers_balance_message()
        self.fiber1.connect_peer(self.fiber2)
        self.fiber2.connect_peer(self.fiber3)
        self.fiber1.connect_peer(self.fiber3)
