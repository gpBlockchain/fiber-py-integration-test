# PR #1709：hold-set 结算重放时队列中的 fulfill 不再阻塞用例评审

评审范围：MPP hold 发票结算中，当某个 TLC 的 fulfill 已排队（对端断连/等待 ack）时，收款方重放 `SettleReceivedHoldTlcSet` 必须仍能重建结算上下文并完成任务，不能丢上下文导致 hold TLC 卡死。单一部分（非 MPP）的 hold 结算重试已由 `PR-1289` 系列用例覆盖；排队 fail 压制本地原像的分支由 `PR1672-03` 覆盖，均不重复。

源码版本：[PR #1709](https://github.com/nervosnetwork/fiber/pull/1709)；diff 取自 2026-11-10 GitHub API 快照（`gh pr diff 1709`）。测试仓库分支 `sunchengzhu/v0.10.0-rc2-regression`；本文为复审修订（按 PR #122 的 F1 意见收窄覆盖声明）。

## 导读

- **这个改动要做什么**：修复一个 MPP hold 付款的结算卡死问题。场景是：买家分多路（多条通道）付一张 hold 发票，卖家结算时买家恰好不在线，fulfill 消息排队等对端确认；这时如果卖家节点重启，恢复后要「重放」这次结算。按 PR 描述，旧版重放时可能把正在排队的 TLC 误认为「已处理」而跳过，导致结算上下文丢失、TLC 卡住（此为 PR 报告的现象与机理推断，本批黑盒测试未实证旧版必定如此）。修复后排队的 fulfill 被视为「同一次结算的重放」，正常完成。
- **行为变化**：改动前 → PR 报告上述场景下卖家重启后发票和部分 TLC 卡住、付款方资金被锁（未由本批实证）；改动后 → 重放正常完成，发票变 Paid，付款方收到 Success，双方无残留 TLC。
- **最怕出什么问题**：修过头 —— 把「排队的失败」也放行了：下游已经明确失败的转发，不应被本地原像改写为成功（这一半行为由既有用例 PR1672-03 守护）。
- **能测到什么**：用公开 RPC 可完整构造崩溃恢复场景：MPP hold 发票 + 双通道付款 + 对端停机/断连 + 收款方 `settle_invoice` + 收款方 SIGKILL 崩溃 + 重启 + 恢复连接。观察付款状态、发票状态、双方 pending TLC 数量。
- **测不到的**：内部谓词 `can_auto_fulfill_received_tlc` 的真值表本身（属单元测试，PR 已自带）；`settle_invoice` 后 SIGKILL 是否每次都正好落在「durable 队列已写、hold 记录未删」的内部交接窗口（见「已决定」）；内部交接的幂等与清理由上游专项测试验证。本批黑盒用例的契约只到「崩溃重启后结算恢复、资金不卡死」。

依据：[PR #1709 描述与 diff](https://github.com/nervosnetwork/fiber/pull/1709)；内部触发路径（`SettleReceivedHoldTlcSet` 重放经 `can_auto_fulfill_received_tlc` 重建上下文）来自 PR 描述，未逐行核对固定 head 源码。

## 已决定

- `PR1709-01/02` 时序屏障（2026-11-10 确认）：结算前**必须**先等 fiber2 发票进入 `Received`（两部分 TLC 均到达的官方信号），再停/断 fiber1、再 settle、再崩溃重启 fiber2。不用固定 sleep 赌时序。
- 收款方用 SIGKILL 崩溃而非优雅停止（2026-11-10 实测修正）：实测观察到，优雅停止的那次运行中重启后未观察到 hold-set 重放（日志中只有重试队列恢复）——这不等同于没有走任何崩溃恢复路径；SIGKILL 紧接 `settle_invoice` 返回后执行的那次运行中，日志显示重启后发生了重放并为未完成交接的 TLC 重建 fulfill。**以上为单次运行的日志证据**：黑盒操作不保证每次命中内部交接窗口，本批用例的契约只到终态资金恢复；命中内部重放路径的专项验证由上游测试负责。
- 覆盖定位（2026-11-11 复审修订）：两条用例定位为 **MPP hold 崩溃恢复回归**。终态 `Success / Paid / pending TLC 归零` 证明崩溃后资金可恢复，但不能证明命中了 #1709 修复路径（存在 durable 队列直接完成交接的另一条路径），也不能据此宣称能拒绝有效的旧版回归。
- 反向验证基线（2026-11-10 查实）：bug 由 #1672 引入（2026-10-01 合入），而 rc1 二进制是 647a720（2026-09-25，`compare` 确认不含 #1672），**rc1 不是有效反向基线**；本地无「含 #1672 但不含 #1709」的 buggy 二进制（构建需 Rust 工具链与上游源码，本机不具备）。因此本批用例不作为 #1709 修复的专项反向验证，只作崩溃恢复回归。

## 用例

公共约定：devnet；fiber1（付款方）与 fiber2（收款方）之间开**两条**通道；fiber2 创建 `allow_mpp` 的 hold 发票（自带 payment_hash），金额超过单通道余额以强制分两部分；fiber1 付款后等待 fiber2 发票 `Received`（两部分 TLC 均已到达）。「收款方崩溃」指 `settle_invoice` 返回后立即 SIGKILL 再启动（是否触发内部结算重放取决于崩溃时机，不作为用例前提）。终态判定：fiber1 付款 `Success`、fiber2 发票 `Paid`、双方 pending TLC 归零（`wait_fibers_pending_tlc_eq0`）。耗时：每条实测约 40~50 秒。等待上限：发票 `Received` 120 秒、付款 `Success` 180 秒、pending TLC 归零 60 秒。

<!-- TEST-CASES-BEGIN -->
| 用例 | 场景 | 预期结果 | 防止的问题 | 优先级 |
| --- | --- | --- | --- | --- |
| `PR1709-01` | - [x] MPP hold 发票的两部分 TLC 均已到达 fiber2（发票 Received）；停掉 fiber1 进程后 fiber2 执行 `settle_invoice`，立即 SIGKILL fiber2 模拟崩溃，随后重启 fiber2、重启 fiber1 并重连 | fiber1 付款 `Success`；fiber2 发票 `Paid`；双方 pending TLC 归零，无卡死 | 收款方在付款方离线时结算后崩溃，重启后结算无法恢复，hold TLC 卡住、资金被锁 | P0 |
| `PR1709-02` | - [x] 同 PR1709-01，但对端离线方式改为 RPC `disconnect_peer`（进程不退出），SIGKILL 并重启 fiber2 后重连恢复 | 同 PR1709-01：付款 `Success`、发票 `Paid`、双方 pending TLC 归零 | 断连路径下收款方崩溃后结算无法恢复 | P1 |
<!-- TEST-CASES-END -->

## 测试树

<!-- TEST-TREE-BEGIN -->
- MPP hold 发票结算中收款方崩溃（对端离线，fulfill 可能正在排队）
  - 对端进程停止后 settle，收款方 SIGKILL 崩溃后重启
    - PR1709-01 [primary] -> SPEC-01：崩溃恢复回归——付款成功、发票 Paid、无 TLC 残留
  - 对端 RPC 断连后 settle，收款方 SIGKILL 崩溃后重启
    - PR1709-02 [primary] -> SPEC-01：同上，断连方式为正交变体（review-feedback：断连须覆盖重启与 RPC 断开两种）
  - 对端在线时 settle（无排队） [not_applicable] — 正常路径已由 settle_invoice/test_mpp.py 等既有用例覆盖
- 排队的 fail 压制本地原像（本 PR 同一谓词的另一半） [not_applicable] — 已由 reviews/pr1672-forwarded-local-invoice.md 的用例 3 守护，不重复实现
<!-- TEST-TREE-END -->

## Spec

### SPEC-01：收款方崩溃重启后结算恢复
- Condition: MPP hold 发票的全部 TLC 部分已到达收款方（发票 Received）；收款方在付款方离线（进程停止或 RPC 断连）时执行 `settle_invoice`，随后被 SIGKILL 并重启，双方恢复连接。
- Expected: 收款方重启后结算继续推进并最终完成；对端恢复后 remove 被确认，资金正常转移。
- Observable: 付款方 `get_payment` 为 `Success`；收款方 `get_invoice` 为 `Paid`；双方 pending TLC 归零；通道保持 Ready。
- Basis: [PR #1709 描述与 diff](https://github.com/nervosnetwork/fiber/pull/1709)。内部重放路径（`SettleReceivedHoldTlcSet` 重放与排队 fulfill 的幂等判定）由上游专项测试验证，本规则只约束外部可见的资金恢复，不保证每次运行命中内部窗口。
- Source: explicit
- Testing: required

## 验证设计

- 独立预期：「崩溃重启后结算应恢复、资金不卡死」来自 PR 描述与用户可报告现象（发票 Paid、付款 Success），不来自被测实现；两条用例共用同一 oracle（付款状态 + 发票状态 + pending TLC 归零）。
- 实际入口与观察：公开 RPC（`new_invoice`、`send_payment`、`settle_invoice`、`disconnect_peer`、`get_payment`、`get_invoice`）与运维操作（进程停止/SIGKILL/启动）；前置屏障为发票 `Received` 状态，终态用框架既有等待方法，不用固定 sleep 判断成功。
- 证明边界：真实构建的 devnet 节点；崩溃由 SIGKILL 模拟，恢复由真实重启完成。本用例终态无法判断是否命中内部交接窗口，也不能据此判断旧版是否会失败（即使命中窗口，durable 队列仍可能直接完成交接）；本用例只证明崩溃后资金恢复，不以区分新旧实现为契约。
