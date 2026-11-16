# PR #1672：转发 TLC 与本地同哈希发票隔离用例评审

评审范围：链下已建立转发关系时，路由节点持有同 `payment_hash` 的本地 `Open` 发票及原像，转发 TLC 的等待、成功、失败不会被当作该发票的本地收款；单笔直达本地发票作为兼容性对照。转发任务刚创建但尚无下游 TLC ID 的瞬态窗口、下游即时兑现、直达与转发并发、链上重整、MPP 另列未分析或待补夹具，不计入本切片的完成范围。

源码版本：[PR #1672](https://github.com/nervosnetwork/fiber/pull/1672)；base tip / merge-base `355bd63582e5034044de3c50a5fd9ad38968aec6`，head `5b14dee72efcacf8834731e241897c98305e04c8`（2026-09-29 GitHub API 快照）。测试仓库 HEAD `a69ea9a1877f2bbaa76854fb405629a961cc8d31`，工作区有既存改动；初次评审阶段未修改测试，确认后新增本切片测试。

原始材料：`reports/pr1672-local-settlement/inputs/pr.json`、`files.json`、`compare.json`、`product.diff` 及其中以提交号开头的源码快照。现有相关映射 `PAY-31` 位于 `test_cases/fiber/devnet/send_payment/test_same_invoice_preimage_scope.py`；两个未映射的旧漏洞 PoC 位于 `test_cases/fiber/devnet/security/test_poc_router_invoice_fulfills_forward.py` 和 `test_poc_router_invoice_ac_collusion.py`，它们以漏洞出现为成功条件，不能直接作为修复回归的通过判据。

## 变更说明

- PR 声明：仅本地付款可结算本地发票及更新付款状态，并补转发、重整回归测试。来源：[PR 描述](https://github.com/nervosnetwork/fiber/pull/1672)。
- 代码观察：`maintain_ready_channel_tlcs` 在读取本地原像和发票前先应用 `can_auto_fulfill_received_tlc`；`SettleTlcSet` 构造结算上下文时再次过滤；Fulfill 移除时只对非转发的 received TLC 更新同哈希本地发票为 `Paid`。来源：[channel.rs](https://github.com/nervosnetwork/fiber/blob/5b14dee72efcacf8834731e241897c98305e04c8/crates/fiber-lib/src/fiber/channel.rs#L1925-L1942)、[维护路径](https://github.com/nervosnetwork/fiber/blob/5b14dee72efcacf8834731e241897c98305e04c8/crates/fiber-lib/src/fiber/channel.rs#L2920-L2985)、[结算上下文](https://github.com/nervosnetwork/fiber/blob/5b14dee72efcacf8834731e241897c98305e04c8/crates/fiber-lib/src/fiber/settle_tlc_set_command.rs#L435-L446)。
- 可观察的前后差异：旧实现可能由路由节点保存的同哈希发票原像提前兑现入站转发 TLC，并将该发票误标 `Paid`；目标实现要求等该转发自己的下游结果，并保持路由节点发票 `Open`。PR 自带的三节点 Rust 用例覆盖“下游 hold → 下游结算”路径；旧 PoC 仅显示旧风险，尚非修复后测试。来源：[新增 Rust 测试](https://github.com/nervosnetwork/fiber/blob/5b14dee72efcacf8834731e241897c98305e04c8/crates/fiber-lib/src/fiber/tests/invoice_settlement.rs#L168-L260)。
- 直接影响：路由付款结果、路由节点 `get_invoice` 状态、上游原像可见性。间接影响：下游结算失败传播、同哈希本地直达付款、链上 Fulfill 后发票状态重整。后者未纳入本切片。
- 未读输入：PR 没有外部产品规范；待人工确认失败路径和直达对照的具体验收口径。初次评审阶段尚未核对节点二进制、合约版本及运行环境；确认后的自动化阶段另行核对并运行聚焦测试。

## Spec

### SPEC-01：下游悬而未决时，转发不触发本地发票
Condition: A→B→C 的已承诺转发 TLC 哈希等于 B 自己 `Open` 发票的哈希；B 持有该发票原像；C 的 hold 发票停在 `Received`，下游尚未给 B Fulfill/Fail。
Expected: B 等待该转发自己的下游结果，不从本地发票原像兑现 A→B 入站 TLC，也不把 B 的发票标为 `Paid`。
Observable: B 发票仍 `Open`、A 付款仍在途且未返回原像；同一转发在 B 的入站和出站 TLC 均仍在途。执行至少一轮 TLC 维护的可验证完成屏障待自动化设计落实，不能只靠固定 sleep。
Basis: [PR 描述](https://github.com/nervosnetwork/fiber/pull/1672)、[新增 Rust 测试](https://github.com/nervosnetwork/fiber/blob/5b14dee72efcacf8834731e241897c98305e04c8/crates/fiber-lib/src/fiber/tests/invoice_settlement.rs#L168-L247)。
Source: inference
Testing: required

### SPEC-02：下游兑现后，仅目标收款方发票变为 Paid
Condition: SPEC-01 的下游 hold TLC 随后由 C 用正确原像结算，B 收到自己转出 TLC 的 Fulfill。
Expected: Fulfill 沿原转发关系返回 A，A 的付款成功，C 发票 `Paid`；B 的同哈希本地发票保持 `Open`。
Observable: A `get_payment` 为 `Success` 且原像匹配；C、B 的 `get_invoice` 分别为 `Paid`、`Open`；对应入站和出站 TLC 经 Fulfill 后退出在途；在无并发转账的隔离通道上，以这笔 TLC 锁定前和两跳最终移除后为余额采样点，B 上游入账等于入站 TLC 金额、B 下游出账与 C 入账等于出站 TLC 金额，路由费为入站与出站 TLC 金额之差；两跳通道仍 Ready。
Basis: [PR 描述](https://github.com/nervosnetwork/fiber/pull/1672)、[新增 Rust 测试](https://github.com/nervosnetwork/fiber/blob/5b14dee72efcacf8834731e241897c98305e04c8/crates/fiber-lib/src/fiber/tests/invoice_settlement.rs#L248-L260)、[Fulfill 状态更新条件](https://github.com/nervosnetwork/fiber/blob/5b14dee72efcacf8834731e241897c98305e04c8/crates/fiber-lib/src/fiber/channel.rs#L1925-L1942)。
Source: inference
Testing: required

### SPEC-03：下游失败不得由本地同哈希原像改写为成功
Condition: A→B→C 为指定的单一路由，B 持有同哈希 `Open` 发票及原像；C 的 hold 发票为 `Received` 后取消，使对应下游 TLC 失败。
Expected: B 将该下游失败对应地向 A 传播，而非用本地原像改写为成功；A 的该笔付款失败，B 发票仍 `Open`，通道保持 Ready。
Observable: 记录同一尝试在两跳的 TLC ID，并限制自动重试；C 发票为 `Cancelled`，下游失败后两跳对应 TLC 均按 Fail 移除；A `get_payment` 为 `Failed` 且不暴露原像；B `get_invoice` 为 `Open`；两跳通道为 Ready。取消后实际 RemoveTlc Fail 的来源与可见性仍须按固定 head 实现核实。
Basis: [转发守卫的源码注释](https://github.com/nervosnetwork/fiber/blob/5b14dee72efcacf8834731e241897c98305e04c8/crates/fiber-lib/src/fiber/channel.rs#L5884-L5913)、[下游失败回传](https://github.com/nervosnetwork/fiber/blob/5b14dee72efcacf8834731e241897c98305e04c8/crates/fiber-lib/src/fiber/channel.rs#L1943-L1962)、本项目 `test_cases/fiber/devnet/cancel_invoice/test_cancel_invoice.py::test_cancel_invoice_that_statue_is_receive`。既有 `PAY-31` 是另一个已结算发票的重放场景，不能代替本用例。
Source: inference
Testing: required

### SPEC-04：真正付给路由节点的本地发票仍可结算
Condition: B 持有 `Open` 发票与原像，A 将 B 作为末跳按发票付款，没有下游转发任务。
Expected: B 正常兑现入站 TLC，A 付款成功，B 发票成为 `Paid`。
Observable: A `get_payment` 为 `Success`，B `get_invoice` 为 `Paid`，通道仍 Ready。
Basis: [新守卫允许非转发 received TLC](https://github.com/nervosnetwork/fiber/blob/5b14dee72efcacf8834731e241897c98305e04c8/crates/fiber-lib/src/fiber/channel.rs#L5898-L5912)、[Fulfill 更新条件](https://github.com/nervosnetwork/fiber/blob/5b14dee72efcacf8834731e241897c98305e04c8/crates/fiber-lib/src/fiber/channel.rs#L1925-L1942)；这是兼容性推断，PR 新增测试未单独证明。
Source: inference
Testing: required

## 测试树

<!-- TEST-TREE-BEGIN -->
- 同哈希本地发票与链下转发隔离
  - B 是中间跳，持有本地 `Open` 发票和原像，入站 TLC 已承诺
    - 下游 hold，结果尚未返回
      - PR1672-01 [primary] -> SPEC-01：完成维护后 B 发票仍 Open、A 未成功且 B 两侧 TLC 待结算
    - 下游用正确原像兑现
      - PR1672-02 [primary] -> SPEC-02：对应两跳兑现且资金按路由转移，A 成功、C 发票 Paid、B 发票仍 Open
    - 下游明确失败
      - PR1672-03 [primary] -> SPEC-03：C 取消持单后对应 Fail 沿两跳返回，A 失败且无原像、B 发票仍 Open
  - B 是末跳，没有转发任务
    - PR1672-04 [primary] -> SPEC-04：直达 B 的发票照常 Paid，A 付款成功
  - [pending] 已收到入站但仍在 `waiting_forward_tlc_tasks`、尚未记录下游 TLC ID — 需要可控阻塞下游 AddTlc 响应的夹具；下一步检查 `basic_p2p`/`p2p-tap` 是否可确定性停在此窗口
  - [pending] TLC 维护执行完成的测试屏障 — 当前仅能观察两侧 Committed，固定 sleep 不证明维护事件已执行；自动化前检查事件日志或可控测试接口能否给出有界完成信号
  - [unanalysed] C 持有原像并即时兑现转发 TLC — 与 hold 后结算共享 Fulfill 传播路径，但时序不同；下一切片从 `handle_remove_tlc` 与即时发票测试进入
  - [unanalysed] B 的直达同哈希本地收款与另一笔转发同时在途 — 单笔直达对照不能证明全局发票状态变化时仍逐 TLC 隔离；下一切片从 `SettleTlcSetCommand` 与同哈希并发测试进入
  - [unanalysed] 同哈希 MPP 与本地发票并发收款 — 尚未逐条检查 MPP 调用及现有映射；下一切片从 `SettleTlcSetCommand::new_hold_tlc_set` 与 MPP 测试进入
  - [unanalysed] 链上 Fulfill / 节点重整时的本地发票结算 — 属本 PR `collect_onchain_fulfilled_received_tlcs` 的另一行为切片；从该函数、链上结算证据和已有关联测试进入
<!-- TEST-TREE-END -->

## 待评审用例

<!-- TEST-CASES-BEGIN -->
| 用例 | 场景 | 预期结果 | 防止的问题 | 优先级 |
| --- | --- | --- | --- | --- |
| `PR1672-01` | - [ ] A→B→C 的指定路由已使 B 入站和 B→C 出站 TLC 均承诺、C 的同哈希 hold 发票为 `Received`，B 有同哈希 `Open` 发票和原像；在下游仍持单时完成 B 一轮 TLC 维护 | B 发票保持 `Open`；A 付款保持在途且未得到原像；B 两侧同一转发的 TLC 仍待下游结果 | 本地发票原像越过下游结果提前兑现转发并泄露原像 | P0 |
| `PR1672-02` | - [x] A→B→C 的指定路由中 B 有同哈希 `Open` 发票和原像，C 持单且 B 两侧对应 TLC 均已承诺；在隔离通道记录该笔两跳 TLC 金额和结算前余额，再由 C 用正确原像结算自己的 hold 发票 | A 付款 `Success` 且原像匹配，C 发票 `Paid`，B 本地发票仍 `Open`；对应两跳 TLC 经 Fulfill 退出在途，B 上游入账等于入站 TLC 金额、B 下游出账与 C 入账等于出站 TLC 金额，两跳通道 Ready | 转发成功误把中间跳发票标 `Paid`，或状态正确但转发资金错误 | P0 |
| `PR1672-03` | - [x] A→B→C 的指定单一路由中 B 有同哈希 `Open` 发票和原像，C 同哈希 hold 发票已 `Received` 且 B 两侧 TLC 已承诺；限制付款自动重试并记录两跳 TLC ID，C 取消自己的发票 | C 发票 `Cancelled`，该尝试的 Fail 沿对应两跳 TLC 返回、A 付款 `Failed` 且无原像，B 发票仍 `Open`，两跳通道 Ready | 下游失败被 B 的本地原像覆盖成成功，或只观测到另一轮重试/超时的失败 | P0 |
| `PR1672-04` | - [x] B 持有 `Open` 发票及原像，A 以 B 为末跳直接按该发票付款，无下游转发 | A 付款 `Success`；B 发票 `Paid`；通道 Ready | 转发隔离守卫误拦截真正的本地收款 | P0 |
<!-- TEST-CASES-END -->

## 本轮需要确认

- 本批 `PR1672-01`–`04` 行集已获当前用户确认；复选框仅表示代码映射。`PR1672-01` 的维护完成屏障仍待落实，故暂不映射。
- 独立设计评审 B（只读、未执行节点；原始材料与实际代码独立重读，宿主级隔离未验证）：指出维护完成缺屏障、成功路径缺 TLC/资金核对、失败结果与本次尝试关联不足，以及并发直达和即时兑现分支未说明。成功路径已补同一尝试的两跳 TLC、隔离通道余额及 TLC 金额口径；失败路径已固定 C 持单取消、对应 ID 与限制重试，但“取消必产生对应 Fail”仍待固定 head 源码/运行证据确认；“维护完成”仍缺可执行屏障，自动化前必须落实，不能把 `PR1672-01` 算作已覆盖。并发直达和即时兑现已在树中标未分析。B 指出 `Source: explicit` 对若干附加断言过强，组合规则已标 `inference`，本批用例行已获用户确认。B 的一次有界复审确认上述两个缺口尚未闭环；无第三次 B 调用。
- 本仓库没有生成项目的 `pr_workflow.py` 与 `check_test_design.py`；原始输入与哈希索引保存于 `reports/pr1672-local-settlement/`，设计链接使用本地校验。旧 PoC 的漏洞成功断言未改。


## 自动化注释复核（2026-09-29）

本节更新上述历史设计评审的证据状态，不改 Spec、用例行、优先级或复选框，也不修改测试输入、步骤、断言与映射。
独立 B 重新读取当前测试、等待 helper 及固定 head 源码；未继承主代理历史，宿主级隔离未验证。

- `PR1672-01`：静态判定 **missing**。缺口：PR1672-01 尚无映射；共用准备只确认 C Received、B 两侧 Committed 和 B 发票 Open。 尚缺 B 维护完成屏障，以及屏障后 A 在途无原像、B 两侧仍待结算的检查。
- `PR1672-02`：静态判定 **covered**。准备：复用 A→B→C 通道，每次新建同哈希的 B 带原像发票和 C hold 发票，固定两跳路由。 C Received、B 两侧 TLC Committed 后记录对应 ID/金额；余额基线取发送前，随后由 C 提交正确原像。 验证 A Success 且原像正确、C Paid、B 仍 Open，并按 hash+ID 等待两侧原 TLC 消失。 核对 B 上游入账、B 下游出账和 C 入账分别等于对应 TLC 金额，B 两侧通道仍 Ready。 Fulfill 由状态、原像、TLC 消失和余额联合证明；本例不证明维护完成屏障或并发分支。
- `PR1672-03`：静态判定 **covered**。准备：固定 A→B→C 路由，等待 C Received、B 两侧 Committed，记录本次 hash、TLC ID 和发送前余额。 显式路由在已核对实现中抑制付款失败后的自动重试；由 C 取消持单，等待 Cancelled 和 A Failed。 两侧原 TLC 必须到 RemoveAckConfirmed 且 ID 匹配，同 hash 只保留这两侧各自的原记录。 验证 A 无原像且错误为 InvoiceCancelled、B 发票仍 Open，B 两侧及 C 余额不变、B 两侧通道 Ready。 Fail 由取消错误、原 TLC 的确认移除状态和余额联合证明；失败记录可留在 pending_tlcs，不断言列表清空。
- `PR1672-04`：静态判定 **covered**。准备：B 新建带原像发票，A 以 B 为收款方直接按发票付款。 验证付款 hash 一致、A Success、B Paid、返回原像正确，A-B 通道仍 Ready。 本例是单笔本地收款对照；不证明与同哈希转发并发时的隔离。

判据说明：固定 head 的 `graph.rs:1187–1225` 在显式路由付款失败时抑制自动重试；`network.rs:4476–4510`、`settle_tlc_set_command.rs:50–59,144–145,272` 和 `channel.rs:1944–1968` 连接了取消、`InvoiceCancelled` 与按原转发关系返回 Fail。`rpc/channel.rs:351–370` 列出 `all_tlcs`，`fiber/channel.rs:7020–7093` 仅在 Fulfill 时转移余额并删除 TLC。因此，02/03 的联合行为断言支持当前静态 covered 判定；无需把读取未暴露的 `RemoveTlcReason` 作为额外通过条件。旧报告中 02/03 的 partial 是历史快照判定，本节不将它改写为当时已通过。

04 的初始 Open 与直达前提来自新建普通发票及隔离拓扑，未额外查询初始状态或实际 route。01 缺少维护完成屏障，仍保持未映射；并发、即时兑现、MPP 与链上分支仍不属于本切片。

本轮不启动节点。历史 `3 passed in 63.71s` 仅作已有记录，不视为本轮执行通过。当前复核及注释数据见 `reports/pr1672-local-settlement/comment-review/evidence.json`；检查命令、原始输出与退出码见同目录 `verification.json`。
