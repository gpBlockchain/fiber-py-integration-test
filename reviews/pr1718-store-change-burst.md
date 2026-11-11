# PR #1718：store 突发变更时订阅不再中断用例评审

评审范围：store 的变更通知通道在突发（>16 条连续变更，转发任务来不及处理）时，旧版订阅方收到 `Lagged` 后**永久断订**——RPC WebSocket 订阅方（`subscribe_store_changes`）和进程内 CCH actor 都从此收不到任何 store 变更。修复后 store 自己的端口按序直投各订阅者信箱，突发多少条都不丢、不断。

源码版本：[PR #1718](https://github.com/nervosnetwork/fiber/pull/1718)；diff 取自 2026-11-11 GitHub API 快照（`gh pr diff 1718`）。测试仓库分支 `sunchengzhu/v0.10.0-rc2-regression`；本文为复审修订（按 PR #122 的 F2/F3/F5/F6 意见及 9aff76e 行集确认）。

## 导读

- **这个改动要做什么**：修一个「订阅被突发流量打死」的问题。store 每发生一次变更（开发票、付款状态推进、通道事件等）都会通知订阅者。旧版通知走一条容量 16 的广播通道：如果一瞬间来 17 条以上变更、转发任务还没来得及送，订阅方直接被判「掉队」并永久断开——之后任何变更都收不到，且没有报错提示。修复后改成 store 按序直投每个订阅者的信箱，没有中间缓冲上限。
- **行为变化**：改动前 → 一阵密集操作后，WebSocket 订阅可能静默失效（上游进程内连续发送场景的单测佐证：Recorder actor 经旧通道 101 条变更收 0 条）；改动后 → 突发期间和之后的每条通知都按序送达。
- **最怕出什么问题**：通知丢失或乱序没有被发现——客户端以为自己「在线」却漏掉了关键状态变更（对 CCH 服务而言是丢链上事件）。
- **能测到什么**：fnn 的 RPC 端口同时服务 WebSocket。订阅 `subscribe_store_changes` 后用 16 线程并发 `new_invoice` 创建 160 张普通发票（每张产生 `PutCkbInvoiceStatus(Open)` 与 `PutPreimage` 两条变更，已经 PR head 源码与 rc2 探针实测双重确认），再加 1 张只传 hash 的标记发票（1 条 Open 变更），本批相关变更共 321 条。按事件类型 + payment_hash 核对通知是否完整、无重复，同一发票的 Open 先于其 Preimage，突发后订阅是否存活。
- **测不到的**：进程内 CCH actor 的真实订阅路径与端到端链路——上游 Recorder 测试验证通用 StoreChangePort 向 actor 投递的完整性与顺序，本批不证明真实 CCH 订阅路径或端到端链路（需 BTC/LND 环境，根因相同，不重复，本次不追加 CCH 压测）。

依据：[PR #1718 描述与 diff](https://github.com/nervosnetwork/fiber/pull/1718)（含上游单元测试断言：Recorder actor 下旧通道 101 条变更收到 0 条，新端口全收）；未逐行核对固定 head 源码；事件计数与载荷结构经 rc2 实测探针确认（2026-11-11）。

## 已决定

- 反向验证结论（2026-11-11 实测）：rc1 确含 buggy 代码（`subscribe_to_port` + 16 容量广播，见 `/tmp` 源码比对），但黑盒**无法稳定复现** Lagged 杀订阅——该竞争要求 17 条变更在转发任务两次调度之间完成，而公开 RPC 驱动的变更间隔受 HTTP/落盘延迟限制（毫秒级），转发只需微秒。rc1 实测：30 顺序、8 线程 80、16 线程 320 张发票突发后订阅均存活。因此两条用例定位为**一般订阅回归**（订阅在高负载下持续有效、订阅者互不影响）：公开黑盒操作无法稳定触发旧版 Lagged，区分 buggy 版本依赖上游单元测试；若在慢速/高负载机器上竞争被触发，用例同样能抓住回归。不盲目增加并发追逐调度窗口。
- 数字口径（2026-11-11 复审修订，源码+实测双重确认）：普通发票不传 `payment_hash` 时节点生成并存储原像，每张产生 `PutCkbInvoiceStatus(Open)` + `PutPreimage` 两条变更；hash-only 标记发票只有 1 条 Open。`PR1718-01`：160 张普通 + 1 标记 = **321 条**相关通知；`PR1718-02`：批次 1 / 批次 2 各 80 张，关闭 A 后的目标集合为 **161 条**（80×2 + 1）。
- 线程异常必须传播（2026-11-11 复审修订）：突发创建用 `ThreadPoolExecutor` 并读取全部 `future.result()`；全部创建 RPC 成功、成功数量与 hash 数量正确且无重复，是后续通知判断的前提，任何请求失败都直接使测试失败。
- 收集与判重方式（2026-11-11 复审修订）：事件按到达顺序完整保存（事件类型 + payment_hash + 顺序号），不先转 set 再判重；主收集在收齐目标集合后结束（上限 120 秒），随后固定收尾观察 5 秒，捕捉紧跟在最后一个目标事件之后到达的重复事件；批次 1 残留事件在 B 连接上出现不直接失败，只是不计入目标 161 条。

## 用例

公共约定：devnet；fiber1 单节点即可（store 变更本地产生）。「突发」指订阅建立后用 16 个并发线程调用 `new_invoice` 创建普通发票并记录全部成功返回的 `payment_hash`；「标记发票」指显式传 `payment_hash`（hash-only）的一张发票，用于证明突发后订阅仍存活。通知以 WebSocket 收到的 `subscription` 消息为准，按事件类型 + payment_hash 过滤出本批相关通知（排除后台无关变更）；不要求不同发票之间的全局顺序，只要求同一发票的 Open 先于其 Preimage。等待：主收集收齐目标集合即结束（上限 120 秒）+ 固定收尾观察 5 秒。耗时：两条用例合计实测约 46 秒（2026-11-11，rc2）。

<!-- TEST-CASES-BEGIN -->
| 用例 | 场景 | 预期结果 | 防止的问题 | 优先级 |
| --- | --- | --- | --- | --- |
| `PR1718-01` | - [x] 对 fiber1 建立 `subscribe_store_changes` WebSocket 订阅后，用 16 个并发线程突发创建 160 张发票（记录全部 payment_hash），再创建 1 张 hash-only 标记发票 | 收齐本批全部 321 条相关通知（每张普通发票 PutCkbInvoiceStatus(Open) + PutPreimage 各 1 条，标记发票 1 条）：按 payment_hash 核对无丢失、无重复；每张发票的 Open 先于其 Preimage；标记事件在本批 321 条相关通知中最后到达 | 突发变更导致订阅静默失效，或整类事件（如全部 Preimage）丢失而计数断言放行 | P0 |
| `PR1718-02` | - [x] 对 fiber1 建立两个并发订阅 A、B；先发批次 1 突发（80 张发票），确认 A、B 均收齐批次 1 通知后关闭 A，再发批次 2（80 张发票）+ 1 张 hash-only 标记发票 | 按事件类型与批次 2/标记发票的 payment_hash 核对：B 收齐全部 161 条相关通知，无丢失、无重复；批次 1 残留事件不计入目标集合（出现也不因此失败）；订阅者互不影响 | 单个订阅者退出误伤其他订阅者的通知投递 | P1 |
<!-- TEST-CASES-END -->

## 测试树

<!-- TEST-TREE-BEGIN -->
- subscribe_store_changes 订阅遭遇 store 突发变更
  - 单订阅 + 160 张发票突发（321 条相关通知）+ 事后标记变更
    - PR1718-01 [primary] -> SPEC-01：按 hash 核对通知完整、无重复、同发票局部有序，突发后订阅存活
  - 双订阅：批次 1 收齐后关闭其一，批次 2 + 标记继续
    - PR1718-02 [primary] -> SPEC-02：存活订阅者收齐断开后的全部 161 条相关通知
  - 变更量低于突发阈值（≤16 条） [not_applicable] — 新旧版本行为一致，无区分度
- 进程内 CCH actor 的订阅路径 [not_applicable] — 上游 Recorder 测试验证通用 StoreChangePort 向 actor 投递的完整性与顺序（101 条全收）；本批不证明真实 CCH 订阅路径或端到端链路；两个 WS 客户端共享同一 PubSubServerActor，黑盒不能证明停止 actor 的清理，该声明已移除
<!-- TEST-TREE-END -->

## Spec

### SPEC-01：突发后订阅持续有效且通知完整
- Condition: 客户端通过 WebSocket 订阅 `subscribe_store_changes`；store 在短时间内产生 321 条相关变更（160 张普通发票 × 2 + 1 张标记发票），远超旧广播通道容量（16）。
- Expected: 每条变更产生一条通知；同一发票的 Open 先于其 Preimage；标记发票的 Open 在本批相关通知中最后到达；连接不报错、不静默失效。
- Observable: 按事件类型 + payment_hash 核对收到 160 个 `PutCkbInvoiceStatus(Open)`、160 个 `PutPreimage`、1 个标记 Open，无丢失、无重复；事件按到达顺序保存并判重。
- Basis: [PR #1718 描述与 diff](https://github.com/nervosnetwork/fiber/pull/1718)（新版 store 直投订阅者信箱；上游 Recorder 单测验证投递完整性与顺序）。
- Source: explicit
- Testing: required

### SPEC-02：订阅者互不影响
- Condition: 同一节点上存在两个并发订阅 A、B；批次 1（80 张发票）的通知在 A、B 均收齐后关闭 A，再发批次 2（80 张发票）+ 1 张标记发票。
- Expected: B 继续完整接收 A 断开后的全部相关通知（161 条）；A 的关闭不影响对 B 的投递；批次 1 残留事件不计入目标集合、不因此失败。
- Observable: 按事件类型与批次 2/标记发票的 payment_hash 核对 B 的通知集合：无丢失、无重复。
- Basis: [PR #1718 描述与 diff](https://github.com/nervosnetwork/fiber/pull/1718)。订阅者移除的 actor 级清理由上游单测（`a_stopped_subscriber_is_removed`）覆盖；本规则只约束客户端互不影响，不声明 actor 清理。
- Source: explicit
- Testing: required

## 验证设计

- 独立预期：「订阅不应因突发失效」「订阅者互不影响」来自 RPC 订阅语义本身（订阅即持续接收），不来自被测实现；旧版失效场景为**上游进程内连续发送场景**（Recorder actor 经旧通道 101 条收 0 条），非黑盒可复现场景；rc1 黑盒实测无法稳定复现（见「已决定」），本批属一般订阅回归。
- 实际入口与观察：公开 WebSocket RPC（`subscribe_store_changes` / `unsubscribe_store_changes`）与公开 RPC（`new_invoice`）；用 `websocket-client` 包直连 fnn RPC 端口，不依赖框架外的内部钩子。
- 证明边界：真实构建的 devnet 节点。突发量 321 条相关变更约为旧缓冲上限 16 的二十倍，无需精确控制消息时序；若运行环境足够慢使旧版竞争被触发，完整性断言与等待超时会抓住回归，但本用例不以区分新旧实现为契约。
- 无 CCH 依赖：本评审不引入 BTC/LND 环境；上游 Recorder 测试验证通用 StoreChangePort 向 actor 投递的完整性与顺序，本批不证明真实 CCH 订阅路径或端到端链路。
