# PR #1673：Trampoline 内外层哈希算法一致性用例评审

评审范围：单个 trampoline 边界 A→B→C，A→B 已接收 TLC 的 `payment_hash` 与内层 trampoline onion 相同且费用、onion、过期预算均有效；本切片只比较 PR 明确给出的上游 `CkbHash` 下内层 `Sha256`（拒绝）与 `CkbHash`（兼容成功）。反向算法组合、多 trampoline、MPP、UDT 与其他畸形字段的校验顺序标为未分析，不计入本切片完成范围。

源码版本：[PR #1673](https://github.com/nervosnetwork/fiber/pull/1673)；base tip / merge-base `cfdde7e6c93a0cfd70156b1674c55d77e6c2da7c`，head `b4cadc2914a0a84d5ce9a9f5729094a31cb164e8`（2026-09-29 GitHub 快照）。测试仓库 HEAD `a69ea9a1877f2bbaa76854fb405629a961cc8d31`，工作区有既存改动；本轮只新增本评审和分析材料，不修改自动化。

原始材料：`.ai-test-agent/current/pr1673-trampoline-hash-algorithm/inputs/pr.json`、`.ai-test-agent/current/pr1673-trampoline-hash-algorithm/inputs/pr.diff` 与 `.ai-test-agent/current/pr1673-trampoline-hash-algorithm/input-index.json`。产品仓库本地检出仍在 `f864497b07cd6b10a4571bbfb264e4f16a1fc6c8` 且有既存改动，本轮通过固定 base/head Git 对象读取源码，未切换产品工作区。现有未映射漏洞 PoC 为 `test_cases/fiber/devnet/security/test_poc_trampoline_inner_hash_algorithm_mismatch.py`；它以旧实现发生下游付款和路由损失为成功条件，不能直接作为修复后通过判据。

## 变更说明

- PR 声明：内层 trampoline `Forward.hash_algorithm` 必须等于上游 received TLC 的 `hash_algorithm`；否则在启动下游付款前以 `InvalidOnionPayload` 拒绝。来源：[PR 描述](https://github.com/nervosnetwork/fiber/pull/1673)。
- 代码观察：head 在确认上游 TLC 存在且 `payment_hash` 一致后、计算过期预算和构造 `SendPaymentData` 前增加算法比较；不相等立即返回 `InvalidOnionPayload`，相等继续原路径。来源：[network.rs#L4678-L4728](https://github.com/nervosnetwork/fiber/blob/b4cadc2914a0a84d5ce9a9f5729094a31cb164e8/crates/fiber-lib/src/fiber/network.rs#L4678-L4728)。
- 可观察的前后差异：base 只比较 `payment_hash`，随后把内层算法写入 `TrampolineContext` 并启动 B→C 付款；当 A→B 用 `CkbHash`、内层指定 `Sha256` 时，C 可按 `Sha256` 兑现而 B 无法用该原像领取上游 TLC。head 应在 B 边界直接失败，C 不收款、B 不承担下游支出；算法匹配的既有付款继续成功。
- PR 自带 Rust 测试：直接构造 B 的 committed received TLC 与内层 onion，覆盖 `CkbHash/Sha256` 返回 `InvalidOnionPayload` 以及 `CkbHash/CkbHash` 返回 `Ok`；它没有验证真实消息回传后 A 的终态、B→C 无副作用、余额/发票，匹配分支也只证明已接受启动。来源：[trampoline.rs#L2459-L2623](https://github.com/nervosnetwork/fiber/blob/b4cadc2914a0a84d5ce9a9f5729094a31cb164e8/crates/fiber-lib/src/fiber/tests/trampoline.rs#L2459-L2623)。
- 直接影响：trampoline 节点的下游付款创建、上游失败传播、付款/发票终态、原像可见性与两条通道余额。间接影响：错误码会参与付款重试/节点惩罚判断；本切片只核对当前单 trampoline 指定路径，不扩展路由评分行为。
- 未读输入：PR 无外部协议或产品规范；CI 在采集时仍有大量 pending。反向 `Sha256/CkbHash`、匹配 `Sha256/Sha256`、多 trampoline、MPP 和 UDT 尚未逐条读完调用链或设计自动化。

## Spec

### SPEC-01：算法不匹配在 trampoline 边界拒绝启动下游付款
Condition: A→B 的 received TLC 已承诺并使用 `CkbHash`；内层 trampoline `Forward` 使用相同 `payment_hash`、有效费用/onion/过期预算，但指定 `Sha256`。
Expected: B 在构造 `SendPaymentData` 和调用 `start_payment_actor` 前以 `InvalidOnionPayload` 拒绝。
Observable: B 的边界调用返回 `InvalidOnionPayload`，且 B 不创建该 `payment_hash` 的下游 payment session。
Basis: [PR 描述](https://github.com/nervosnetwork/fiber/pull/1673)、[新增 guard 位于下游 payment builder/start 之前](https://github.com/nervosnetwork/fiber/blob/b4cadc2914a0a84d5ce9a9f5729094a31cb164e8/crates/fiber-lib/src/fiber/network.rs#L4678-L4728)。
Source: explicit
Testing: required

### SPEC-02：边界拒绝沿上游失败收尾且没有下游资金副作用
Condition: A→B→C 的隔离通道中满足 SPEC-01；C 另持有同一 `payment_hash`、正确原像和 `Sha256` 算法的 `Open` 发票，恶意 A 的攻击 hook 只把内层 `Forward` 改为 `Sha256`，外层付款会话仍为 `CkbHash`。
Expected: SPEC-01 的失败沿原 A→B TLC 返回；A 不得到原像，B 不向 C 支付。
Observable: A 的该笔付款进入 `Failed`、`failed_error` 为 `InvalidOnionPayload` 且 `payment_preimage` 为空；固定同一 hash/ID 的 A→B TLC 终态为 `RemoveAckConfirmed`。B 查询该 hash 得到 payment session 不存在，B→C 无同 hash TLC；C 发票保持 `Open`。失败前后 A/B 上游与 B/C 下游四侧本地余额不变，两条通道仍为 Ready。
Basis: [边界返回值与位置](https://github.com/nervosnetwork/fiber/blob/b4cadc2914a0a84d5ce9a9f5729094a31cb164e8/crates/fiber-lib/src/fiber/network.rs#L4678-L4728)、[中间跳错误传播与重试记录](https://github.com/nervosnetwork/fiber/blob/b4cadc2914a0a84d5ce9a9f5729094a31cb164e8/crates/fiber-lib/src/fiber/history.rs#L338-L356)、本仓库旧实现 PoC `test_cases/fiber/devnet/security/test_poc_trampoline_inner_hash_algorithm_mismatch.py` 所记录的相反副作用；完整端到端终态为修复效果推断。
Source: inference
Testing: required

### SPEC-03：算法匹配的 trampoline 付款保持成功
Condition: 与 SPEC-01 相同的 A→B→C 指定 trampoline 路由和有效输入，但上游 received TLC 与内层 `Forward` 均使用 `CkbHash`，C 的发票及正确原像也按 `CkbHash` 生成。
Expected: 新 guard 不误拒绝；B 启动 B→C 付款，正确原像沿 C→B→A 返回，整笔付款按正常路径成功。
Observable: C hold 发票进入 `Received` 后，记录同 hash 的 A→B incoming TLC 与 B→C outgoing TLC 的 ID、金额及四侧本地余额，再用正确原像结算。A 付款最终 `Success` 且原像匹配、C 发票 `Paid`，两个已记录 TLC 经 Fulfill 收尾；A 扣款与 B 上游入账等于 incoming 金额，B 下游扣款与 C 入账等于 outgoing 金额，路由费等于 incoming 减 outgoing，两条通道仍为 Ready。
Basis: [PR 描述中的 matching 对照](https://github.com/nervosnetwork/fiber/pull/1673)、[新增 Rust 对照只证明边界返回 Ok](https://github.com/nervosnetwork/fiber/blob/b4cadc2914a0a84d5ce9a9f5729094a31cb164e8/crates/fiber-lib/src/fiber/tests/trampoline.rs#L2593-L2623)；完整端到端终态与资金断言属于兼容性推断。
Source: inference
Testing: required

## 测试树

<!-- TEST-TREE-BEGIN -->
- 单 trampoline 的内外层哈希算法一致性
  - 上游 A→B TLC 为 `CkbHash`，且 payment_hash、费用、onion、过期预算均有效
    - 内层 B→C `Forward` 为 `Sha256`
      - PR1673-01 [primary] -> SPEC-01：B 返回 InvalidOnionPayload 且不创建下游 payment session
      - PR1673-01 [ref] -> SPEC-02：同一失败沿上游 TLC 收尾，C 发票和两跳余额无副作用
    - 内层 B→C `Forward` 为 `CkbHash`
      - PR1673-02 [primary] -> SPEC-03：B 不误拒绝，A→B→C 端到端兑现并按记录的两跳 TLC 金额转移余额
  - [unanalysed] 上游 `Sha256`、内层 `CkbHash` 或 `Sha256` — PR 未给出这组集成期望；下一切片从 `SendPaymentData.hash_algorithm`、攻击夹具的 `ckb_hash` override 和现有 sha256 trampoline xfail 进入
  - [unanalysed] 多 trampoline 与 MPP — 尚未检查每个 trampoline 边界及多 part 是否都能形成独立、可观察的拒绝；下一切片从 `TrampolineContext.previous_tlcs`、`max_parts` 和 trampoline MPP 测试进入
  - [unanalysed] UDT 付款 — guard 本身不按资产分支，但端到端 payment/TLC/余额 oracle 仍走 UDT 路径；下一切片从 `udt_type_script` 传递和现有 trampoline UDT 用例进入
  - [unanalysed] 同时存在 payment_hash、费用、onion 或过期错误时的错误优先级 — 属既有校验顺序；下一切片从 `forward_trampoline_packet` 相邻返回分支与对应 Rust 用例进入
<!-- TEST-TREE-END -->

## 待评审用例

<!-- TEST-CASES-BEGIN -->
| 用例 | 场景 | 预期结果 | 防止的问题 | 优先级 |
| --- | --- | --- | --- | --- |
| `PR1673-01` | - [x] A→B→C 隔离通道中，C 持有同一 `payment_hash`、正确原像和 `Sha256` 算法的 `Open` 发票；A→B 已承诺 received TLC 使用 `CkbHash`，恶意 A 的 attack hook 只令内层 `Forward` 使用 `Sha256`，其余费用、onion 与过期预算均有效 | B 在创建 B→C payment session 前返回 `InvalidOnionPayload`（由 B 的 guard 日志行直接观测；RPC 不暴露该错误码与 TLC 移除原因）；A 付款 `Failed` 且无原像，A 端 `failed_error` 记为发送端在上游拒绝后的重试建路错误（不含该错误码）；固定同一 hash 的上游 TLC 为 `RemoveAckConfirmed`；B 无该 hash 的 payment session，B→C 无同 hash TLC，C 发票仍 `Open`，四侧余额不变且两条通道 Ready | C 按内层算法兑现后 B 已对下游付款却无法用该原像领取上游 TLC，造成路由资金损失 | P0 |
| `PR1673-02` | - [x] A→B→C 隔离通道中，上游 received TLC、内层 `Forward` 与 C 的 CkbHash hold 发票都使用 `CkbHash`；C `Received` 后记录同 hash 的两跳 TLC ID、incoming/outgoing 金额和四侧余额，再用正确原像结算 | 新 guard 不误拒绝；A 付款 `Success` 且原像正确、C 发票 `Paid`，两个已记录 TLC 经 Fulfill 收尾；A 扣款与 B 上游入账等于 incoming，B 下游扣款与 C 入账等于 outgoing，费用为 incoming-outgoing，两条通道 Ready | 修复把合法 trampoline 付款一并拦截，或只返回已接受却未真正完成下游付款和资金转移 | P0 |
<!-- TEST-CASES-END -->

## 本轮需要确认

- 独立设计评审 B（只读、未运行节点，宿主级隔离未验证）首轮结论为 `revise`：F1 指出执行二进制未绑定 head，F2 指出 `python` 实为 Python 2，二者作为自动化前置门禁采纳；F3–F5 已补发票/attack hook 前提、拆分显式边界规则与推断的端到端规则，并固定 payment session、TLC ID/终态和余额方程；F6 已把 UDT 改为未分析；F7 已修正输入索引路径。唯一一次有界复审结论为 `pass`，无残余 critical/high/medium finding；B 对 `PR1673-01`、`PR1673-02` 的设计层静态判断均为 `covered`，该判断不表示已有映射或执行通过。
- 自动化前置门禁：B 的 stock victim 必须由 head `b4cadc2914a0a84d5ce9a9f5729094a31cb164e8` 构建并记录二进制哈希；同时记录 `framework/test_fiber.py`、attack FNN 及其 hook 源码/构建来源和哈希。测试命令使用已能收集用例的 `./venv/bin/python -m pytest`，不使用当前指向 Python 2.7 的 `python`。
- 请确认本批 `PR1673-01`–`02` 的场景、预期和优先级。当前仅到 G1；确认前不修改 PoC、现有 trampoline 测试或 `TEST-MAP`。

## 自动化与本轮决定

- G1 已确认本批 `PR1673-01`–`02`；自动化落在 `test_cases/fiber/devnet/security/test_poc_trampoline_inner_hash_algorithm_mismatch.py`（原漏洞 PoC 改写为修复后回归），场景复选框随 `TEST-MAP`（`:260`、`:370`）置为 `- [x]`。
- 执行命令与原始输出、映射检查、独立 B 覆盖评审结论、A 的逐条回应统一存放于 `reports/pr1673-trampoline-hash-algorithm/automation/`（`verification.json`、`b-coverage-review.md`、`a-response.md`），本文件不重复。
- 人工决定（2026-09-29）：`PR1673-01` 的预期结果已按实测改写——`InvalidOnionPayload` 由 B 的 guard 日志行直接观测，A 端 `failed_error` 记为发送端重试建路错误，上游 TLC 按同一 `payment_hash` 追踪；场景、优先级与 ID 不变，已记入 `reviews/review-feedback.md`。仍待定：B 的 F5（`CURRENT_DEV` 全局重指向）。


