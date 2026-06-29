"""
PR-1213: feat: funding retry
https://github.com/nervosnetwork/fiber/pull/1213

Test coverage for channel funding retry on transient CKB RPC errors.
Verifies the fix for issue #1189 where transient HTTP errors during
cell collection or transaction signing permanently aborted channels.

Issue #1189 identifies three root causes:
  1. is_temporary() didn't classify CkbTxBuilderError/CkbTxUnlockError
     as temporary when they wrap transient HTTP errors.
  2. Even temporary errors only skipped abort_funding without scheduling
     a retry, leaving channels stalled forever.
  3. SignFundingTx unconditionally aborted on any error without checking
     is_temporary().

Approach:
    A CKB RPC proxy sits between Fiber nodes and the CKB devnet node.
    - block()/unblock(): shuts down the proxy port entirely → "Connection
      refused" errors.
    - reject_next(n): keeps the port open but closes TCP connections
      without sending a response → "Connection reset by peer" errors,
      matching the original testnet.ckbapp.dev failure mode.
"""

import time

from framework.basic_fiber import FiberTest
from framework.ckb_rpc_proxy import CkbRpcProxy
from framework.config import DEFAULT_MIN_DEPOSIT_CKB


class TestFundingRetry(FiberTest):
    """Test funding retry on transient CKB RPC failures (PR-1213)."""

    proxy: CkbRpcProxy

    def setup_method(self, method):
        """Override: insert CKB RPC proxy before starting Fiber nodes."""
        self.did_pass = None
        self.beginNum = hex(self.node.getClient().get_tip_block_number())
        self.fibers = []
        self.new_fibers = []

        # --- Start CKB RPC proxy ---
        self.proxy = CkbRpcProxy(self.node.rpcUrl)
        self.proxy.start()

        from framework.test_fiber import Fiber

        self.fiber1 = Fiber.init_by_port(
            self.fiber_version,
            self.account1_private_key,
            "fiber/node1",
            "8228",
            "8227",
        )
        self.fiber2 = Fiber.init_by_port(
            self.fiber_version,
            self.account2_private_key,
            "fiber/node2",
            "8229",
            "8230",
        )
        self.fibers.append(self.fiber1)
        self.fibers.append(self.fiber2)

        from framework.helper.udt_contract import UdtContract, issue_udt_tx
        from framework.basic_fiber import XUDT_TX_HASH

        self.udtContract = UdtContract(XUDT_TX_HASH, 0)
        deploy_hash, deploy_index = self.udtContract.get_deploy_hash_and_index()

        self.node.getClient().clear_tx_pool()
        for i in range(5):
            self.Miner.miner_with_version(self.node, "0x0")
        tx_hash = issue_udt_tx(
            self.udtContract,
            self.node.rpcUrl,
            self.fiber1.account_private,
            self.fiber1.account_private,
            1000 * 100000000,
        )
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        self.node.start_miner()

        # --- Key difference: Fiber nodes use the PROXY URL, not the direct CKB URL ---
        update_config = {
            "ckb_rpc_url": self.proxy.url,
            "ckb_udt_whitelist": True,
            "xudt_script_code_hash": self.Contract.get_ckb_contract_codehash(
                deploy_hash, deploy_index, True, self.node.rpcUrl
            ),
            "xudt_cell_deps_tx_hash": deploy_hash,
            "xudt_cell_deps_index": deploy_index,
        }
        update_config.update(self.start_fiber_config)

        self.fiber1.prepare(update_config=update_config)
        self.fiber1.start(fnn_log_level=self.fnn_log_level)

        self.fiber2.prepare(update_config=update_config)
        self.fiber2.start(fnn_log_level=self.fnn_log_level)

        self.fiber1.connect_peer(self.fiber2)
        time.sleep(1)
        self.logger.debug(f"\nSetting up method: {method.__name__}")

    def teardown_method(self, method):
        proxy = getattr(self, "proxy", None)
        if proxy:
            proxy.stop()
        super().teardown_method(method)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _read_fiber_log(self, fiber) -> str:
        """Read a Fiber node's log file."""
        log_path = f"{fiber.tmp_path}/node.log"
        try:
            with open(log_path, "r") as f:
                return f.read()
        except FileNotFoundError:
            return ""

    def _assert_retry_in_logs(self, fiber, label="fund channel"):
        """Assert that retry messages appear in the Fiber node log."""
        log = self._read_fiber_log(fiber)
        log_lower = log.lower()
        # PR-1213 logs: "Temporary {operation} error, scheduling retry"
        assert (
            "scheduling retry" in log_lower
        ), f"Expected 'scheduling retry' in {fiber.tmp_path}/node.log"

    # ------------------------------------------------------------------
    # Test Cases
    # ------------------------------------------------------------------
    def test_open_channel_succeeds_after_transient_ckb_rpc_failure(self):
        """
        Core test: block CKB RPC proxy, open channel, unblock after a few
        seconds.  The retry mechanism should allow the channel to eventually
        reach ChannelReady.

        Timeline:
          t=0s   block proxy + call open_channel
          t=3s   unblock proxy (after 1-2 retry failures)
          t≈15s  channel reaches ChannelReady via retry
        """
        # Block CKB RPC *before* opening the channel
        self.proxy.block()

        # open_channel RPC goes to Fiber (not CKB), so it succeeds immediately
        self.fiber1.get_client().open_channel(
            {
                "pubkey": self.fiber2.get_pubkey(),
                "funding_amount": hex(200 * 100000000 + DEFAULT_MIN_DEPOSIT_CKB),
                "public": True,
                "tlc_fee_proportional_millionths": "0x4B0",
            }
        )

        # Keep proxy blocked for 3 seconds to let 1-2 retry attempts fail
        time.sleep(3)

        # Unblock — next retry should pick up and succeed
        self.proxy.unblock()

        # Wait for channel to become ready
        self.wait_for_channel_state(
            self.fiber1.get_client(),
            self.fiber2.get_pubkey(),
            "ChannelReady",
            timeout=30,
        )

        # Verify retries happened by checking logs
        self._assert_retry_in_logs(self.fiber1)

    def test_channel_aborted_after_retry_exhausted(self):
        """
        Boundary test: keep CKB RPC blocked until all 5 retry attempts are
        exhausted.  The channel should transition to a closed/aborted state.

        Retry timing: 2s + 4s + 8s + 16s = 30s total before 5th attempt aborts.
        We block for 35s to be safe.
        """
        self.proxy.block()

        self.fiber1.get_client().open_channel(
            {
                "pubkey": self.fiber2.get_pubkey(),
                "funding_amount": hex(200 * 100000000 + DEFAULT_MIN_DEPOSIT_CKB),
                "public": True,
                "tlc_fee_proportional_millionths": "0x4B0",
            }
        )

        # Wait long enough for all retries to be exhausted
        # Attempts: 1(+0s), 2(+2s), 3(+4s), 4(+8s), 5(+16s) → ~30s total
        time.sleep(35)

        # Unblock now (too late — retries should be exhausted)
        self.proxy.unblock()
        time.sleep(3)

        # Channel should NOT be in ChannelReady; it should be closed/absent
        channels = self.fiber1.get_client().list_channels(
            {"pubkey": self.fiber2.get_pubkey(), "include_closed": True}
        )
        if len(channels["channels"]) > 0:
            state = channels["channels"][0]["state"]["state_name"]
            assert (
                state != "ChannelReady"
            ), f"Channel should have been aborted but is in state: {state}"
        # If no channels at all, that's also acceptable (fully cleaned up)

    def test_open_channel_normal_via_proxy(self):
        """
        Control test: proxy in forwarding mode (no blocking).
        Verifies the proxy does not interfere with normal channel establishment.
        """
        # Proxy is forwarding by default — just open a channel normally
        self.open_channel(self.fiber1, self.fiber2, 200 * 100000000, 100 * 100000000)
        # If we get here, the channel opened successfully through the proxy
        channels = self.fiber1.get_client().list_channels(
            {"pubkey": self.fiber2.get_pubkey()}
        )
        assert len(channels["channels"]) > 0
        assert channels["channels"][0]["state"]["state_name"] == "ChannelReady"

    # ------------------------------------------------------------------
    # Issue #1189, Failure 1: only one side's CKB RPC fails
    # ------------------------------------------------------------------
    def test_only_one_side_ckb_rpc_fails(self):
        """
        Issue #1189 Failure 1: acceptor-side cell collector HTTP failure.

        Setup:
          - fiber1 uses the shared proxy (will be blocked → CKB RPC unavailable)
          - fiber3 is started via start_new_fiber → uses direct CKB RPC URL
            (no proxy, always available)
          - fiber1 opens channel to fiber3

        When the proxy is blocked, only fiber1's UpdateChannelFunding fails.
        fiber3's UpdateChannelFunding succeeds through the direct CKB
        connection. After the proxy is unblocked, fiber1's retry succeeds
        and the channel reaches ChannelReady.

        This verifies asymmetric failure handling — the channel protocol
        tolerates one side being temporarily slower due to RPC retries.
        """
        # fiber3 uses direct CKB URL (start_new_fiber does NOT use the proxy)
        fiber3 = self.start_new_fiber(self.generate_account(10000))
        self.fiber1.connect_peer(fiber3)
        time.sleep(1)

        # Block fiber1's CKB RPC
        self.proxy.block()

        self.fiber1.get_client().open_channel(
            {
                "pubkey": fiber3.get_pubkey(),
                "funding_amount": hex(200 * 100000000 + DEFAULT_MIN_DEPOSIT_CKB),
                "public": True,
                "tlc_fee_proportional_millionths": "0x4B0",
            }
        )

        # Let 1-2 retry attempts fail
        time.sleep(3)

        # Unblock — fiber1's next retry succeeds
        self.proxy.unblock()

        self.wait_for_channel_state(
            self.fiber1.get_client(),
            fiber3.get_pubkey(),
            "ChannelReady",
            timeout=30,
        )

        # Verify retry happened on fiber1's side
        self._assert_retry_in_logs(self.fiber1)

    # ------------------------------------------------------------------
    # Issue #1189, Issue 3: SignFundingTx retry
    # ------------------------------------------------------------------
    def test_sign_phase_retry_via_mid_flow_block(self):
        """
        Issue #1189 Issue 3: SignFundingTx used to unconditionally abort
        on any error without checking is_temporary().

        Strategy: use ``auto_block_on_method("get_transaction")`` so the
        proxy automatically closes its listening socket (→ Connection refused)
        the instant it sees a ``get_transaction`` RPC call.  Cell collection
        RPCs (get_cells, get_live_cell, etc.) are forwarded normally, but
        the first ``get_transaction`` — used for signing / cell dep
        resolution — triggers immediate port shutdown.

        This is deterministic and has no timing dependency:
          - Cell collection succeeds (earlier RPCs forwarded)
          - The get_transaction call (and all subsequent calls) fail with
            Connection refused
          - Fixed version: retries and recovers → ChannelReady
          - Buggy version: unconditionally aborts → never ChannelReady
        """
        # Arm the auto-block trigger
        self.proxy.auto_block_on_method("get_transaction")

        self.fiber1.get_client().open_channel(
            {
                "pubkey": self.fiber2.get_pubkey(),
                "funding_amount": hex(200 * 100000000 + DEFAULT_MIN_DEPOSIT_CKB),
                "public": True,
                "tlc_fee_proportional_millionths": "0x4B0",
            }
        )

        # Wait for auto-block to trigger and sign retries to fail
        for _ in range(10):
            if self.proxy.auto_blocked:
                break
            time.sleep(0.5)
        assert self.proxy.auto_blocked, "auto_block_on_method did not trigger"

        # Keep blocked for a few seconds to let retry attempts fail
        time.sleep(3)

        # Unblock — next retry should succeed
        self.proxy.unblock()

        # Channel should reach ChannelReady via retry
        self.wait_for_channel_state(
            self.fiber1.get_client(),
            self.fiber2.get_pubkey(),
            "ChannelReady",
            timeout=30,
        )

    # ------------------------------------------------------------------
    # Issue #1189 reproduction: connection-reset mid-flow
    # ------------------------------------------------------------------
    def test_connection_reset_mid_flow(self):
        """
        Simulates the exact failure mode from Issue #1189: the CKB RPC
        endpoint resets TCP connections mid-flow (without closing the port).

        Uses reject_next(n) which keeps the proxy port open but closes
        TCP connections immediately, producing "Connection reset by peer"
        errors — exactly matching the testnet.ckbapp.dev failure.

        Strategy: set reject_next BEFORE opening the channel so the first
        CKB RPC calls for funding/signing are guaranteed to be rejected.
        Both fiber1 and fiber2 use the proxy, so we need enough rejects
        to cover both sides' calls. After the rejects are consumed, the
        proxy auto-resumes normal forwarding.
        """
        # Set rejections BEFORE opening channel to ensure funding CKB calls
        # are hit. Use a small count (2) so retries can succeed after the
        # rejections are consumed. Both nodes share the proxy, so 2 rejects
        # will be consumed quickly by the first funding CKB call(s).
        self.proxy.reject_next(2)

        self.fiber1.get_client().open_channel(
            {
                "pubkey": self.fiber2.get_pubkey(),
                "funding_amount": hex(200 * 100000000 + DEFAULT_MIN_DEPOSIT_CKB),
                "public": True,
                "tlc_fee_proportional_millionths": "0x4B0",
            }
        )

        # Channel should recover via retry and reach ChannelReady
        # (after rejections are consumed, proxy auto-resumes forwarding)
        self.wait_for_channel_state(
            self.fiber1.get_client(),
            self.fiber2.get_pubkey(),
            "ChannelReady",
            timeout=30,
        )

        # Verify retries happened — check both nodes since both use the proxy
        log1 = self._read_fiber_log(self.fiber1)
        log2 = self._read_fiber_log(self.fiber2)
        combined = (log1 + log2).lower()
        assert (
            "scheduling retry" in combined
        ), "Expected 'scheduling retry' in fiber node logs"

    # ------------------------------------------------------------------
    # PR-1444: permanent SendTx failure must abort funding
    # ------------------------------------------------------------------
    def test_permanent_send_transaction_error_aborts_funding_channel(self):
        """
        PR-1444 regression: when CKB rejects the funding transaction at
        send_transaction time with a permanent TransactionFailedToResolve
        error, Fiber should synthesize a rejected tx trace and abort the
        channel instead of leaving it stuck in a pending funding state.
        """
        self.proxy.block_method_with_error(
            "send_transaction",
            code=-301,
            message="TransactionFailedToResolve: Unknown(OutPoint)",
        )
        peer_pubkey = self.fiber2.get_pubkey()

        self.fiber1.get_client().open_channel(
            {
                "pubkey": peer_pubkey,
                "funding_amount": hex(200 * 100000000 + DEFAULT_MIN_DEPOSIT_CKB),
                "public": True,
                "tlc_fee_proportional_millionths": "0x4B0",
            }
        )

        last_pending_channels = []
        for _ in range(45):
            pending = self.fiber1.get_client().list_channels(
                {
                    "pubkey": peer_pubkey,
                    "only_pending": True,
                }
            )
            last_pending_channels = pending["channels"]
            for channel in pending["channels"]:
                if channel["state"]["state_name"] == "Closed":
                    assert channel["state"]["state_flags"] == "FUNDING_ABORTED"
                    return
                assert channel["state"]["state_name"] != "ChannelReady"
            time.sleep(1)

        channels = self.fiber1.get_client().list_channels(
            {"pubkey": peer_pubkey, "include_closed": True}
        )
        assert False, (
            "permanent send_transaction error did not abort funding; "
            f"pending={last_pending_channels}, channels={channels['channels']}"
        )
