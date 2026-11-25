# PR #1775 Biscuit watchtower RPC 日志回归

## 导读

- **这个改动要做什么**：删除正常鉴权请求每次都会写出的一条旧 `WARN ... fetch`。
- **行为变化**：有效 Biscuit 的 watchtower RPC 仍成功，且该请求不再产生旧 WARN。
- **最怕出什么问题**：日志修复误伤鉴权或处理器，或者测试没有触发目标代码就误报“无 WARN”。
- **能测到什么**：真实 FNN 的 HTTP JSON-RPC 响应与本次请求后的进程日志片段。
- **测不到的**：成功响应不能证明内部注入 id 的精确值，也不能证明所有方法或级别均不记录 node id。

依据：[Fiber PR #1775](https://github.com/nervosnetwork/fiber/pull/1775)；[任务验收结论](https://github.com/nervosnetwork/acceptance-internal/issues/1751#issuecomment-7126878959)。固定 base `b65cf44d6ad638cffa84641abe36292af12fc35a`、head `dbfe98cf42b2b624b2ac106be8265b645be61aca`。

## 需要你决定

无；两行已在任务 G1/G2 确认。

## 用例

公共前置：真实 FNN 启用 Biscuit；有效 token 同时含 watchtower 写权限与 node id；调用需要 RPC context 的 `remove_watch_channel`。每条用例的节点启动与检查约需 2 至 3 分钟。

<!-- TEST-CASES-BEGIN -->
| 用例 | 场景 | 预期结果 | 防止的问题 | 优先级 |
| --- | --- | --- | --- | --- |
| `F1775-01` | - [x] 使用有效 token 调用参数合法的 `remove_watch_channel` | HTTP 200，JSON-RPC 响应精确为 `result: null` 且无 `error` | 删除旧 WARN 时误改鉴权或 handler 路径 | P1 |
| `F1775-02` | - [x] 同一有效请求前记录日志偏移量，请求成功后只读取新增片段 | head 不再出现旧 `WARN fnn::rpc::biscuit: fetch`；base 同条件应出现 | 正常认证流量持续刷旧 WARN，或日志断言在未触发路径时假通过 | P1 |
<!-- TEST-CASES-END -->

## 测试树

<!-- TEST-TREE-BEGIN -->
- 有效 Biscuit 请求经过需要 node id 上下文的 watchtower RPC
  - 响应保持成功：`F1775-01` [primary] -> `SPEC-01`
  - 旧 WARN 消失：`F1775-02` [primary] -> `SPEC-02`
- [not_applicable] — 内部注入 id 的精确值未通过公开 RPC 返回，不由成功响应推断。
<!-- TEST-TREE-END -->

## Spec

### SPEC-01：正常鉴权请求仍到达处理器

- Condition: 有效 token 含 watchtower 写权限与 node id，调用 `remove_watch_channel`。
- Expected: 请求通过鉴权并正常执行。
- Observable: HTTP 200 与完整 JSON-RPC `result: null`，没有 `error`。
- Basis: [PR #1775 保留鉴权路径](https://github.com/nervosnetwork/fiber/pull/1775)。
- Source: explicit
- Testing: required

### SPEC-02：正常请求不再刷旧 WARN

- Condition: 同一请求成功通过鉴权和 node id 提取。
- Expected: head 不产生旧 `WARN fnn::rpc::biscuit: fetch`。
- Observable: 请求前记偏移，只检查新增 FNN 进程日志；固定 base 是阳性对照。
- Basis: [PR #1775 删除的日志语句](https://github.com/nervosnetwork/fiber/pull/1775)。
- Source: explicit
- Testing: required

## 验证设计

- 独立预期：有效 token 的权限与 node id 输入按产品约定构造；旧 WARN 的精确目标来自 PR 删除行，base 为日志阳性对照。
- 实际入口与观察：真实 FNN 的 `remove_watch_channel` HTTP 请求及新日志片段；固定 head 两条 Python 用例分别通过（约 137 秒、139 秒），base 请求成功但旧 WARN 出现一次，使第二条按预期失败。
- 证明边界：只排除这一条旧 WARN，不扩大成所有 RPC/所有日志级别均不记录 node id。
