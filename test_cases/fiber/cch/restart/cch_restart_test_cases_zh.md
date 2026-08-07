# CCH 重启集成测试用例矩阵

本目录用于承载 CCH + LND + Fiber + watchtower 相关的重启恢复用例。用例 ID 采用 `CCH-Rxxx`，并保留与上层 `CCH-Txxx` 风险项的映射。

## 覆盖原则

- 重启测试必须证明最终资金状态、preimage、CCH order 状态和两侧 invoice/payment 状态一致，不能只断言 RPC 成功。
- 对当前 harness 无法精确控制的场景，只记录为 `Backlog`，不在 pytest 中放 `pass` 空壳；等 harness 补齐后再补真实步骤。
- `send_btc` 与 `receive_btc` 要分别覆盖，因为前者依赖 LND outgoing success/preimage，后者依赖 Fiber outgoing success/preimage。
- force shutdown / watchtower 场景按慢速 devnet/integration 测试处理，必须覆盖 CCH 停机、watchtower 停机、链上 preimage、timeout/refund、reorg 和重复观察。

## 用例全集

| Case ID | 方向 / 组件 | 场景 | 自动化状态 | pytest 落点 |
| --- | --- | --- | --- | --- |
| CCH-R001 | send_btc | `Pending` order 重启后恢复 Fiber incoming invoice tracking。 | Ready | `test_send_btc_restart.py::test_cch_r001_pending_order_restart_recovers_invoice_tracking` |
| CCH-R002 | send_btc | Fiber incoming accepted 后、LND outgoing send 前重启。 | Backlog | 需要 action-dispatch stop hook |
| CCH-R003 | send_btc | LND outgoing in-flight 时重启，不能重复支付。 | Ready | `test_send_btc_restart.py::test_cch_r003_restart_does_not_duplicate_inflight_lnd_payment` |
| CCH-R004 | send_btc | CCH down 期间 LND outgoing success，重启后恢复 preimage 并 settle Fiber。 | Ready | `test_send_btc_restart.py::test_cch_r004_recovers_lnd_success_during_cch_downtime` |
| CCH-R005 | send_btc | LND success/preimage 后、Fiber settle 前重启。 | Backlog | 需要 preimage-store 到 Fiber-settle 之间的 stop hook |
| CCH-R006 | send_btc | CCH down 期间 LND outgoing fail/timeout。 | Backlog | 需要可控 LND terminal failure 与 Fiber non-settle 断言 |
| CCH-R007 | send_btc | CCH down 期间本地 order expiry，但 LND 已 success。 | Backlog | 需要 expiry 控制和外部终态优先级断言 |
| CCH-R008 | send_btc | 重启后重复触发 outgoing action，必须幂等。 | Partial | R003 覆盖 inflight restart；direct replay 需要 dispatcher retry hook |
| CCH-R101 | receive_btc | LND hold invoice `Pending` order 重启后恢复 tracking。 | Ready | `test_receive_btc_restart.py::test_cch_r101_pending_order_restart_recovers_lnd_tracking` |
| CCH-R102 | receive_btc | LND hold invoice accepted 后、Fiber outgoing send 前重启。 | Backlog | 需要 LND accepted 到 Fiber send 之间的 stop hook |
| CCH-R103 | receive_btc | Fiber outgoing in-flight 时重启，不能重复发送。 | Backlog | 需要 standalone CCH 或 CCH-only stop control |
| CCH-R104 | receive_btc | CCH down 期间 Fiber success/preimage，重启后 settle LND。 | Backlog | 需要 standalone CCH 或 CCH-only stop control |
| CCH-R105 | receive_btc | Fiber success/preimage 后、LND settle 前重启。 | Backlog | 需要 Fiber preimage 到 SettleInvoice 之间的 stop hook |
| CCH-R106 | receive_btc | CCH down 期间 Fiber outgoing fail/timeout。 | Backlog | 需要可控 Fiber outgoing failure/timeout |
| CCH-R107 | receive_btc | CCH down 期间 order expiry，但 Fiber 已 success/preimage。 | Backlog | 需要 expiry 控制和 Fiber preimage 恢复断言 |
| CCH-R108 | receive_btc | 重启后重复触发 Fiber outgoing action，必须幂等。 | Backlog | 需要 dispatcher retry hook |
| CCH-R201 | LND | LND sender 在 outgoing in-flight 时重启。 | Ready | `test_lnd_restart.py::test_cch_r201_lnd_sender_restarts_during_outgoing_inflight` |
| CCH-R202 | LND | CCH 与 LND 同时重启后恢复 payment success。 | Backlog | 需要组合进程生命周期控制 |
| CCH-R203 | LND | LND invoice 节点在 hold invoice accepted 后重启。 | Backlog | 需要 hold accepted stop point |
| CCH-R204 | LND | LND invoice settled 后、CCH 观察前 LND 重启。 | Backlog | 需要 invoice settled 到 CCH observe 之间的 gap |
| CCH-R205 | LND | LND invoice expired/canceled 后重启。 | Backlog | 需要 invoice terminal failure gap |
| CCH-R206 | LND | LND RPC 短暂不可用后恢复。 | Backlog | 需要短暂 RPC outage 注入 |
| CCH-R301 | Fiber | hub Fiber / in-process CCH 重启后恢复 order/preimage。 | Partial | R001/R003/R004/R101 覆盖 hub restart；DB 边界断言待补 |
| CCH-R302 | Fiber | send_btc 的 Fiber payer 节点支付中重启。 | Backlog | 需要 payer restart 中断点 |
| CCH-R303 | Fiber | receive_btc 的 Fiber counterparty 支付中重启。 | Backlog | 需要 counterparty restart 中断点 |
| CCH-R304 | Fiber | Fiber incoming accepted 后 hub 重启。 | Backlog | 需要 incoming accepted 到 action handling 的 stop hook |
| CCH-R305 | Fiber | Fiber outgoing in-flight 时相关节点重启。 | Backlog | 需要 outgoing inflight restart harness |
| CCH-R306 | Fiber | Fiber payment success 后、CCH 消费 store-change 前重启。 | Backlog | 需要 store-change gap 注入 |
| CCH-R307 | Fiber / standalone CCH | standalone CCH WebSocket 断线重连后补齐 gap。 | Backlog | 需要 standalone CCH 和 WebSocket gap 控制 |
| CCH-R401 | watchtower | send_btc 已知 preimage，Fiber force shutdown 后链上 settle。 | Backlog | 需要 deterministic force shutdown / watchtower harness |
| CCH-R402 | watchtower | send_btc 无 preimage force shutdown，不能链上 settle。 | Backlog | 需要 timeout/refund 链上路径 |
| CCH-R403 | watchtower | receive_btc force shutdown 后链上 settlement 暴露 preimage。 | Backlog | 需要链上 settlement preimage 观测 |
| CCH-R404 | watchtower | receive_btc force shutdown 后 timeout/refund，不能 settle LND。 | Backlog | 需要 timeout/refund 链上路径 |
| CCH-R405 | watchtower | CCH down 期间 watchtower 观察到 settlement。 | Backlog | 需要 watchtower store/backfill 观测 |
| CCH-R406 | watchtower | watchtower down 期间链上 settlement，重启后 backfill。 | Backlog | 需要 watchtower restart/backfill harness |
| CCH-R407 | watchtower | CCH 和 watchtower 同时 down，链上状态已确认后恢复。 | Backlog | 需要组合 downtime 和链上 finality 控制 |
| CCH-R408 | watchtower | standalone watchtower 覆盖 built-in watchtower downtime。 | Backlog | 需要 standalone watchtower 模式 |
| CCH-R409 | watchtower | standalone watchtower RPC outage 后恢复或报告 gap。 | Backlog | 需要 RPC outage 注入 |
| CCH-R410 | watchtower | CCH offline 时 watchtower 使用已知 preimage settle。 | Backlog | 需要 CCH offline + watchtower preimage store |
| CCH-R411 | watchtower | CCH downtime 期间 watchtower 发现 preimage，CCH 重启后消费。 | Backlog | 需要 CCH 查询/消费 watchtower preimage 路径 |
| CCH-R412 | watchtower | 重复观察 settlement / 重复广播幂等。 | Backlog | 需要重复 indexer event 或 rebroadcast 注入 |
| CCH-R413 | watchtower | child-before-parent 或同区块 parent/child 后重启。 | Backlog | 需要 indexer order 控制 |
| CCH-R414 | watchtower | observed settlement 后 reorg，再重启。 | Backlog | 需要 CKB reorg harness |
| CCH-R415 | watchtower | old/revoked commitment 广播期间重启，不能误 success。 | Backlog | 需要 revoked commitment 场景 |
| CCH-R416 | watchtower | witness 中是错误 payment hash 的 preimage。 | Backlog | 需要 settlement witness 注入 |
| CCH-R501 | persistence | 连续多次重启，状态推进幂等。 | Backlog | 可基于 R001/R003/R004 扩展多轮 restart |
| CCH-R502 | persistence | 每个状态写 DB 边界后重启。 | Backlog | 需要 DB-write boundary fault injection |
| CCH-R503 | persistence | 外部动作成功但本地状态未写入前重启。 | Backlog | 需要外部 success / 本地 write gap 注入 |
| CCH-R504 | persistence | 重启后配置变化，旧 order 保持创建时语义。 | Backlog | 需要旧 order + 新配置组合 |
| CCH-R505 | persistence | 重启后 auth/cert/macaroon 配置恢复。 | Backlog | 需要 auth/cert/macaroon restart matrix |
| CCH-R506 | persistence | 重启后重复 tracker event 幂等。 | Backlog | 需要 duplicate tracker event injection |
| CCH-R507 | persistence | 重启后乱序 tracker event 不能回退状态。 | Backlog | 需要 out-of-order tracker event injection |
| CCH-R508 | persistence | 重启后外部 unknown/not-found 不应直接视为终态失败。 | Backlog | 需要 external unknown/not-found injection |

## 与原分析用例映射

- `CCH-T006` -> `CCH-R004`
- `CCH-T007` -> `CCH-R104`
- `CCH-T008` -> `CCH-R003` / `CCH-R008`
- `CCH-T009` -> `CCH-R401`
- `CCH-T010` -> `CCH-R403`
- `CCH-T011` -> `CCH-R404`
- `CCH-T012` -> `CCH-R405` / `CCH-R411`
- `CCH-T013` -> `CCH-R413`
- `CCH-T014` -> `CCH-R414`
- `CCH-T016` -> `CCH-R307`
