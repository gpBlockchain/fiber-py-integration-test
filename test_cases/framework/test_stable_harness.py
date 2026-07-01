import concurrent.futures

from test_cases.fiber.devnet.stable import test_stable


class RecordingLogger:
    def __init__(self):
        self.errors = []

    def debug(self, message):
        pass

    def error(self, message):
        self.errors.append(message)


def test_collect_stable_futures_records_payment_failures_without_raising():
    logger = RecordingLogger()
    times = []
    completed_counts = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        pending = {
            executor.submit(lambda: "ok"),
            executor.submit(lambda: (_ for _ in ()).throw(Exception("payment failed"))),
        }
        concurrent.futures.wait(pending)

    pending, completed, failed, finished = test_stable._collect_stable_futures(
        pending,
        completed_tasks=0,
        failed_tasks=0,
        tasks_submitted=2,
        start_time=0,
        logger=logger,
        times=times,
        completed_counts=completed_counts,
    )

    assert pending == set()
    assert completed == 2
    assert failed == 1
    assert finished == 2
    assert len(logger.errors) == 1
    assert "payment failed" in logger.errors[0]
