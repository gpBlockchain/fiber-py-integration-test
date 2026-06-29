# June 2026 PR Regression Test Analysis

Date: 2026-06-09

Scope: PRs `#1371`, `#1363`, `#1387`, `#1388`, `#1386`, `#1385`, `#1384`,
`#1390`, `#1391`, `#1393`, `#1394`, `#1392`, `#1395`, `#1396`, `#1398`,
`#1400`, `#1404`, `#1401`, `#1409`, `#1407`, and `#1403`.

## Added Python Integration Tests

- `test_cases/fiber/devnet/accept_channel/test_auto_accept_pending_limits.py`
  - Covers PR #1371 per-peer pending channel cap through public
    `open_channel` and `list_channels(only_pending=true)`.
- `test_cases/fiber/devnet/send_payment/params/test_payment_rpc_limits.py`
  - Covers PR #1363 amount overflow rejection and PR #1398
    `list_payments.limit` clamping behavior.
- `test_cases/fiber/devnet/send_payment_with_router/test_router_overflow_and_trampoline_budget.py`
  - Covers PR #1392 trampoline fee budget behavior and PR #1409 explicit
    router overflow validation.
- `test_cases/fiber/devnet/open_channel_with_external_funding/test_external_funding_regressions.py`
  - Covers externally-funded channel behavior relevant to PR #1388, #1390,
    #1391, #1394, and #1404 through public RPC flows.
- `test_cases/fiber/devnet/watch_tower/test_invalid_watchtower_private_key.py`
  - Covers PR #1401 at the watchtower JSON-RPC boundary by sending a
    syntactically valid but secp256k1-invalid private key to
    `create_watch_channel`. A fixed binary must return a JSON-RPC error and
    keep serving RPC requests.

The target `fnn` binary is now available locally, so the new PR regression
tests are enabled and were run as integration tests.

Template support was also added to `source/fiber/dev_config_3.yml.j2` for:

- `fiber_to_be_accepted_channels_number_limit`
- `fiber_to_be_accepted_channels_bytes_limit`

## PR Mapping

| PR | Risk / behavior | Python coverage added | Result |
| --- | --- | --- | --- |
| #1371 | Pending channel count limit must include pending opens from same peer. | `TestAutoAcceptPendingLimits.test_per_peer_pending_channel_number_limit_is_enforced` | Passed. |
| #1363 | RPC-facing amount values must reject overflow instead of wrapping or panicking. | `TestPaymentRpcLimits.test_send_payment_rejects_amount_plus_fee_overflow`, `test_open_channel_rejects_amount_over_u64_for_ckb` | Passed. |
| #1387 | Mock-chain UDT funding context fix. Test-only Rust mock behavior, not observable in this Python devnet layer. | No new Python case. Existing upstream Rust test result in PR body: 1021 passed, 8 skipped. | Documented as Rust/mock-chain covered. |
| #1388 | Peer TxUpdate must not advance external funding before local signed submission. | `test_external_funding_does_not_progress_before_signed_submit` | Passed. |
| #1386 | `bitcoin` dependency bump. | No Python behavior-specific case. Covered by build/CI dependency resolution. | Documented as dependency-only. |
| #1385 | `hyper` dependency bump. | No Python behavior-specific case. Covered by build/CI dependency resolution and RPC smoke in other tests. | Documented as dependency-only. |
| #1384 | `log` dependency bump. | No Python behavior-specific case. Covered by build/CI dependency resolution. | Documented as dependency-only. |
| #1390 | Duplicate/stale post-submit TxComplete must be idempotent. | `test_external_funding_submit_then_restart_reaches_ready` exercises the public restart/race symptom. | Passed. |
| #1391 | Stale funding exclusions must be cleaned during external-funding handshake. | `test_external_funding_submit_then_restart_reaches_ready` | Passed. |
| #1393 | Comment-only update for synthetic funding tx explanation. | No runtime test needed. | Documented as no logic change. |
| #1394 | Stale funding exclusion leakage on peer TxUpdate. | `test_external_funding_uses_external_wallet_and_remains_payable` checks the external wallet path stays usable and payable. | Passed. |
| #1392 | Trampoline outer route fee must be deducted from trampoline fee budget. | `TestRouterOverflowAndTrampolineBudget.test_trampoline_budget_includes_outer_route_fee` | Passed. |
| #1395 | fiber-js worker startup log must redact secrets. | No Python FNN integration case. Should be verified in `fiber-js` unit/browser-worker tests or static source check. | Documented as JS/WASM-layer coverage needed. |
| #1396 | Wrong-chain inbound OpenChannel must be rejected before durable record persistence. | No Python case: public RPC cannot forge peer `OpenChannel.chain_hash`. | Documented as internal p2p/Rust coverage needed. |
| #1398 | Huge `list_payments.limit` must be clamped to 500. | `test_list_payments_huge_limit_is_clamped_and_paginates` | Passed. |
| #1400 | Individual gossip messages must validate chain_hash before store/cursor updates. | No Python case: public RPC cannot inject signed wrong-chain gossip messages. | Documented as internal p2p/Rust coverage needed. |
| #1404 | External funding peer input verification must use external lock script. | `test_external_funding_uses_external_wallet_and_remains_payable` | Passed. |
| #1401 | Watchtower RPC invalid secp256k1 private keys must return an error, not panic. | `TestInvalidWatchtowerPrivateKey.test_create_watch_channel_rejects_invalid_private_key_without_panic` | Passed with `fnn v0.9.0-rc3 (3bbf5ea 2026-06-11)`. |
| #1409 | Explicit router overflow validation must reject invalid routes before TLC dispatch. | `test_explicit_router_rejects_overflow_amount_before_tlc_dispatch` | Passed. |
| #1407 | Forwarded TLC hash_algorithm/payment_hash consistency. | No Python case: requires malicious onion/TLC mismatch injection below public RPC. | Documented as channel actor/Rust coverage needed. |
| #1403 | NodeAnnouncement `/p2p` peer ID mismatch must be rejected/stripped. | No Python case: public RPC cannot forge signed NodeAnnouncement addresses. | Documented as gossip/Rust coverage needed. |

## Verification

Performed locally:

```bash
.venv/bin/python -m compileall \
  test_cases/fiber/devnet/accept_channel/test_auto_accept_pending_limits.py \
  test_cases/fiber/devnet/send_payment/params/test_payment_rpc_limits.py \
  test_cases/fiber/devnet/send_payment_with_router/test_router_overflow_and_trampoline_budget.py \
  test_cases/fiber/devnet/open_channel_with_external_funding/test_external_funding_regressions.py
.venv/bin/python -m pytest --collect-only -q \
  test_cases/fiber/devnet/accept_channel/test_auto_accept_pending_limits.py \
  test_cases/fiber/devnet/send_payment/params/test_payment_rpc_limits.py \
  test_cases/fiber/devnet/send_payment_with_router/test_router_overflow_and_trampoline_budget.py \
  test_cases/fiber/devnet/open_channel_with_external_funding/test_external_funding_regressions.py
.venv/bin/python -m pytest -q -s \
  test_cases/fiber/devnet/accept_channel/test_auto_accept_pending_limits.py \
  test_cases/fiber/devnet/send_payment/params/test_payment_rpc_limits.py \
  test_cases/fiber/devnet/send_payment_with_router/test_router_overflow_and_trampoline_budget.py \
  test_cases/fiber/devnet/open_channel_with_external_funding/test_external_funding_regressions.py
.venv/bin/python -m compileall \
  test_cases/fiber/devnet/watch_tower/test_invalid_watchtower_private_key.py
.venv/bin/python -m pytest --collect-only -q \
  test_cases/fiber/devnet/watch_tower/test_invalid_watchtower_private_key.py
.venv/bin/python -m pytest -q -s \
  test_cases/fiber/devnet/watch_tower/test_invalid_watchtower_private_key.py
.venv/bin/python -m pytest -q -s \
  test_cases/fiber/devnet/accept_channel/test_auto_accept_pending_limits.py \
  test_cases/fiber/devnet/send_payment/params/test_payment_rpc_limits.py \
  test_cases/fiber/devnet/send_payment_with_router/test_router_overflow_and_trampoline_budget.py \
  test_cases/fiber/devnet/open_channel_with_external_funding/test_external_funding_regressions.py \
  test_cases/fiber/devnet/watch_tower/test_invalid_watchtower_private_key.py
```

Observed results:

- `compileall` passed for the moved PR regression test files.
- Focused `pytest --collect-only` collected the 9 PR regression tests.
- Focused integration run reported `9 passed in 332.86s (0:05:32)`.
- New PR #1401 test passed `compileall` and `pytest --collect-only`
  (`1 test collected in 0.33s`).
- Additional PR #1401 watchtower RPC run with `fnn v0.9.0-rc3`
  (`3bbf5ea 2026-06-11`) reported `1 passed in 49.07s`. The same test
  previously xfailed against `0.9.0-rc2` because the RPC connection was closed
  after `Invalid secret key: InvalidSecretKey` panic; rc3 now returns the
  expected JSON-RPC error and keeps serving `node_info`.
- Full focused rerun with `fnn v0.9.0-rc3 (3bbf5ea 2026-06-11)` reported
  `10 passed in 417.61s (0:06:57)`, covering #1371, #1363, #1388, #1390,
  #1391, #1394, #1392, #1398, #1404, #1401, and #1409 through the Python
  integration cases listed above.
- The first sandboxed run failed before test setup because `ckb-cli` could not
  update `/Users/xueyanli/.ckb-cli/keystore`; rerunning with elevated runtime
  permissions resolved the environment issue.

## Remaining Test Gaps

The following PRs need lower-level Rust, JS/WASM, or protocol-injection
coverage rather than Python integration coverage:

- #1387: mock-chain UDT funding context changes only affect
  `crates/fiber-lib/src/ckb/tests/test_utils.rs` and Rust mock-chain setup.
  Python devnet tests use the real CKB dev node and cannot observe this helper
  directly.
- #1393: comment-only refinement in the same Rust synthetic funding
  transaction helper; no runtime behavior changed.
- #1395: validate `fiber-js/src/fiber.worker.ts` logs only non-sensitive
  initialization fields. This is a fiber-js worker concern, outside the Python
  FNN integration harness.
- #1396: wrong-chain inbound `OpenChannel` validation happens on received p2p
  messages before `ChannelOpenRecord` persistence. Public `open_channel` RPC
  always uses the local chain hash and cannot forge a peer `OpenChannel`.
- #1400: wrong-chain individual gossip messages require constructing signed
  gossip payloads with mismatched `chain_hash`; public graph/RPC APIs only
  expose accepted graph state.
- #1403: mismatched `/p2p` peer IDs in `NodeAnnouncement` require forged signed
  node announcements. Public `connect_peer` and graph APIs cannot inject a
  malformed announcement from another node ID.
- #1407: malicious forwarded TLC hash mismatch requires channel actor or onion
  payload injection below normal `send_payment`.
