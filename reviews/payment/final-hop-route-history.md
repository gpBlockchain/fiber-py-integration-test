# PR #1752 末跳错误路由记忆用例

## 导读

- **这个改动要做什么**：收款节点因最终 TLC 金额或到期时间错误拒绝支付时，让付款节点记住真正送来该 TLC 的末跳通道，而不是误伤前一条通道。
- **行为变化**：三节点及更长路由中，失败记忆由倒数第二条通道移到进入收款节点的通道；前面的通道记为成功。双节点直连支付仍停止重试并标记收款节点失败。
- **最怕出什么问题**：后续支付仍避开正常的上游通道、重复走出错的末跳通道，或直连错误后的外部终态发生漂移。
- **能测到什么**：本项目的真实 FNN 节点加 Python JSON-RPC 可构造指定末跳的失败支付，再用一笔新的自动路由支付及两条并行末跳通道的余额变化观察路由记忆。不能把手动路由支付本身当成自动改道的证明。
- **测不到的**：release RPC 不返回内部 `PaymentHistory` 的精确 pair 条目；`get_payment.routers` 只在 debug 构建提供，因此不以它或日志文本作为主判据。前段通道被记为“成功”的精确金额只由上游单测支持，黑盒只能证明它未阻断后续路由。

依据：[PR #1752](https://github.com/nervosnetwork/fiber/pull/1752)，固定 base `21c4dfbdbd48ca3eac55332c9f1cc3e241b1582e`、head `06b21b03721e05733ef9cc9323cbffea8ab91101`；差异仅为 `crates/fiber-lib/src/fiber/history.rs` 与其单测。下述黑盒构造来自该 head 的 `payment.rs`、`channel.rs`、`graph.rs` 实现观察，已在两个固定版本的 release FNN 上执行；issue 自动生成的“protocol schema”标签不是实际 diff 范围。

## 已决定

1. 以真实 FNN、Python JSON-RPC 和通道余额作为黑盒验收层；不把 debug-only `routers` 或内部 pair map 当作结果。
2. 纳入双节点直连的外部失败终态兼容场景，不声称已读取内部 `fail_node` 或精确重试次数。
3. 本仓库 `AGENTS.md` 的 Case preferences 已明确真实节点与公开接口优先；本批用例按此执行。

## 用例

公共约定：每个场景使用独立 `FiberTest` 环境。多跳拓扑为付款 A → 中继 B → 收款 C：A-B 只有一条公共通道，B-C 有两条可用公共通道 BC1/BC2，金额、容量和 graph 同步事先确认；先分别用显式 `build_router` 确认 BC1/BC2 均可路由。再用 `send_payment_with_router` 固定 BC1 制造**一次**最终错误；该手动路由支付不自动重试。等待它终态 Failed、错误 TLC 进入 `RemoveAckConfirmed` 且待处理余额归零后，用新的 payment hash 发起正常 `send_payment`；以 RPC 状态及按 channel outpoint 读取的 A-B/BC1/BC2 余额判断新支付是否经 BC2，避免依赖 release 版本缺失的 `routers`。错误支付的收款 invoice 保持 Open；后续正常支付用新 keysend，互不复用 hash。验收二进制与源码都固定在 PR head；base 对照有助于确认判据区分度。

<!-- TEST-CASES-BEGIN -->
| 用例 | 场景 | 预期结果 | 防止的问题 | 优先级 |
| --- | --- | --- | --- | --- |
| `FHP-01` | - [x] C 建立金额 X 的非 MPP invoice；A 不传 invoice 文本，仅用其 payment hash 与小于 X 的金额，手动指定 A-B-BC1 后发送；失败 TLC 无未结算余额后 A 用新 hash 自动发送有效 keysend | 首笔在 C 处报 `FinalIncorrectTlcAmount`、invoice 仍 Open 且无结算余额变化；后续 keysend 成功，A-B 与 BC2 的余额沿支付方向变化、BC1 不变 | 把 A-B 误记为失败，导致唯一上游路径不可用，或继续复用 BC1 | P0 |
| `FHP-02` | - [x] C 建立 `final_expiry_delta` 为允许上限 14 天的非 MPP invoice；A 不传 invoice 文本，以正确金额/其 payment hash 和默认较短最终到期时间手动指定 A-B-BC1；失败 TLC 无未结算余额后 A 用新 hash 自动发送有效 keysend | 首笔在 C 处报 `FinalIncorrectExpiryDelta`、invoice 仍 Open 且无结算余额变化；后续 keysend 成功，A-B 与 BC2 的余额沿支付方向变化、BC1 不变 | 到期时间错误仍错误惩罚 A-B，或两个错误码只修复其一 | P0 |
| `FHP-03` | - [x] 在独立的两条直连通道中用两个付款方的普通 `send_payment` 分别构造金额不足与到期不足；到期场景将付款的 `tlc_expiry_limit` 设为 48 小时，低于 invoice 的 14 天要求；观察支付结束与通道状态 | 分别返回 `FinalIncorrectTlcAmount`、`FinalIncorrectExpiryDelta`，两笔错误支付均进入终态 Failed，不发生结算余额变化；直连通道保持 ChannelReady | 多跳分支修复时改变了直连支付的外部失败结果 | P1 |
<!-- TEST-CASES-END -->

## 测试树

<!-- TEST-TREE-BEGIN -->
- 收款节点报告最终 TLC 错误，路由至少三节点
  - 金额不足：`FHP-01` [primary] -> `SPEC-01`
  - 最终到期不足：`FHP-02` [primary] -> `SPEC-01`
- 只有两节点的直连路由：`FHP-03` [primary] -> `SPEC-02`
- [not_applicable] — 上游 pair 的内部 `success_amount` 与时间戳不符合 Python JSON-RPC 黑盒观察层；release RPC 不暴露，PR 新增 Rust 单测检查内部 map。
- [not_applicable] — 直连 `fail_node` 条目和精确重试次数不符合 Python JSON-RPC 黑盒观察层；release RPC 不暴露，直连 Case 只检验最终外部状态。
- [unanalysed] — 历史记忆跨重启的持久化及衰减时间未在此 PR 修改；若扩展范围，先审查存储加载和衰减接口再设计用例。
- [unanalysed] — MPP/Trampoline 包装后的同类错误有不同的归属与重试路径；需另对相应协议切片分析。
<!-- TEST-TREE-END -->

## Spec

### SPEC-01：多跳最终错误归因到末跳

- Condition: 经至少一条中继通道到达收款节点；收款节点返回 `FinalIncorrectTlcAmount` 或 `FinalIncorrectExpiryDelta`。
- Expected: 进入收款节点的通道被记为失败，之前的通道保留可用；之后的自动选路可避开失败末跳，改用另一条末跳通道。
- Observable: 手动路由失败的错误码与 invoice 状态；新自动支付的终态、A-B/BC1/BC2 通道余额；base/head 对照。
- Basis: [PR 描述及固定 diff](https://github.com/nervosnetwork/fiber/pull/1752/files)明确声明末跳惩罚与前段成功；后续选路影响是 `PaymentHistory::eval_probability` 的实现推论。
- Source: explicit
- Testing: required

### SPEC-02：直连最终错误仍终止

- Condition: 付款节点与收款节点直接相连，收款节点返回上述最终错误。
- Expected: 该次支付最终失败，通道仍处于 ChannelReady 且无结算余额变化；内部 `fail_node` 与精确重试次数不纳入本黑盒结论。
- Observable: 支付终态、invoice 状态、通道状态及结算后余额。
- Basis: [PR 描述](https://github.com/nervosnetwork/fiber/pull/1752)明示直连路径不变。
- Source: explicit
- Testing: required

## 验证设计

- **独立预期**：按 invoice 约定构造金额/到期界限，而不是从待测日志复制预期；预先通过显式 `build_router` 与 graph/channel RPC 确认 BC1、BC2 均可用。多跳到期反例将收款 invoice 的 `final_expiry_delta` 设为 RPC 允许的 14 天上限（1,209,600,000 ms）：付款不携带 invoice，所构造 TLC 的绝对 expiry 被付款端自身的 14 天 `tlc_expiry_limit` 限住；三节点路由还必须给中继预留正的转发到期差，因此最终 TLC 的 expiry 严格低于该上限，即使加入随机隐私预留也不足以满足收款端要求。直连到期反例则给普通 `send_payment` 显式设置 48 小时 `tlc_expiry_limit`，高于默认最终到期与最小随机预留之和，低于收款端 14 天要求。错误码由收款端既有验证规则提供，随后以新 hash 的正常支付及实际余额移动验证路由选择。
- **实际入口与观察**：运行真实 FNN、CKB dev node，使用 `new_invoice`、`build_router`、`send_payment_with_router`、`get_payment`、`send_payment`、`list_channels`。等待错误 TLC 清除与通道余额稳定，再发送正常支付；在 base 与 head 上使用隔离、等价的拓扑和输入作反事实对照。
- **证明边界**：手动路由仅保证第一笔错误经过 BC1，不能证明历史起效；第二笔必须走自动路由，且通过 BC2 的余额变化证明实际末跳。`get_payment.routers` 为 debug-only，不作为 release 判据。若付款端先行拒绝请求、错误不是目标最终错误码、任一末跳在首笔前不可用，均判为 fixture 未到达目标层，不算产品失败。`FHP-03` 只证明直连失败终态等外部行为，不宣称读到了内部 `fail_node` 或重试次数。

## 执行证据

- head `06b21b03` 的 release `fnn` SHA-256 为 `0c5a789af9f6bdee38444d41113b0ccb2c9528fab0515dfbc8a1f5f2362938d2`。在隔离验收工作树运行整文件，原生 JUnit `3 passed / 0 failed / 0 skipped`，pytest 和收据工具退出码均为 0，输入未漂移。完整结果已[同步到任务 issue](https://github.com/nervosnetwork/acceptance-internal/issues/1717#issuecomment-7151698493)；运行产物不纳入本次代码提交。三条均使用真实 CKB/FNN 和 Python JSON-RPC；`FHP-01/02` 的第二笔自动支付分别经 BC2 成功，`FHP-03` 的两笔直连错误均终止且余额不变。
- base `21c4dfbd` 以同一 Python 用例和独立 release FNN 对照：`pytest -k bad_final_ -q --tb=short` 的两条多跳测试都在首笔目标错误与无结算断言通过后，于第二笔自动 `send_payment` 返回 `PathFind error: no path found`（预期红灯）；直连测试单独运行 `1 passed`。这是外部行为红绿对照，不宣称读取了内部 pair map。
