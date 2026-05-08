# Fiber Python Integration Test - AI Development Guide

This repository contains Python integration tests for Fiber Network Node (FNN), a Lightning-style payment network on Nervos CKB.

This file is the primary development guide for AI agents working in this repository. Treat it as a living playbook: when a test fails because of a wrong assumption or missing domain knowledge, update this guide with the lesson learned so future AI development can avoid repeating the same mistake.

## Core Rule for AI Agents

Before adding or changing tests, always follow this loop:

1. Understand the product behavior or upstream PR being tested.
2. Search existing tests for similar flows.
3. Reuse existing helper methods and test patterns first.
4. Write the smallest clear regression test that proves the behavior.
5. If the test fails, diagnose whether the failure is product behavior, test timing, missing chain progress, wrong topology, or wrong assertion.
6. Update this guide with any reusable lesson.

Do not guess complex Fiber or CKB on-chain behavior. Look for an existing passing test and copy the proven flow.

## Project Structure

```text
framework/
├── basic.py                  # CkbTest base, CKB node lifecycle
├── basic_fiber.py            # FiberTest base, 2 Fiber nodes + common helpers
├── basic_share_fiber.py      # SharedFiberTest base, shared env across tests
├── basic_fiber_with_cch.py   # FiberCchTest, CCH/BTC/LND helpers
├── fiber_rpc.py              # JSON-RPC Fiber client
├── fnn_cli.py                # fnn-cli wrapper
├── test_fiber.py             # Fiber node lifecycle
├── test_node.py              # CKB node lifecycle
└── helper/                   # miner, ckb-cli, contract, UDT, tx helpers

test_cases/fiber/devnet/
├── open_channel/
├── shutdown_channel/
├── send_payment/
├── watch_tower/
├── watch_tower_wit_tlc/
├── cch/
└── ...
```

## Base Test Classes

Use `FiberTest` for most new regression tests. It creates a fresh environment for each test method and is safer for destructive scenarios such as force close, revoke, shutdown, restart, and on-chain settlement.

Use `SharedFiberTest` only when several tests intentionally share the same topology and state. Shared tests are faster but can become order-dependent.

## Common Helpers

| Helper | Purpose |
|---|---|
| `self.open_channel(f1, f2, bal1, bal2, udt=None)` | Open channel and wait until ready |
| `self.send_payment(f1, f2, amount, wait=True, udt=None)` | Keysend payment with retry |
| `self.send_invoice_payment(f1, f2, amount, wait=True, udt=None)` | Invoice payment with retry |
| `self.wait_for_channel_state(client, pubkey, state, include_closed=False, channel_id=None)` | Wait channel state |
| `self.wait_payment_state(fiber, payment_hash, status)` | Wait payment state |
| `self.wait_invoice_state(fiber, payment_hash, status)` | Wait invoice state |
| `self.generate_account(ckb_balance, udt_owner_private_key=None, udt_balance=...)` | Create funded account |
| `self.start_new_fiber(private_key)` | Start extra Fiber node |
| `self.generate_random_preimage()` | Generate payment preimage |
| `self.get_commit_cells()` | Inspect force-close commitment cells |
| `self.node.getClient().generate_epochs("0x1", wait_time=0)` | Advance CKB epochs |

Amounts are in Shannon. Use `hex()` for RPC amount fields.

## State Names

Channel states commonly used in tests:

```text
NegotiatingFunding / ChannelReady / ShuttingDown / Closed
```

Payment states:

```text
Created / Inflight / Success / Failed
```

Invoice states:

```text
Open / Received / Paid / Cancelled / Expired
```

Always confirm the exact state string from existing tests or RPC output before asserting.

## Test Writing Rules

Keep tests simple and linear. A future developer should be able to read the test from top to bottom and understand the scenario without jumping through many helper layers.

Preferred pattern:

```python
class TestFeature(FiberTest):
    def test_specific_behavior(self):
        # 1. Build topology
        # 2. Create invoice / payment / channel state
        # 3. Trigger the behavior
        # 4. Advance chain or wait for async work if needed
        # 5. Assert final RPC-visible state
```

Rules:

- Put one regression feature in one file.
- Prefer existing helpers in `FiberTest` before adding framework helpers.
- Prefer direct RPC assertions: `get_payment`, `get_invoice`, `list_channels`, `list_peers`.
- Do not assert too early after asynchronous or on-chain operations.
- Use explicit waits with clear timeouts.
- For PR regressions, mention the upstream PR or issue in the test class docstring.

## PR Regression Workflow

When adding tests for a Fiber PR:

1. Read the PR summary and changed files.
2. Identify the changed component.
3. Map the component to a test category.
4. Search existing tests in that category.
5. Copy the closest proven setup/chain-progress pattern.
6. Add a focused regression test.
7. Update this guide if the PR exposes a new testing pitfall.

Component mapping:

| Changed area | Preferred test directory |
|---|---|
| `fiber/channel.rs` | `open_channel/`, `shutdown_channel/`, `list_channels/` |
| `fiber/payment.rs` | `send_payment/`, `send_payment_with_router/` |
| `fiber/network.rs` | `connect_peer/`, `disconnect_peer/` |
| `fiber/invoice.rs` | `new_invoice/`, `get_invoice/`, `settle_invoice/`, `cancel_invoice/` |
| `fiber/graph.rs`, `fiber/gossip.rs` | `graph_channels/`, `graph_nodes/`, `send_payment/path/` |
| watchtower / on-chain settlement | `watch_tower/`, `watch_tower_wit_tlc/` |
| CCH | `cch/` |

## Critical Lesson: Force Shutdown Is an On-Chain Settlement Flow

Do not treat `shutdown_channel({"force": True})` as a simple RPC state transition.

Force shutdown creates on-chain commitment/settlement transactions. If a test involves force close, pending TLCs, watchtower, or `settle_invoice` after force close, final payment/invoice/channel states may not converge until:

1. peers/watchtower observe the force-close transaction,
2. the preimage or settlement transaction is submitted,
3. CKB epochs advance far enough for the settlement/unlock path,
4. commitment cells are consumed.

Wrong pattern:

```python
fiber.get_client().shutdown_channel({"channel_id": channel_id, "force": True})
payee.get_client().settle_invoice({"payment_hash": payment_hash, "payment_preimage": preimage})
self.wait_payment_state(payer, payment_hash, "Success")  # may be too early
```

Correct pattern:

```python
# 1. Force-close the channel.
fiber.get_client().shutdown_channel({"channel_id": channel_id, "force": True})

# 2. Give peers/watchtower time to observe the force-close transaction.
time.sleep(10)

# 3. Reveal the preimage / settle held invoice if needed.
payee.get_client().settle_invoice({
    "payment_hash": payment_hash,
    "payment_preimage": preimage,
})

# 4. Advance epochs so settlement/unlock conditions can be reached.
self.node.getClient().generate_epochs("0x1", wait_time=0)

# 5. Wait until force-close commitment cells are consumed.
while len(self.get_commit_cells()) != 0:
    time.sleep(10)

# 6. Now assert final states.
self.wait_payment_state(payer, payment_hash, "Success")
self.wait_invoice_state(payee, payment_hash, "Paid")
```

Use the existing watchtower tests as the source of truth for force-close flows. If a force-close regression test flakes or times out, first check whether the chain was advanced and commit cells were fully consumed before the final assertion.

## AI Failure Recovery and Experience Summary

When an AI-generated test fails, do not only patch the code. Also summarize the reusable lesson in this file.

Use this checklist:

```text
Failure summary:
- What failed?
- Which assumption was wrong?
- Was the failure caused by timing, topology, chain progress, RPC usage, or actual product behavior?
- Which existing test shows the correct pattern?
- What rule should future AI agents follow?
```

Then update the relevant section of this guide.

Examples of lessons that should be recorded:

- Force shutdown requires on-chain epoch advancement and commit-cell cleanup before final assertions.
- Multi-hop payment tests may need explicit `trampoline_hops` or route hints.
- Hold invoices should assert `Received` before force close or settlement.
- UDT channel tests need correct `udt_type_script` and funded owner account.
- Restart tests must distinguish persisted state from in-memory actor state.

## Running Tests

Specific file:

```bash
pytest test_cases/fiber/devnet/watch_tower_wit_tlc/test_force_close_fulfill.py -v -s
```

Specific method:

```bash
pytest path/to/test_file.py::TestClass::test_method -v -s
```

All devnet tests:

```bash
make fiber_test
```

With HTML report:

```bash
python -m pytest test_cases/fiber/devnet/ --html=report/report.html
```

## Documentation Links

- API reference: `docs/references/api-reference.md`
- Test patterns: `docs/references/test-patterns.md`
- Lightning/Fiber concepts: `docs/references/lightning-concepts.md`
- Gap analysis: `docs/references/gap-analysis.md`
