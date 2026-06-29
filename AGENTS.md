# Fiber Network Integration Test

## Project Overview

Fiber Network Node (FNN) is a Lightning Network implementation on Nervos CKB blockchain. This test project provides Python integration tests covering channel lifecycle, payments, invoices, watchtower, cross-chain hub (CCH), and more.

For detailed Lightning Network concepts mapped to Fiber, see [docs/references/lightning-concepts.md](docs/references/lightning-concepts.md).

## Test Framework Architecture

```
framework/
├── basic.py              # CkbTest base (unittest.TestCase → CKB node)
├── basic_fiber.py        # FiberTest base (2 Fiber nodes + helpers)
├── basic_share_fiber.py  # SharedFiberTest base (shared env across methods)
├── basic_fiber_with_cch.py  # FiberCchTest (+ BTC + LND for cross-chain)
├── fiber_rpc.py          # FiberRPCClient (JSON-RPC 2.0)
├── fnn_cli.py            # FNN CLI wrapper used by fnn-cli integration tests
├── rpc.py                # RPCClient for CKB
├── test_cluster.py       # Cluster lifecycle helpers for multi-node tests
├── test_btc.py           # Bitcoin node lifecycle helpers (CCH)
├── test_fiber.py         # Fiber node lifecycle
├── test_lnd.py           # LND node lifecycle helpers (CCH)
├── test_node.py          # CKB node lifecycle
├── test_wasm_fiber.py    # WASM Fiber service lifecycle helpers
├── config.py             # Constants (DEFAULT_MIN_DEPOSIT_CKB = 99 * 100000000)
├── util.py               # Utilities (run_command, generate_account, change_time)
└── helper/               # miner.py, ckb_cli.py, contract.py, udt_contract.py, tx.py
```

**Inheritance**:

```
unittest.TestCase → CkbTest → FiberTest → FiberCchTest
                                  ↓
                            SharedFiberTest
```

- **FiberTest**: Each test method (`setup_method`) starts fresh Fiber nodes, issues UDT, connects peers, then tears down everything in `teardown_method`. Isolated but slow.
- **SharedFiberTest**: Fiber environment is initialized once in `setup_class` and shared across all test methods. Only `teardown_class` cleans up. Much faster for multi-test classes that build on the same topology.

Each test method auto-gets: CKB dev node (`self.node`), two connected Fiber nodes (`self.fiber1`, `self.fiber2`), UDT contract (`self.udtContract`).

### Choosing FiberTest vs SharedFiberTest

| Criteria | FiberTest | SharedFiberTest |
|----------|-----------|-----------------|
| **Environment lifecycle** | Per-method (fresh each test) | Per-class (shared across tests) |
| **Speed** | Slow (full setup/teardown per test) | Fast (one-time setup, reused) |
| **Test isolation** | Full isolation | Tests share state — order may matter |
| **Use when** | Tests need clean state, destructive ops (force close, revoke) | Multiple tests build on same topology (routing, fee, payment params) |
| **Extra nodes** | `self.start_new_fiber(key)` in test method | `self.start_new_fiber(key)` in `setUp()` with `_channel_inited` guard |
| **Cleanup** | Automatic per-method | Automatic per-class |

## Writing Tests - Quick Templates

### Template A: FiberTest (isolated per-method environment)

```python
import time
import pytest
from framework.basic_fiber import FiberTest

class TestMyFeature(FiberTest):
    # Optional config override
    # start_fiber_config = {"fiber_auto_accept_amount": "0"}

    def test_basic_scenario(self):
        self.open_channel(self.fiber1, self.fiber2, 200 * 100000000, 100 * 100000000)
        payment_hash = self.send_payment(self.fiber1, self.fiber2, 10 * 100000000)
        result = self.fiber1.get_client().get_payment({"payment_hash": payment_hash})
        assert result["status"] == "Success"

    def test_error_scenario(self):
        with pytest.raises(Exception) as exc_info:
            self.fiber1.get_client().open_channel({
                "pubkey": self.fiber2.get_pubkey(),
                "funding_amount": hex(0), "public": True,
            })
        assert "should be greater than or equal to" in exc_info.value.args[0]
```

### Template B: SharedFiberTest (shared environment, one-time topology setup)

Use when multiple tests share the same channel topology (routing tests, fee tests, parameter boundary tests).

```python
import pytest
from framework.basic_share_fiber import SharedFiberTest
from framework.test_fiber import Fiber

class TestMySharedFeature(SharedFiberTest):
    # Optional config override
    # start_fiber_config = {"fiber_auto_accept_amount": "0"}

    fiber3: Fiber
    fiber4: Fiber

    def setUp(self):
        """One-time topology setup, guarded by _channel_inited flag."""
        if getattr(TestMySharedFeature, "_channel_inited", False):
            return
        TestMySharedFeature._channel_inited = True

        # Create extra nodes
        self.__class__.fiber3 = self.start_new_fiber(self.generate_account(10000))
        self.__class__.fiber4 = self.start_new_fiber(self.generate_account(10000))

        # Build topology: fiber1 -- fiber2 -- fiber3 -- fiber4
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0)
        self.open_channel(self.fiber2, self.fiber3, 1000 * 100000000, 0)
        self.open_channel(self.fiber3, self.fiber4, 1000 * 100000000, 0)

    def test_multi_hop_payment(self):
        payment_hash = self.send_payment(self.fiber1, self.fiber4, 1 * 100000000)
        result = self.fiber1.get_client().get_payment({"payment_hash": payment_hash})
        assert result["status"] == "Success"

    def test_dry_run_fee(self):
        payment = self.fiber1.get_client().send_payment({
            "target_pubkey": self.fiber4.get_client().node_info()["pubkey"],
            "amount": hex(1 * 100000000),
            "keysend": True,
            "dry_run": True,
        })
        assert int(payment["fee"], 16) > 0
```

**Key pattern**: `setUp()` (unittest-style, called before each test) + `_channel_inited` class-level flag ensures topology is built only once. Use `self.__class__.fiberN` to store extra nodes on the class.

## Key Helper Methods

| Method | Purpose |
|--------|---------|
| `self.open_channel(f1, f2, bal1, bal2, udt=None)` | Open channel with balances |
| `self.send_payment(f1, f2, amount)` | Keysend with retry |
| `self.send_invoice_payment(f1, f2, amount)` | Invoice payment with retry |
| `self.wait_for_channel_state(client, pubkey, state)` | Wait channel state |
| `self.wait_payment_state(fiber, hash, status)` | Wait payment Success/Failed |
| `self.wait_invoice_state(client, hash, status)` | Wait invoice status |
| `self.generate_account(ckb_balance)` | Create funded account |
| `self.start_new_fiber(private_key)` | Start fiber3, fiber4, ... |
| `self.generate_random_preimage()` | Random 32-byte hex |
| `self.get_fiber_balance(fiber)` | Chain + channel balances |
| `self.wait_and_check_tx_pool_fee(rate, check)` | Wait for tx in pool |
| `self.get_ln_tx_trace(tx_hash)` | Trace on-chain LN txs |

**Amounts**: All in Shannon (1 CKB = 100000000). Use `hex()` for RPC.

**States**: Channel: `NEGOTIATING_FUNDING → CHANNEL_READY → SHUTTING_DOWN → CLOSED`. Payment: `Created → Inflight → Success/Failed`. Invoice: `Open → Received → Paid/Cancelled/Expired`.

For complete API reference, see [docs/references/api-reference.md](docs/references/api-reference.md).
For detailed test patterns, see [docs/references/test-patterns.md](docs/references/test-patterns.md).

## Test style: simple, obvious, easy to maintain

New and refactored integration tests should be **straightforward** (“stupid” is good): a reader should follow the flow without hunting through helpers or clever abstractions.

- **Linear setup and assertions**: Put steps in `setUp` / test methods in order. Avoid one-off private helpers unless the same logic is reused across tests or files.
- **Obvious waits**: Prefer a plain `for` loop with `time.sleep(1)` and a clear timeout / `assert False, "…"` message over nested wait utilities when the condition is local to one test file.
- **Assert behavior, not prose**: Prefer checks on RPC results (e.g. `list_peers`, channel state, payment status). Do not assert on many alternate error substrings unless the product contract requires it; `pytest.raises(Exception)` is acceptable when only “must fail” matters.
- **Reuse the framework first**: Use `FiberTest` / `SharedFiberTest` helpers (`open_channel`, `send_payment`, `wait_*`, etc.) before adding new shared utilities in `framework/`.
- **Scope**: One file (or class) per feature or PR regression; a short top-of-file comment naming the PR or behavior is enough—no long essays.

---

## Test Coverage Gap Analysis: Fiber vs Bitcoin LND

The following is a systematic comparison between LND's integration test suite (160+ test cases) and Fiber's current coverage (500+ test methods). Gaps are categorized by priority.

For the complete gap analysis with recommended test cases, see [docs/references/gap-analysis.md](docs/references/gap-analysis.md).

### Critical Gaps (P0 - Must Fix)

| Gap Area | LND Coverage | Fiber Status | Impact |
|----------|-------------|--------------|--------|
| **Cooperative close with pending TLCs** | `testCoopCloseWithHtlcs`, `testCoopCloseWithHtlcsWithRestart` | `shutdown_channel/` has close-path tests, but pending-TLC coop-close remains only in commented `test_pending_tlc.py` | Fund loss risk |
| **Channel update tests** | `testUpdateChanStatus`, `testSendUpdateDisableChannel` | `update_channel/` has baseline tests (e.g. `test_update_channel.py`, `test_enabled.py`), but disable/propagation coverage is still limited | Routing broken |
| **Offline payment delivery** | `testSwitchOfflineDelivery*` (4 tests) | `send_payment/offline/` has restart suites, but some cases are still stubs (`test_disconnect.py` empty, `test_send_payment_with_stop.py` pass) | Payment loss |
| **Payment error propagation** | `testHtlcErrorPropagation`, `testSendToRouteErrorPropagation` | No dedicated error propagation test | Silent failures |
| **Channel reestablishment** | `testDataLossProtection` | No channel reestablish test after disconnect | State corruption |

### High Priority Gaps (P1)

| Gap Area | LND Coverage | Fiber Status | Recommended Tests |
|----------|-------------|--------------|-------------------|
| **Gossip protocol sync** | `testGraphTopologyNotifications`, `testNodeAnnouncement` | No gossip sync validation tests | Test gossip message propagation, stale message handling |
| **Payment retry & backoff** | Built-in retry logic (DEFAULT_PAYMENT_TRY_LIMIT=5) | No explicit retry behavior test | Test retry after transient failures, backoff timing |
| **Max pending channels** | `testMaxPendingChannels` | No test | Test concurrent channel opens exceed limit |
| **Channel balance accounting** | `testChannelBalance`, `testChannelUnsettledBalance` | Balance checked incidentally, no dedicated test | Test balance accuracy during TLC lifecycle |
| **Invoice subscription/streaming** | `testInvoiceSubscriptions` | No subscription test | Test real-time invoice state notifications |
| **List payments query** | `testListPayments` | Only `get_payment` tested | Test payment history query, filtering |
| **Connection timeout** | `testNetworkConnectionTimeout` | No timeout test | Test peer connection timeout behavior |
| **Reconnect after address change** | `testReconnectAfterIPChange` | No IP change test | Test node reconnection after address update |

### Medium Priority Gaps (P2)

| Gap Area | LND Coverage | Fiber Status | Recommended Tests |
|----------|-------------|--------------|-------------------|
| **Revoked close retribution (remote hodl)** | `testRevokedCloseRetributionRemoteHodl` | `test_revert_tx.py` covers basic case only | Test with pending TLCs during revoked close |
| **Circuit persistence** | `testSwitchCircuitPersistence` | No test | Test payment circuit survives node restart |
| **Payment address mismatch** | `testWrongPaymentAddr` | No test | Test payment with wrong payment secret |
| **Funding expiry edge cases** | `testFundingExpiryBlocksOnPending`, `testFundingManagerFundingTimeout` | `test_funding_timeout.py` basic only | Test various funding timeout scenarios |
| **Max channel size** | `testMaxChannelSize`, `testWumboChannels` | No max size test | Test channel size limits |
| **Hold invoice persistence** | `testHoldInvoicePersistence` | `test_settle_invoice.py` has restart test | Enhance with multi-hop hold persistence |
| **Sphinx replay persistence** | `testSphinxReplayPersistence` | No test | Test onion packet replay protection |
| **Async bidirectional payments** | `testBidirectionalAsyncPayments` | `test_send_payment_each_other` partial | Test high-throughput bidirectional stress |
| **Fee estimation (route)** | `testEstimateRouteFee` | `test_dry_run.py` basic | Enhance dry_run with multi-hop fee estimation |

### Fiber-Specific Gaps (Features in code but not tested)

| Feature | Source Location | Current Test Status |
|---------|----------------|-------------------|
| `max_tlc_number_in_flight` enforcement | channel.rs (max 125, system 253) | open_channel test only, no enforcement test during payment |
| `max_tlc_value_in_flight` enforcement | channel.rs | open_channel test only, no enforcement test during payment |
| Custom records (max 2KB) | payment.rs | `test_custom_records.py` basic, no overflow test |
| Trampoline MPP restriction | payment.rs (only 1 hop with MPP) | No test for this constraint |
| Payment `max_parts` limit | payment.rs (PAYMENT_MAX_PARTS_LIMIT) | `test_max_parts.py` is empty (pass) |
| Gossip message ordering | gossip.rs | No test |
| Gossip stale message handling | gossip.rs (SOFT_BROADCAST_MESSAGES_CONSIDERED_STALE_DURATION) | No test |
| Watchtower external (standalone) | config: standalone_watchtower_rpc_url | No test |
| CCH order expiry | cch/ (DEFAULT_ORDER_EXPIRY_DELTA_SECONDS) | No test |
| CCH order pruning | cch/ (PRUNE_DELAY_SECONDS = 21 days) | No test |
| CCH fee calculation | cch/ (base_fee_sats, fee_rate_per_million_sats) | No test |
| Biscuit token time-based validation | rpc/biscuit.rs | No time-based auth test |
| Channel funding_timeout_seconds | channel.rs | Basic test, no edge cases |
| TLC waiting ACK timeout (30s) | channel.rs | No test |
| Commitment number sequence validation | channel.rs | No test |
| Network max_service_protocol_data_size (130KB) | network.rs | No test |
| Payment session retry from Failed state | payment.rs | No test |
| Graph cursor pagination | graph.rs (default 500) | `test_graph_nodes.py` tests pagination, graph_channels not tested |

---

## Writing New Tests for PR Regression

When a new PR lands, follow this workflow:

### 1. Identify Changed Components

```bash
# Check what changed
git diff develop..PR_BRANCH --stat
# Focus on fiber-lib/src/ changes
git diff develop..PR_BRANCH -- fiber/fiber-lib/src/
```

### 2. Map Changes to Test Categories

| Changed File | Test Directory |
|-------------|---------------|
| `fiber/channel.rs` | `open_channel/`, `shutdown_channel/`, `update_channel/`, `list_channels/` |
| `fiber/payment.rs` | `send_payment/`, `send_payment_with_router/` |
| `fiber/network.rs` | `connect_peer/`, `disconnect_peer/`, general integration |
| `fiber/invoice.rs` | `new_invoice/`, `get_invoice/`, `settle_invoice/`, `cancel_invoice/` |
| `fiber/graph.rs` | `graph_channels/`, `graph_nodes/`, `build_router/`, `send_payment/path/` |
| `fiber/gossip.rs` | `graph_channels/`, `graph_nodes/` (gossip sync) |
| `watchtower/` | `watch_tower/`, `watch_tower_wit_tlc/`, `watch_tower_debug/` |
| `cch/` | `cch/` |
| `rpc/` | Corresponding feature directory |

### 3. Test Template for New PR

```python
import time
import pytest
from framework.basic_fiber import FiberTest

class TestPR<number>(FiberTest):
    """
    PR-<number>: <title>
    Test coverage for: <brief description of changes>
    """

    def test_<feature>_happy_path(self):
        """Test the normal/expected behavior introduced by this PR."""
        # Setup
        self.open_channel(self.fiber1, self.fiber2, 200 * 100000000, 100 * 100000000)
        # Execute the new feature
        # Assert expected behavior

    def test_<feature>_edge_case(self):
        """Test boundary conditions."""
        pass

    def test_<feature>_error_handling(self):
        """Test error cases."""
        with pytest.raises(Exception) as exc_info:
            # Trigger error condition
            pass
        assert "expected error" in exc_info.value.args[0]

    def test_<feature>_backward_compatible(self):
        """Ensure existing functionality still works."""
        pass
```

### 4. Test Naming Convention

- File: `test_cases/fiber/devnet/<category>/test_<feature>.py`
- Class: `Test<Feature>(FiberTest)` or `<FeatureName>(FiberTest)`
- Method: `test_<scenario_description>(self)`

### 5. CI Integration

Add new test to `Makefile` test targets if new category. Existing categories auto-discover.

---

## Running Tests

```bash
# Specific test
pytest test_cases/fiber/devnet/open_channel/test_funding_amount.py -v -s

# Specific method
pytest test_cases/fiber/devnet/open_channel/test_funding_amount.py::FundingAmount::test_funding_amount_ckb_is_zero -v -s

# All devnet
make fiber_test

# With HTML report
python -m pytest test_cases/fiber/devnet/ --html=report/report.html
```
