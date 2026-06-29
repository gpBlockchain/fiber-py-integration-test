import re
import time
from pathlib import Path

from framework.basic_fiber import FiberTest
from framework.config import DEFAULT_MIN_DEPOSIT_CKB

# A watchtower forwarding RPC against an offline standalone watchtower must
# surface as an error line in fiber2's log. Match "watchtower" near an
# error/connection word so the matcher is robust to exact wording changes.
WATCHTOWER_RPC_ERROR_RE = re.compile(
    r"(?i)watchtower.*(error|refus|unreachable|connect|fail|timed?[ _-]?out)"
)


class TestWatchtowerTaskSurvivesRpcError(FiberTest):
    """
    PR-1397 regression (GHSA-49ph-wf39-5prx): a watchtower RPC error must NOT
    permanently kill fiber2's watchtower event-processing task. If it does, no
    further commitment updates are ever forwarded to the standalone watchtower,
    so a later legitimate force-close cannot be settled by it.

    Discriminating design (so the test fails on the vulnerable build and passes
    on the fixed one):
      1. fiber2 uses standalone watchtower fiber3, built-in disabled.
      2. Open the channel while fiber3 is ONLINE (commitment #0 is forwarded ok).
      3. Take fiber3 OFFLINE, then make a payment -> fiber2's forward RPC fails
         with connection-refused. NEGATIVE CONTROL: assert this error is in
         fiber2's log, proving the vulnerable path was actually hit.
      4. Bring fiber3 back ONLINE and fund it.
      5. Make ANOTHER payment -> this commitment (#2) must be forwarded to fiber3
         AFTER the earlier error. This only happens if the event task survived.
      6. Force-close while fiber3 is ONLINE so it watches the commitment on-chain
         and settles. The settlement tx appears ONLY if fiber3 holds the latest
         commitment data, i.e. only if step 5's forward succeeded => task alive.
    """

    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 5}

    # Amount (shannons) moved fiber1 -> fiber2 on each commitment update.
    PAYMENT_AMOUNT = 1 * 100000000

    def test_event_task_survives_watchtower_rpc_error(self):
        self.fiber3 = self.start_new_fiber(self.generate_random_preimage())
        self.fiber2.stop()
        self.fiber2.prepare(
            {
                "fiber_standalone_watchtower_rpc_url": self.fiber3.get_client().url,
                "fiber_disable_built_in_watchtower": "true",
            }
        )
        self.fiber2.start()

        # Channel opens while the watchtower is online: commitment #0 forwarded ok.
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1)

        check_interval = self.start_fiber_config[
            "fiber_watchtower_check_interval_seconds"
        ]

        # Take the watchtower offline, then create a commitment update so fiber2's
        # forwarding task issues an RPC call that fails (connection refused).
        self.fiber3.stop()
        time.sleep(check_interval)
        self.send_payment(self.fiber1, self.fiber2, self.PAYMENT_AMOUNT)

        # Negative control: prove the RPC error path was exercised. Poll the log
        # and return as soon as the error appears (faster + more robust than a
        # fixed sleep); fail if it never shows up within the budget.
        fiber2_log = Path(self.fiber2.tmp_path) / "node.log"
        log_text = ""
        for _ in range(30):
            log_text = fiber2_log.read_text(errors="replace")
            if WATCHTOWER_RPC_ERROR_RE.search(log_text):
                break
            time.sleep(1)
        wt_lines = [ln for ln in log_text.splitlines() if "watchtower" in ln.lower()]
        print("=== fiber2 watchtower log lines (tail) ===")
        for ln in wt_lines[-40:]:
            print(ln)
        assert WATCHTOWER_RPC_ERROR_RE.search(log_text), (
            "expected a watchtower RPC error in fiber2 node.log during the "
            "offline window; the vulnerable path was not exercised"
        )

        # Bring the watchtower back online and fund it so it can build settlement.
        self.fiber3.start()
        self.faucet(self.fiber3.account_private, 1000)

        # A commitment update AFTER the earlier RPC error. If the event task had
        # died, this is never forwarded and the watchtower keeps stale data.
        self.send_payment(self.fiber1, self.fiber2, self.PAYMENT_AMOUNT)
        time.sleep(check_interval * 2)

        # Force-close while the watchtower is ONLINE so it watches the commitment
        # on-chain and settles. The settlement tx can only be built if the post-
        # error forward (above) reached fiber3, i.e. the task survived.
        self.fiber1.get_client().shutdown_channel(
            {
                "channel_id": self.fiber1.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "force": True,
            }
        )
        tx = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, tx)
        self.fiber1.stop()
        self.node.getClient().generate_epochs("0x1", 0)

        before_balance = self.get_fiber_balance(self.fiber3)
        # If the event-processing task had died on the earlier RPC error, the
        # watchtower would lack the latest commitment and no settlement tx would
        # appear here. Its appearance proves the task survived.
        tx = self.wait_and_check_tx_pool_fee(1000, False, 120 * 5)
        self.Miner.miner_until_tx_committed(self.node, tx)
        after_balance = self.get_fiber_balance(self.fiber3)
        result = self.get_balance_change([before_balance], [after_balance])
        # The watchtower settles fiber2's side, which equals its base deposit
        # plus the two payments forwarded to it. The post-error payment being
        # included is the discriminator: on the vulnerable build the task would
        # be dead, the latest commitment never forwarded, and this amount wrong.
        expected_gain = DEFAULT_MIN_DEPOSIT_CKB + 2 * self.PAYMENT_AMOUNT
        assert abs(result[0]["ckb"] + expected_gain) < 10000
