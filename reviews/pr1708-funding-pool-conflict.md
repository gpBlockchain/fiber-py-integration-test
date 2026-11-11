# PR #1708：funding 交易与交易池冲突时快速失败用例评审

评审范围：funding 交易广播时被 CKB 交易池以 `PoolRejectedRBF`（-1111，池中另一笔交易已花费本交易的某个输入）拒绝后的外部可见行为，以及失败后的重开能力。关账交易（closing）遇池冲突保持原等待行为的分支、-1107 重复广播分支另列，不计入本切片用例表。

源码版本：[PR #1708](https://github.com/nervosnetwork/fiber/pull/1708)；diff 取自 2026-11-10 GitHub API 快照（`gh pr diff 1708`）。本地 `~/project/fiber` 检出为旧版（`873dad25`，不含本 PR），涉及 `abort_funding` 的观察基于该旧版，函数签名与调用关系在 diff 中未变化。测试仓库分支 `sunchengzhu/v0.10.0-rc2-regression`（自 `v0.10.0` 拉出）；本文为复审修订（按 PR #122 的 F6 意见清理过时描述与失效路径）。

## 导读

- **这个改动要做什么**：开通道的 funding 交易广播时，如果交易池里已有另一笔交易花掉了它要用的 cell（比如同账户的另一笔 funding、或 Watchtower 的结算交易），节点应当立刻判定这次开通道失败并报错，而不是像旧版那样把「冲突」误当成「已在池中」，干等到 funding 超时（PR 实测 testnet 约 5.5 分钟）才失败。
- **行为变化**：改动前 → 冲突的 funding 被反复重发，通道卡在等待中、无任何报错，直到自身超时；改动后 → 下一次轮询即上报失败，通道快速进入失败状态。关账交易遇同类冲突时行为不变（继续等待，不上报失败）。
- **最怕出什么问题**：该快速失败的没有失败（通道仍干等数分钟）；不该失败的失败了（正常开通道、或冲突解除后的关账交易被误判失败）。
- **能测到什么**：从 `list_channels(only_pending=true)` 观察通道状态与失败耗时。构造方式的实测结论（2026-11-10，rc2 二进制）：rc2 的 funding 选币器已感知交易池（PR #1707，逐 cell 查询池中占用），双节点共用账户的第二笔 funding 会直接报「capacity not enough」而无法选中池内已花费的 cell，真实池内输入竞争只剩选币到广播之间的竞态窗口，外部无法稳定命中；且对端会冗余广播同一笔 funding 交易并成功确认，单节点注入会被掩盖。确定性触发方式：两个 Fiber 节点都经 CKB RPC 代理连接链，代理对 `send_transaction` 返回真实节点会产生的 -1111 错误（复用 PR-1213/PR-1444 的代理模式）。
- **测不到的**：关账交易遇池冲突的分支——需要从外部让一笔 closing 与池中交易争同一 cell，时序无法稳定构造；重复广播（-1107）分支——只在节点内部重试路径触发，外部无法按需制造。

依据：[PR #1708 描述与 diff](https://github.com/nervosnetwork/fiber/pull/1708)；CKB RPC -1111 语义来自 PR 引用的 CKB 行为，未独立核对 CKB 源码。

## 需要你决定

- `PR1708-01`：失败详情（`failure_detail`）的具体措辞未在 diff 中核实，当前预期只断言「快速失败 + 状态为 Closed/FUNDING_ABORTED」，不断言文案。是否要求核对并断言具体措辞？（待确认：措辞口径）
- `PR1708-01`：原「双节点共用账户」构造经实测不可行（见导读的实测结论），已改用代理注入 -1111；场景行措辞随之更新，预期结果不变。请确认接受该构造方式。（待确认：构造方式变更）

## 用例

公共约定：devnet；fiber1、fiber2 都经 `framework/ckb_rpc_proxy.py` 代理连接 CKB 节点（复用 `test_funding_retry.py` 的既有模式），代理按 JSON-RPC 方法注入错误码。失败耗时从 `open_channel` 返回起算，到 pending 记录变为 Closed/FUNDING_ABORTED 为止；「快速」的阈值取 60 秒（旧实现要等 funding 自身超时，PR 实测约 5.5 分钟；rc2 实测 5.0 秒）。耗时：两条用例合计实测约 50 秒。

<!-- TEST-CASES-BEGIN -->
| 用例 | 场景 | 预期结果 | 防止的问题 | 优先级 |
| --- | --- | --- | --- | --- |
| `PR1708-01` | - [x] fiber1、fiber2 都经 CKB RPC 代理连接链；代理对 `send_transaction` 返回真实节点会产生的 -1111（PoolRejectedRBF，池中另一笔交易已花费本交易输入）；fiber1 向 fiber2 开通道，funding 广播被拒 | fiber1 的 pending 通道在 60 秒内变为 Closed/FUNDING_ABORTED，而不是干等到 funding 超时 | 池冲突被当成「已在池中」，开通道干等约 5.5 分钟无任何报错 | P0 |
| `PR1708-02` | - [x] `PR1708-01` 失败后，等待 fiber2 侧也记录通道中止（选币预留释放），代理恢复转发 `send_transaction`；fiber1 重新向 fiber2 开通道 | 新通道正常建立并 Ready；fiber1 无残留的失败 pending 记录影响新通道 | 冲突失败后 in-flight 记录或状态残留，导致同节点后续开通道失败 | P1 |
<!-- TEST-CASES-END -->

## 测试树

<!-- TEST-TREE-BEGIN -->
- funding 交易广播遭交易池冲突（-1111）
  - 池中另一笔交易已花费 funding 的输入
    - PR1708-01 [primary] -> SPEC-01：下一次轮询即上报失败，pending 通道快速 Closed/FUNDING_ABORTED
    - PR1708-02 [primary] -> SPEC-02：失败后同节点重新开通道成功，无残留
  - [not_applicable] 同一 funding 重复广播（-1107）仍视为「已在池中」 — 只在节点内部重试路径触发，外部无法按需制造
- 关账交易广播遭交易池冲突
  - [pending] closing 遇 -1111 继续等待、不上报失败 — 需让 closing 与池中交易争同一 cell，外部时序无法稳定构造；下一步评估能否借 watchtower 结算在池的窗口（与 PR #1707 场景重叠）附带观察
<!-- TEST-TREE-END -->

## Spec

### SPEC-01：funding 遭池冲突立即失败
- Condition: funding 交易广播时，交易池中另一笔交易已花费其某个输入（CKB 返回 -1111）。
- Expected: 节点将该错误视为永久性失败，下一次轮询即上报，结束本次开通道；不再反复重发到自身超时。
- Observable: `list_channels(only_pending=true)` 中该 pending 通道在有界短时间内（远小于 funding 超时，取 60 秒）变为 Closed/FUNDING_ABORTED。代理注入构造下不存在真实的竞争交易，「竞争交易确认后其通道正常」不属于本规则；正常开通道路径由全仓库既有 open_channel 用例覆盖。
- Basis: [PR #1708 描述与 diff](https://github.com/nervosnetwork/fiber/pull/1708)（`CkbChainMessage::SendTx` 对 -1111 返回错误；`is_permanent_send_tx_error` 纳入 -1111；Funding 种类上报 `ReportSendTxError`）。
- Source: explicit
- Testing: required

### SPEC-02：冲突失败不残留、可重开
- Condition: 一次 funding 因池冲突（代理注入 -1111）失败结束，对侧已记录通道中止（选币预留已释放），且代理已恢复转发 `send_transaction`；代理注入构造下不存在真实的竞争交易。
- Expected: 同一节点重新发起开通道可正常完成。
- Observable: 新通道 Ready；旧失败记录不干扰新通道。
- Basis: 兼容性推断，PR 未单独声明；`abort_funding` 移除 pending 记录与 in-flight 任务（旧版源码观察，diff 未触碰该路径）。
- Source: inference
- Testing: required

## 本轮状态

- 结构检查：`check_test_design.py --review reviews/pr1708-funding-pool-conflict.md` 退出码 0（2 条用例；SPEC-02 的 inference 与 closing 待评估分支按要求列入待决定）。
- 映射检查：`scripts/check_test_map.py` 当前退出码 1——`PR1708-01/02` 均已映射，退出 1 来自基线既有孤儿映射（`PMP-01~05`、`SETTLE-01` 等），不在本切片处理；具体数字以最新运行输出为准。
- 自动化进展：`PR1708-01/02` 均已实现于 `test_cases/fiber/devnet/open_channel/test_funding_pool_conflict_fails_fast.py`（2026-11-11 由 `test_pr1708_funding_pool_conflict.py` 按功能改名，TEST-MAP ID 不变）并映射。执行证据：`PR1708-01` 在 rc2 上 funding 5.0s 内 FUNDING_ABORTED（节点日志含 `conflicts with one in the pool` 与 `Funding transaction failed`），同一用例在 rc1 二进制上反向验证超时失败（复现旧行为）；`PR1708-02` 初次运行发现 fiber1 记录失败后 fiber2 的选币预留释放存在约 200ms 滞后，已加对侧中止记录屏障，两条用例 `2 passed in 49.22s`。
- 独立 B 评审：本环境未启用子代理，尚未执行独立 B 评审；已按规范自查（分行核对预期与 diff 依据），但不计为独立评审。是否需要补一轮独立 B 评审，请与用例行一并指示。

## 验证设计

- 独立预期：-1111 的「池冲突」语义与「应立即失败」来自 PR 描述及 CKB RPC 行为；两条用例共用同一 oracle（pending 通道状态 + 失败耗时上限），预期不来自被测实现本身。
- 实际入口与观察：公开 RPC `open_channel`、`list_channels(only_pending=true)`；-1111 由 CKB RPC 代理按真实节点的错误格式注入（代理只替换 CKB 的响应内容，节点软件与协议交互均为真实）。操作后检查新的 pending 记录状态与耗时，而非仅复述调用返回值。
- 证明边界：真实构建的 devnet 节点与 CKB 链；拒绝层为代理注入的 RPC 错误而非链上状态。反例已实测：`v0.10.0-rc1`（commit 647a720）二进制在同一测试下 60 秒内无失败、用例超时失败；`v0.10.0-rc2`（commit 7aa1d5d）5.0 秒失败、用例通过。
