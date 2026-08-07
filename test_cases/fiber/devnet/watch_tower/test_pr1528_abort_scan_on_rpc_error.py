"""PR-1528: abort a watchtower settlement scan when pagination RPCs fail."""

import time
from pathlib import Path

from framework.basic_fiber import FiberTest
from framework.ckb_rpc_proxy import CkbRpcProxy


class TestWatchtowerPaginationRpcError(FiberTest):
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 5}

    def test_pagination_rpc_errors_abort_current_scan(self):
        proxy = CkbRpcProxy(self.node.rpcUrl)
        proxy.start()

        try:
            # Use a standalone watchtower so unrelated Fiber tasks cannot consume
            # the proxy notification used to place the error injection.
            self.fiber3 = self.start_new_fiber(
                self.generate_random_preimage(),
                {"ckb_rpc_url": proxy.url},
            )

            self.fiber2.stop()
            self.fiber2.prepare(
                {
                    "fiber_standalone_watchtower_rpc_url": self.fiber3.get_client().url,
                    "fiber_disable_built_in_watchtower": "true",
                }
            )
            self.fiber2.start()

            # Open the channel while fiber3 is online so it persists the watch data.
            channel_id = self.open_channel(
                self.fiber1,
                self.fiber2,
                1000 * 100000000,
                1,
            )

            # Keep fiber3's persisted watch entry while the commitment transaction
            # is submitted and committed.
            self.fiber3.stop()
            self.fiber1.get_client().shutdown_channel(
                {"channel_id": channel_id, "force": True}
            )
            commitment_tx = self.wait_and_check_tx_pool_fee(1000, False)
            self.Miner.miner_until_tx_committed(self.node, commitment_tx)
            for _ in range(2):
                self.Miner.miner_with_version(self.node, "0x0")

            log_path = Path(self.fiber3.tmp_path) / "node.log"
            log_offset = log_path.stat().st_size

            # The first get_transactions call is the outer lookup for the
            # commitment transaction. It must succeed before the two pagination
            # loops covered by PR-1528 are reached.
            proxy.notify_on_method("get_transactions")
            self.fiber3.start()
            assert proxy.wait_for_method(
                20
            ), "watchtower did not look up the committed force-close transaction"

            proxy.block_method_with_error(
                "get_transactions",
                code=-32000,
                message="PR-1528 injected pagination error",
            )
            proxy.block_method_with_error(
                "get_cells",
                code=-32000,
                message="PR-1528 injected pagination error",
            )

            scan_log = ""
            try:
                deadline = time.time() + 10
                while time.time() < deadline:
                    with log_path.open("rb") as log_file:
                        log_file.seek(log_offset)
                        scan_log = log_file.read().decode(errors="replace")

                    if (
                        "Failed to get transactions:" in scan_log
                        and "Failed to get cells:" in scan_log
                        and "PeriodicCheck finished elapsed:" in scan_log
                    ):
                        break

                    # A vulnerable build retries the first failed request in a
                    # tight loop. Stop observing early so an A/B run fails fast.
                    if (
                        scan_log.count("Failed to get transactions:") >= 3
                        or scan_log.count("Failed to get cells:") >= 3
                    ):
                        break
                    time.sleep(0.2)
            finally:
                # Also lets a vulnerable build escape its loop before teardown.
                proxy.unblock_methods()
                time.sleep(1)

            lines = scan_log.splitlines()
            scan_start = next(
                (
                    index
                    for index, line in enumerate(lines)
                    if "PeriodicCheck started" in line
                ),
                None,
            )
            scan_finish = next(
                (
                    index
                    for index, line in enumerate(lines)
                    if scan_start is not None
                    and index > scan_start
                    and "PeriodicCheck finished elapsed:" in line
                ),
                None,
            )
            assert scan_start is not None and scan_finish is not None, scan_log

            scan_lines = lines[scan_start : scan_finish + 1]
            transaction_errors = [
                index
                for index, line in enumerate(scan_lines)
                if "Failed to get transactions:" in line
                and "PR-1528 injected pagination error" in line
                and "aborting settlement scan" in line
            ]
            cell_errors = [
                index
                for index, line in enumerate(scan_lines)
                if "Failed to get cells:" in line
                and "PR-1528 injected pagination error" in line
                and "aborting settlement scan" in line
            ]

            assert len(transaction_errors) == 1, scan_lines
            assert len(cell_errors) == 1, scan_lines
            assert transaction_errors[0] < cell_errors[0], scan_lines

            # The failed scan ended, so the next periodic scan can run normally.
            recovery_offset = log_path.stat().st_size
            recovery_log = ""
            deadline = time.time() + 15
            while time.time() < deadline:
                with log_path.open("rb") as log_file:
                    log_file.seek(recovery_offset)
                    recovery_log = log_file.read().decode(errors="replace")
                if (
                    "PeriodicCheck started" in recovery_log
                    and "PeriodicCheck finished elapsed:" in recovery_log
                ):
                    break
                time.sleep(0.5)

            assert "PeriodicCheck started" in recovery_log, recovery_log
            assert "PeriodicCheck finished elapsed:" in recovery_log, recovery_log
            assert self.fiber3.get_client().node_info()["pubkey"]
        finally:
            proxy.unblock_methods()
            proxy.stop()
