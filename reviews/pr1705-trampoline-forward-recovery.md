# PR #1705：trampoline 历史转发记录与升级恢复

## 导读

这个 PR 在转发 TLC 真正离开通道时清理遗留记录，并在恢复旧通道时删除已经没有对应 TLC 的记录。清理前保留“这是转发”的判断，避免转发成功后把中间节点自己的同哈希发票误标为已支付。

外部协议和 RPC 字段没有变化。主要影响长期运行的 trampoline 节点，以及带历史转发数据重启、升级的节点。成功转发的记录随 TLC 删除；失败 TLC 要等原有确认条件满足后才物理删除，其历史记录暂留不等于资金仍被占用。

**建议增加两条升级回归**：旧版产生成功、失败历史后，保留另一笔在途付款，升级中间节点，分别验证成功结算和取消退款。两条均在真实节点、公开 RPC、正常停机与原目录重启上执行。CKB / UDT 使用相同操作和判断依据，作为每条的参数变体。

PR 的四个 Rust 测试已经检查记录生命周期、恢复时保留有效记录，以及中间节点发票隔离/直达收款对照，本仓库不重复建立相同的内部状态用例。RPC 不暴露转发记录数量；以下用例验证升级兼容性和资金结果，不宣称证明内存泄漏消失或必然旧失败/新通过。

依据：[PR #1705](https://github.com/nervosnetwork/fiber/pull/1705)，截至 2026-11-10 已合并。初次评审为静态分析；真实节点回归结果见末节。

## 需要你决定

- 本批 `PR1705-01`、`PR1705-02` 的升级场景、CKB / UDT 变体、预期和优先级已获用户明确确认。升级后行为属于依据现有转发/恢复语义提出的兼容性验收口径（Spec 保留 inference 来源）。两个用例的 CKB/UDT 变体均已实现并通过；本批无待确认项。
- 内部记录数量与内存增长不作为本批验收指标；旧版到目标版的在途恢复是本批重点。旧版本按用户要求只使用仓库已有的 0.9.1，升级目标为 CURRENT_DEV；正常启动所需数据库迁移纳入升级步骤。强制 kill、链上强关、MPP、多 trampoline 不计入这两条用例。

## 用例

公共前提与操作：

- 拓扑 A→T→C，A 为付款方、T 为唯一 trampoline，C 为收款方；没有其他通往 C 的通道。A→T 公开，T→C 私有。每次付款显式使用 `send_payment.trampoline_hops=[T]`；CKB / UDT 分别建立隔离通道，三节点统一使用相应资产，`ckb_hash`，单 part、单付款串行操作。
- 先全部使用仓库已有 `FiberConfigPath.V091_DEV`（0.9.1）。经 RPC 成功结算一张 C 发票，再使另一张 C hold 发票到达 Received 后取消并确认 Failed。再成功付款一次，推动历史失败记录按正常协议收尾；若对应失败 TLC 仍存在，只要求确认移除、不要求列表清空。用成功历史保证经过旧版已知的遗留记录产生路径，不读取或改写数据库来断言记录存在。
- 在同一通道发起新的 C hold 发票付款 H：C 只接收 payment_hash、无原像，allow_trampoline_routing=true、allow_mpp=false。待 C Received、两跳对应 H 的 TLC 均 Committed 后，T 通过 new_invoice 创建同哈希、同资产的本地 Open hold 发票，不存入原像。这是稳定的业务等待状态，不截取消息竞态窗口。
- 在发起 H 前记录四侧余额；持单后记录两跳 TLC 的 hash、方向、ID、金额、expiry。正常停止 T，等进程退出，保留原数据目录；仅用仓库 `FiberConfigPath.CURRENT_DEV` 启动 T 原目录，经现有 `start_with_confirm(confirm="y")` 回答正常迁移确认，A/C 保持 0.9.1。密钥、配置、链和合约不变。恢复原 channel_id 到 ChannelReady 后，确认 C 仍 Received、T 本地发票仍 Open，再执行各行结算/取消。
- 每条每资产预计 4–8 分钟（含环境准备、不含编译）。RPC 轮询 0.5 秒，开通/图同步/恢复/结果收敛各自上限 120 秒，回归代码沿用各步骤的有界等待。通道使用默认 1 epoch commitment delay；C 的 hold 发票显式设 expiry=3600 秒、final_expiry_delta=86400000 毫秒，付款 tlc_expiry_limit=172800000 毫秒。升级前，核对公开 TLC expiry 减当前时间、再减该通道的提前移除裕量（commitment delay 的 2/3 加 CHECK_CHANNELS_INTERVAL），至少仍有 10 分钟；不足则判准备失败。检查以扣除裕量后的时间为准。
- 时间依据：release 的 1 epoch 为 4 小时，默认 final_expiry_delta 约 160 分钟，已经接近提前移除裕量，故本批明确加长。无原像 hold 初始记录用 min(发票到期、TLC 到期)，但发票 Received 后忽略 hold timer，真正上限由通道提前到期检查决定；有原像 MPP 的 120 秒 release / 20 秒 debug 常量不适用于本批。编译常量不作运行时调短。升级前检查仓库已有二进制，持单期间不编译。
- 后续健康付款使用新 hash、同一 A→T→C 路径，必须真实成功。取消后允许协议自身重试，但全部尝试必须在结果窗口内结束；不套外层重发 helper，不把等待超时当作取消成功。

<!-- TEST-CASES-BEGIN -->
| 用例 | 场景 | 预期结果 | 防止的问题 | 优先级 |
| --- | --- | --- | --- | --- |
| `PR1705-01` | - [x] A→T→C 在旧版已有成功和取消失败的转发历史；C 持有新付款 H、T 有同哈希 Open 发票时，保留数据升级 T，再由 C 提交正确原像；CKB/UDT 分别执行，预计各 4–8 分钟 | A 的 H 成功且原像正确，C 发票 Paid，T 同哈希发票仍 Open；两跳资金只按该笔金额转移一次、H 的已记录 TLC 退出在途，通道保持可用且后续新付款成功 | 升级误清在途转发依据，导致付款卡住、中间节点发票误结算或重复划款 | P0 |
| `PR1705-02` | - [x] A→T→C 在旧版已有成功和取消失败的转发历史；C 持有新付款 H、T 有同哈希 Open 发票时，保留数据升级 T，再由 C 取消 H；CKB/UDT 分别执行，预计各 4–8 分钟 | C 发票 Cancelled，A 的 H 最终 Failed 且无原像，T 同哈希发票仍 Open；H 不改变两跳结算余额、相关 TLC 不再占用资金，通道保持可用且后续新付款成功 | 清理历史失败记录时影响仍在途的付款，或失败处理反复执行造成资金占用、通道不可用 | P1 |
<!-- TEST-CASES-END -->

## 测试树

<!-- TEST-TREE-BEGIN -->
- trampoline 转发结束与旧数据恢复
  - 真实 0.9.1 数据：已有成功转发、取消失败转发，同时有另一笔稳定持单
    - 保留数据升级 T，保留有效转发的结算能力
      - C 结算：PR1705-01 [primary] -> SPEC-01
      - C 取消：PR1705-02 [primary] -> SPEC-01
    - 发票归属与资金隔离
      - 转发成功只结算 C：PR1705-01 [ref] -> SPEC-02
      - 取消不结算 T、不转移资金：PR1705-02 [ref] -> SPEC-02
  - [not_applicable] 成功/失败删除时记录数量、未确认失败的精确剪枝边界、恢复后的记录集合 — 不符合本仓库用例偏好：需要内部状态/精确消息顺序，PR 自带 Rust 测试已覆盖
  - [not_applicable] 新版单次转发的同哈希本地发票隔离和直达收款 — PR 自带进程内测试已覆盖；现有 PR1672-04 另有真实直达对照，不新增重复行
  - [not_applicable] REMOVE 位只应用一次 — base 已在调用前设置同一标记；新增调用后补记的幂等性不单独伪装成新的外部行为
  - [unanalysed] 其他旧版本、链上恢复、kill 中断、MPP、多 trampoline — 本批限定 0.9.1 创建的通道与正常升级；扩展时从版本兼容、offline_restore_mode、恢复后的 payment owner 与对应现有测试进入
  - [pending] 泄漏规模与性能收益 — RPC 未暴露遗留记录数，PR 没有给出资源阈值；若需要性能验收，先定义固定工作量、稳定资源指标和可接受变化
<!-- TEST-TREE-END -->

## Spec

### SPEC-01：升级恢复既有通道时，在途转发仍可结束且通道继续可用
- Condition: 0.9.1 创建的通道含历史成功/失败转发，同时有公开 RPC 可观测的已承诺 hold 付款；正常停机，使用 CURRENT_DEV 迁移并恢复同一节点目录。
- Expected: 由 C 决定的结算或取消结果最终返回 A；未结束转发不因历史记录清理而失去恢复能力；结果完成后同一路径的新付款成功。
- Observable: 原 channel_id、ChannelReady、A get_payment、C get_invoice、两跳对应 TLC 的状态/资金占用，以及新的独立付款结果。
- Basis: [恢复时仅保留仍有 TLC 的记录](https://github.com/nervosnetwork/fiber/blob/f2f06c48d1002cef2ddd9130974fcd086f8e6dad/crates/fiber-lib/src/fiber/channel.rs#L8143-L8151)，[恢复入口](https://github.com/nervosnetwork/fiber/blob/f2f06c48d1002cef2ddd9130974fcd086f8e6dad/crates/fiber-lib/src/fiber/channel.rs#L5250-L5265)，既有 recover_pending_forward_tlcs 保留 trampoline payment owner 的逻辑；端到端升级验收为推断。
- Source: inference
- Testing: required

### SPEC-02：付款结果和资金只属于实际转发，保持中间节点同哈希发票隔离
- Condition: T 正在转发 A 到 C 的 H，同时通过公开 RPC 持有同哈希的本地 Open hold 发票；T 经保留数据升级，C 随后结算或取消。
- Expected: 成功时仅 C 发票 Paid，资金按两跳 TLC 金额分别转移一次；失败时两跳结算余额不变；T 的本地发票始终 Open。
- Observable: A 的终态/原像，T/C 发票状态，隔离通道四侧余额、TLC 记录与仍被占用的金额。
- Basis: [清理前保存转发判断并更新发票](https://github.com/nervosnetwork/fiber/blob/f2f06c48d1002cef2ddd9130974fcd086f8e6dad/crates/fiber-lib/src/fiber/channel.rs#L2239-L2299)，[PR 新增 trampoline 发票隔离测试](https://github.com/nervosnetwork/fiber/blob/f2f06c48d1002cef2ddd9130974fcd086f8e6dad/crates/fiber-lib/src/fiber/tests/trampoline.rs#L4626)，资产结算不变量与已有 PR1672-02/03；升级与 UDT 组合为兼容性推断。
- Source: inference
- Testing: required

## 验证设计

**独立预期与观察。** 设持单时记录的 A→T 金额为 x、T→C 金额为 y，发送前余额为基线。成功后，A 在 A–T 的余额减 x、T 在 A–T 加 x、T 在 T–C 减 y、C 在 T–C 加 y；T 净收入 x−y，各通道总额守恒。list_channels 的 local_balance/remote_balance 在 CKB 通道按 Shannon、UDT 通道按 token 最小单位核对；offered_tlc_balance/received_tlc_balance 在无其他付款的通道均应回到零。UDT 附带的 CKB 容量通过 CKB get_live_cell 检查原 funding outpoint 仍 live 且 capacity 不变，不假设 Fiber RPC 有独立的 CKB reserve 字段。失败后 H 的四侧结算余额与发送前一致；保留的 RemoveAckConfirmed 历史行允许存在，按 hash/方向/ID 及占用金额证明对应在途资金已释放。后续健康付款之前完成这些检查，避免混入另一笔资金。

**实际入口。** 未修改的 FNN 通过 new_invoice、send_payment、get_payment、get_invoice、list_channels、settle_invoice、cancel_invoice 和正常进程启停驱动。每个窗口轮询新 RPC 结果，既检查 A 的终态，也检查 T/C 状态与金额，避免仅凭发送请求返回判断成功。T→C 私有且显式 trampoline_hops 可排除普通直达路由。

**证明边界。** 是真实节点升级端到端兼容性设计；不读取 waiting_forward_tlc_tasks、不改数据库、不使用 attack fnn 或测试钩子。即使 0.9.1 原样重启也通过，仍是有效的升级回归，但不构成泄漏修复的差分证据。没有给“历史记录一定仍在磁盘”作超出公开接口能力的断言。

## 变更依据与现有覆盖

固定源码仓库 nervosnetwork/fiber；base tip 和 merge-base 均为 `20a0ba487dcebebb9770a91819bdef51fdc246ce`，head 为 `f2f06c48d1002cef2ddd9130974fcd086f8e6dad`，合并提交为 `811db5bc8096fab19852bcdc3b20709db8655047`。GitHub compare 确认 head ahead 3 / behind 0。生产代码仅 channel.rs，另改两份 Rust 测试，共 254 行增加、1 行删除；没有协议、RPC schema 或合约变更。

| 代码变化 | 前后差别与测试处置 |
| --- | --- |
| apply_remove_tlc 删除 waiting_forward_tlc_tasks 对应项 | 成功 TLC 立即删除时清理；失败 TLC 经既有确认规则剪枝时清理。内部集合由 Rust lifecycle / pruning 测试验证 |
| drop_stale_waiting_forward_tlc_tasks + 恢复入口调用 | 恢复时先过滤无对应 TLC 的旧项，保留现有 TLC；两条升级用例检查与在途资金恢复组合的外部结果 |
| was_waiting_forward | 清理后查询会丢失“原来是转发”的信息，因此先取值再决定本地发票 Paid；升级后的同哈希发票是两条用例的附加观察 |
| 成功 remove 后补 AppliedFlags::REMOVE | base 的同一函数调用前已经设置 REMOVE，remove_tlc_with_reason 的失败路径也没有清除此标记；未发现可独立证明的新外部失败行为，不宣称旧版必然重复结算 |

PR 自带四个测试：test_failed_tlc_pruning_only_drops_confirmed_forward_markers、test_channel_restore_prunes_orphan_forward_markers、test_trampoline_forward_marker_lifecycle、test_trampoline_fulfill_does_not_settle_forwarder_invoice。前两项直接构造内部状态，后两项使用进程内节点并读取状态；未覆盖从 0.9.1 的真实数据升级到当前版本的节点操作。

现有映射逐项检查：
- `PR1672-02/03/04` 均仅各一处映射，在 test_cases/fiber/devnet/settle_invoice/test_pr1672_forwarded_local_invoice.py。02/03 通过 send_payment_with_router 做普通转发，无 trampoline_hops；04 是本地直达对照。原有断言仍适用，均不等于本批升级覆盖。
- `PR1673-02` 仅一处映射，在 test_cases/fiber/devnet/security/test_poc_trampoline_inner_hash_algorithm_mismatch.py；已有两跳资金断言可参考，但整个文件使用 attack/debug 夹具，不直接当本批未修改节点的验证。
- trampoline_routing/test_same_invoice_race.py 与 test_same_invoice_mpp_overpay.py 已检查失败后再次付款；没有版本升级或同哈希本地发票前提，且部分分支把 send_payment 抛错直接记为失败，复用前需保留本批“已真正到达下游”的前提。
- `PR1705-01`、`PR1705-02` 映射至 test_cases/fiber/devnet/trampoline_routing/test_pr1705_upgrade_forward.py，分别对应升级后结算/取消方法，均由 CKB/UDT 两个类执行。公共 helper 只复用旧版转发历史、持单和升级准备，结算/退款断言保留在各用例方法中。复选框只表示映射存在，执行结果独立记录。现有 CI 的 fiber_test_trampoline_routing_one_way job 包含该分类，沿用 prepare 产物中的 0.9.1/current；download_fiber.py 已包含 0.9.1，无需 PR 专用构建；通用 make fiber_test 的显式目录列表没有 trampoline_routing，本阶段保留其原范围。

环境核对：测试仓库 HEAD `004e5132bbc00e0b1c0c150fd1a6c06b96395a87`，既存修改保留。本地 fiber 工作区在 `4319344528ded25e9c024a66f6783736387d21a9` 且含其他修改，本次未切换；分析使用固定 base Git 对象、GitHub head 文件与完整 PR diff。长期回归使用 download/fiber/current/fnn，本次版本输出为 `fnn Fiber v0.10.0-rc1 (7aa1d5d 2026-11-09)`；0.9.1 输出为 `fnn Fiber v0.9.1 (9a561b3 2026-09-10)`。执行记录单独保存两个实际二进制的哈希；本地 fiber checkout 仅为源码环境记录，其提交不等同于 current 的构建来源。

当前模板 source/fiber/dev_config_3.yml.j2 的 FundingLock code_hash 为 `0xe7576045a47bb4cc172ffd0306ce01e232dc43805becb356e58f8bda9df51e26`，CommitmentLock 为 `0xf3775d5328de71717f2c5614fa06b9b93c48b7d90c1e135c0812c74ee3126453`。这些是配置值，尚未绑定本轮实际链上部署/合约提交；执行阶段需核对所用 devnet 和 0.9.1/current 二进制，不使用旧 README 的不同哈希作证据。

## 检查与运行结果

两条用例经过独立设计复核及人工确认。设计复核补充了到期提前移除裕量和 UDT funding cell 的公开观察方式；复核结论限于设计，未宣称独立实现复核。

2026-11-10 实际运行：旧节点 `fnn Fiber v0.9.1 (9a561b3 2026-09-10)`，升级节点 `fnn Fiber v0.10.0-rc1 (7aa1d5d 2026-11-09)`。测试始终使用仓库 `V091_DEV` / `CURRENT_DEV` 配置，不固定 PR 历史提交。仅升级 T，A/C 保持 0.9.1；两个用例的 CKB/UDT 四个变体通过。

```bash
venv/bin/python -m pytest test_cases/fiber/devnet/trampoline_routing/test_pr1705_upgrade_forward.py -x -v -o log_cli=false
```

原始结果：`4 passed in 316.64s (0:05:16)`，退出码 `0`。成功路径验证原像、发票归属、逐跳余额只转移一次；取消路径验证无原像、四侧余额不变、TLC 释放。两条均验证原通道继续付款，UDT funding cells 保持 live 且内容不变。

长期回归代码使用框架清理、RPC 超时和有界等待，无专用构建、evidence 文件输出、signal 计时器或目录备份。CI 已收集 trampoline_routing 分类。

设计结构检查退出 `0`。发布分支项目映射检查退出 `1`，有既存孤儿映射 PMP-01～05、SETTLE-01，未新增孤儿映射。本批两条用例均有唯一 TEST-MAP，映射存在和实际执行通过分别核对。
