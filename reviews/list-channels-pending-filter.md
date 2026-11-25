# PR #1774 pending 通道过滤

## 导读

- **这个改动要做什么**：`list_channels(only_pending=true)` 在读取完整记录前排除已就绪通道。
- **行为变化**：已就绪通道仍不应出现在 pending 结果中，未接受的 opening 通道仍应可见；默认列表不受影响。
- **最怕出什么问题**：优化后误列 ready 通道，或漏列调用方需要处理的 opening 通道。
- **能测到什么**：真实 FNN JSON-RPC 返回的通道 ID 与状态。
- **测不到的**：完整记录读取次数只能由 Fiber 产品侧定向测试验证；Python 黑盒不以耗时推断读次数。pending→ready 瞬时窗口已由 RD 接受为非阻断限制，不纳入长期自动化断言。

依据：[Fiber PR #1774](https://github.com/nervosnetwork/fiber/pull/1774)；[任务结论及范围调整](https://github.com/nervosnetwork/acceptance-internal/issues/1750#issuecomment-7128266239)。固定验收 head `695776b1f9dd2d960e2f50ee3ca8dec748121d39`。

## 需要你决定

无；本行已在任务 G1/G2 确认，极端瞬时窗口的豁免范围也已由 RD 确认。

## 用例

公共前置：关闭自动接受；一条公共通道已手动接受并进入 `ChannelReady`，同一对端的另一条合法 opening 通道尚未接受。按已知 channel ID 比较 `only_pending` 和默认查询结果。节点初始化、通道确认与轮询约需 2 分钟。

<!-- TEST-CASES-BEGIN -->
| 用例 | 场景 | 预期结果 | 防止的问题 | 优先级 |
| --- | --- | --- | --- | --- |
| `F1774-01` | - [x] 同一调用方有一条 ready 和一条未接受的 opening 通道；调用 `list_channels(only_pending=true)` 与默认列表 | pending 结果的 ID 集合恰为 opening ID、状态为 `NegotiatingFunding`；默认列表仍含 ready ID | 前置过滤误列 ready 或漏列 opening | P1 |
<!-- TEST-CASES-END -->

## 测试树

<!-- TEST-TREE-BEGIN -->
- 稳定通道状态下的 pending 结果集合：`F1774-01` [primary] -> `SPEC-01`
- [not_applicable] — 完整记录读取计数需要产品内部观察，不符合本仓库黑盒用例偏好。
- [not_applicable] — 精确控制 pending→ready 两次读取的交错需要测试钩子，且该瞬时窗口已被接受为非阻断限制。
<!-- TEST-TREE-END -->

## Spec

### SPEC-01：pending 过滤只返回仍在开通中的通道

- Condition: 同一对端同时有一条 ready 和一条未接受的 opening 通道。
- Expected: `only_pending=true` 只返回 opening；默认列表仍包含 ready。
- Observable: `list_channels` 的 channel ID 集合与状态字段。
- Basis: [PR #1774 目标](https://github.com/nervosnetwork/fiber/pull/1774)与 [API pending 定义](https://github.com/nervosnetwork/fiber/blob/695776b1f9dd2d960e2f50ee3ca8dec748121d39/crates/fiber-json-types/src/channel.rs#L362-L366)。
- Source: explicit
- Testing: required

## 验证设计

- 独立预期：用已知 open/ready channel ID 形成预期集合，不从被测 `only_pending` 结果反推。
- 实际入口与观察：真实 CKB/FNN 上通过 `open_channel`、`accept_channel`、`list_channels` 构造并读取状态；等待上限 120 秒，固定 head 曾 `1 passed`（112.27 秒）。
- 证明边界：只证明稳定状态下的结果集合；不证明少读完整记录，也不把已接受的极端状态转换窗口当成门禁。
