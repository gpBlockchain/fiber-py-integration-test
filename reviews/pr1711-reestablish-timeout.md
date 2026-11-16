# PR #1711：旧通道重连超时与资金回收

## 导读

这个改动修复的是：连接已经恢复，但对端没有这条通道的数据、一直不回应通道恢复时，本端可能长期卡住。修复后，旧通道连续等待超过 5 分钟就走强制关闭，让资金进入链上回收；对端纯离线期间不计时，收到恢复消息后停止这项计时，即使双方数据不一致也避免误广播旧承诺。

主要风险是该关的通道一直不关、离线或备份恢复时误关，以及强关后只看到状态变化却取不回钱。本批通过真实旧节点建通道、升级、保留身份清空对端数据库、停机复制和恢复目录来验证。新节点默认新建 V2 通道，直接用当前版本开通道不足以覆盖主修复。

补充风险：对端每次都在 5 分钟前断开，重连又从零计时，可能持续推迟自动关闭；旧协议恢复期间的入站 TLC 移除和 ACK 超时也未提供通用兜底。新增用例采用用户确认的累计在线 300 秒期限，离线暂停、重连继续累计，并预留 90 秒检查裕量；不把一次长连接最后能关闭当作该问题已解决。

上游已覆盖内部计时条件和拦截消息；本仓库补充部署升级、实际链上资金、等待中运维和健康通道隔离，不复制内部状态单测。初版为设计分析；后续 `PR1711-08` 的自动化与实测记录见文末。

依据：[PR #1711](https://github.com/nervosnetwork/fiber/pull/1711)，已于北京时间 2026-11-06 合并。PR 报告 base `20a0ba487dcebebb9770a91819bdef51fdc246ce`、head `3c8ff0536ad5d720dcc50776504f2002ae558b50`；本地保存的是 squash 提交 `ab6364f1169bde596a7342f08767e79a4750a8ca`，其实际父提交为 `3bdb69f60c3eb58e156b66069eaca2bddf115f01`。已逐文件核对 GitHub 三个 patch 的全部 hunks 与本地父提交→squash diff 一致；不把 base→squash 之间其他资金交易改动算进本 PR。

## 当前范围

用户要求先 skip `PR1711-08`，继续补充其余用例，现按已有 `PR1711-01`～`PR1711-07` 实现自动化。用户同时明确旧版本只使用仓库已有 v0.9.1；`04` 增加 current 新建 V2 对照，不纳入中间版本 V1。`08` 保留累计在线 300 秒及 90 秒裕量的原预期和历史失败记录，暂时跳过执行。

`PR1711-08` 的自动强关观察上限为累计在线 **390 秒**，按 **150＋150＋90 秒** 三个短连接窗口构造两次断连重连；离线和启动耗时不计入累计值。广播后的资金回收另限 660 秒墙钟。`PR1711-02` 无在途付款，继续描述该 PR 的单次重置行为；`08` 的累计期限针对已提交原像且无恢复进展的入站付款，当前不将此规则扩展到所有空闲通道。

## 用例

### 版本与共同前提

- A 为保存待测通道的节点，B 为故障对端；需要隔离观察时另建 A–C 健康通道。主路径使用正式构建，无消息拦截、改二进制或数据库字段编辑。
- 本批“旧通道”只使用两个官方 `v0.9.1 / 9a561b3e7b786001488b61303a0663d9f4f22259` 建立的 Legacy 通道。先完成双向付款，再正常停机保留副本，通过目标版本正常启动/迁移升级双方，恢复同一 channel_id、funding outpoint、余额及最新承诺 hash，并完成双向探针。用户已明确排除中间版本 V1 变体。
- 被测新版本固定为上述 squash 提交；如使用 PR head 构建，单独记录身份。现有 `current` 是后续 `7aa1d5d`，只能作为后续版本回归，不能记为该 PR 精确版本对照。新 V2 对照由目标构建双方直接创建。版本选择由真实创建版本和升级路径证明；不假设 `list_channels` 暴露协议特性。强关变体再核对链上承诺布局。
- `01/05` 各覆盖 Legacy × CKB/UDT；`02/03/06/07` 覆盖 Legacy CKB；`04` 覆盖 Legacy/V2 CKB。相同操作与判断依据共用准备和核对方法；各行耗时为单变体估计，含环境准备、不含构建。
- B 丢数据：先正常停止并备份，移走独立测试目录中的通道数据库，保留原节点身份密钥、CKB 密钥和配置；以同一 pubkey 启动空库节点，通过 `list_peers` 确认双方连接、B 的 `list_channels` 确认没有原通道。该节点正常完成节点握手，却没有原通道的恢复响应；源码未知通道分支直接忽略。不得重新生成身份，也不得只让进程离线代替“重连无响应”。
- 每次计时前停止付款负载并等所有普通付款终态、余额收敛；持单用例通过 `new_invoice` 仅提交 hash，测试端保管原像，确认发票 Received、双方能查到该 TLC，静置 60 秒无变化后才注入故障。不读取内部 ACK 字段。Received 发票不会被普通 120 秒 hold 超时移除（目标 `network.rs:4327`）；发票 expiry=3600 秒，final_expiry_delta=86400000 毫秒，付款的 tlc_expiry_limit 至少 172800000 毫秒。读取实际 TLC expiry，扣除承诺延迟的提前移除裕量后仍须覆盖整条用例预算，避免另一个超时抢先强关。配置以目标版本公开参数验证为准。
- `07` 的备份通过正常运维取得：B 停机，A 正常重启且原通道可查，等待启动后的自动备份目录完整落盘（上限 120 秒），正常停止 A 后用目标 FNN `--restore` 恢复该副本，再启动并查询 Stale。该版本没有注册 `backup_now` RPC，不能照搬被跳过的旧测试。自动备份的首次调度见 `store/actor.rs:67–73`，不手改备份内容。
- `05` 的合作关闭请求必须使用 `force=false`；请求成功后，额外查询 A 确认 ShuttingDown、原持单仍在 `pending_tlcs`，且 funding cell 仍 live，然后才停止 B。持续持单提供稳定等待窗口，不依赖卡住某条关闭消息的时序。强关后用 `settle_invoice` 提交原像，内置 Watchtower 走正常链上领取；固定 devnet 的承诺延迟并启用实际自动出块，领取窗口仍服从链上约束。
- `08` 本轮仅使用仓库已有官方 v0.9.1 创建的 Legacy CKB 通道再升级，遵循反馈中“旧版本只考虑仓库有的0.9.1”；不新增中间版本构建。故障前只保留一笔 B→A 已承诺持单，A 没有发出的在途 TLC，也未回滚自己的目录。B 丢库后第一次同身份重连，A 调用一次 `settle_invoice` 提交正确原像，确认请求成功，开始持续观察实际资金；以此排除测试自己一直不放行持单造成的等待。
- `08` 保持 A 进程和最新目录不变，让 B 依次在线 150、150、90 秒；前两轮末正常停止 B，确认断连后保持离线 10 秒，再原空库启动并重连，每次启动/连接上限 120 秒。按实际观察到的在线时长累计，最后一轮仅补足 390 秒总上限，不再额外等一个完整窗口。若已自动强关，立即结束故障注入并进入资金核对，无须凑满三轮。逐轮记录真实连接窗口并确认 B 仍无原通道；出现未计划断连、连接未成立或单次在线达到 300 秒，判场景构造失败。主动强关、恢复 B 旧库和尾部额外保持在线超过 300 秒都不得充当自动退出成功。

### 等待与观察

正式二进制的恢复期限为 **300 秒**；维护轮询 release 为 **60 秒**、debug 为 **3 秒**，只有 `cfg(test)` 才把期限缩为 20 秒。以 RPC 连接完成前后的单调时钟界定连接窗口，前 290 秒检查无提前强关，持续无响应至连接完成后 390 秒要求已广播原 funding 的消费交易；390 秒包含一次 60 秒轮询和观察裕量，不声称精确毫秒边界。每秒查询，任意意外断连都显式失败或重新标记准备阶段，不能沿用旧起点。

离线观察 390 秒；连接恢复、迁移后探针等各限 120 秒。资金回收使用正常 devnet 出块推进实际相对锁定期，单独给链上结算最多 660 秒，整条上限见各行；链上出块不缩短 300 秒墙钟期限。健康 A–C 每 30 秒一次双向探针并独立对账。负向“未广播”同时检查 funding cell 仍 live、记录的承诺 hash 不在交易池/链上，不能只看 `ChannelReady`。

`08` 使用独立窗口：三轮累计在线上限 390 秒，加两次离线及有界重启，故障阶段约 7–11 分钟。按双方 `list_peers` 确认的在线区间累计，离线/启动时间暂停计数，重连后续加而非清零；300 秒为确认的期限，其后累计在线 90 秒为维护检查及观察裕量。390 秒内自动关闭则立即核款，耗尽仍未广播即失败，不改成长连接等它通过。广播后的链上到账另限 660 秒墙钟，不混入自动关闭期限。单变体预计 16–24 分钟、总预算 28 分钟，含旧通道创建与升级、不含编译。有限观察中“仍悬挂”证明违反已确认的期限，不声称证明永久锁死。

<!-- TEST-CASES-BEGIN -->
| 用例 | 场景 | 预期结果 | 防止的问题 | 优先级 |
| --- | --- | --- | --- | --- |
| `PR1711-01` | - [x] 已升级的旧 A–B 通道无在途付款，B 保留身份清空数据库后重连，A–C 持续付款；等待自动关闭及链上回收（12–18 分钟，上限 22 分钟） | A–B 不提前关闭，并在持续重连无响应 390 秒内广播强关；A 按原余额收回本金扣实际费用，A–C 付款和余额正常 | 丢数据对端使资金长期锁住，或故障通道拖垮健康通道 | P0 |
| `PR1711-02` | - [x] 已升级旧通道的 B 丢数据重连，等待 150 秒后再停 B 390 秒，随后同身份空库重连并保持在线（18–24 分钟，上限 28 分钟） | B 离线期间不强关；第二次连接按完整新窗口等待后有界强关，A 最终收回本金 | 把离线时间或上次连接的等待累加，导致过早关闭 | P1 |
| `PR1711-03` | - [x] 已升级旧通道的 B 丢数据重连，等待 150 秒后正常停止 A，从原目录重启并与 B 重连，保持在线（14–20 分钟，上限 24 分钟） | A 启动成功且保留原通道，最终重连后 390 秒内自动强关并收回本金；没有重复消费或遗留待结算资金 | 进程重启丢失超时检查，通道重新永久卡住 | P1 |
| `PR1711-04` | - [x] 已升级 Legacy 或新 V2 通道有 B→A 持单，正常停止 B 390 秒后原目录重启，A 收到正常恢复后结算持单并再观察 390 秒（15–19 分钟，上限 23 分钟） | 离线及恢复后均不误关；持单 Paid、付款 Success、TLC 清空、余额只变化一次，之后双向付款成功 | 离线误触发强关，或恢复成功后遗留计时器误关可用通道 | P0 |
| `PR1711-05` | - [x] 已升级旧通道有 B→A 持单，A 请求合作关闭且 funding 尚未被消费；B 随后丢数据重连，自动强关后 A 提交原像并回收资金（12–18 分钟，上限 22 分钟） | 等待合作关闭也会在持续重连无响应 390 秒内强关；A 本金和该笔应收款按账本到账，发票 Paid、无遗留本方 TLC 资金 | 合作关闭中的旧通道漏过超时，或关闭状态变化却拿不到在途款 | P0 |
| `PR1711-06` | - [x] 已升级旧通道停机复制 A 完整目录，恢复正常使用并完成至少 3 笔付款；再次停机保留最新副本，换回旧副本重连 B，观察 390 秒，最后换回最新副本恢复（10–15 分钟，上限 19 分钟） | 旧副本期间双方不自动广播已撤销承诺、funding 保持未消费；恢复最新副本后原通道双向付款及账本正确 | 对端已回应但数据落后的本端仍触发超时，广播旧承诺遭罚 | P0 |
| `PR1711-07` | - [x] 已升级旧通道用正常启动生成的自动备份，经 CLI 恢复进入待对端核对状态，B 保留身份清空数据库后重连，观察 390 秒（9–13 分钟，上限 17 分钟） | A 保持等待核对、funding 未消费且没有自动广播旧承诺，节点 RPC 持续可用 | 把恢复备份后尚未核对的数据当成可安全强关的通道 | P0 |
| `PR1711-08` | - [x] v0.9.1 创建的 Legacy CKB 通道升级后有一笔 B→A 持单；B 保留身份丢库重连，A 提交原像，B 按 150＋150＋90 秒在线窗口经历两次停机断连和空库重连，A 持续运行；自动强关后核款（16–24 分钟，上限 28 分钟） | 累计在线无进展期限为 300 秒，离线暂停、重连不清零；加检查裕量后 390 秒内自动广播强关，随后 660 秒内 A 本金和应收款扣实际费用后到账，超时仍悬挂判失败 | 对端反复重置恢复计时，使已请求结算的入站资金长期卡住 | P0 |
<!-- TEST-CASES-END -->

## 测试树

<!-- TEST-TREE-BEGIN -->
- 旧恢复协议：v0.9.1 创建的 Legacy（用户明确不纳入中间版本 V1）
  - 对端重连但丢失通道数据
    - 正常已用通道：资金回收及健康通道隔离：PR1711-01 [primary] -> SPEC-01, SPEC-04
    - 等待期间再次离线：PR1711-02 [primary] -> SPEC-01, SPEC-02, SPEC-04
    - 连续短连接始终无恢复进展，入站付款已请求结算：PR1711-08 [primary] -> SPEC-05
    - 等待期间本端正常重启：PR1711-03 [primary] -> SPEC-01, SPEC-04
    - 合作关闭等待持单：PR1711-05 [primary] -> SPEC-01, SPEC-04
  - 对端正常恢复，持单仍可支付：PR1711-04 [primary] -> SPEC-02, SPEC-04
  - 旧数据的误广播保护
    - 直接回滚目录，对端数据已领先：PR1711-06 [primary] -> SPEC-03
    - CLI 恢复后对端无通道可核对：PR1711-07 [primary] -> SPEC-03
- V2 共享连接计时变更
  - 长离线及正常重启，持单恢复：PR1711-04 [ref] -> SPEC-02, SPEC-04
  - [not_applicable] 恢复消息已验证但精确卡在重放阶段、funding 恢复分支 — 依赖消息/内部状态切点，由已有上游 V2 测试承担，本批不生成黑盒用例
- 条件分支的精确边界
  - [not_applicable] 恰好 300000 毫秒、时钟倒退、已广播标志及状态谓词 — 上游状态单测覆盖代表性单条件反例，未穷举所有组合；本批只检查真实外部时窗和资金
<!-- TEST-TREE-END -->

## Spec

### SPEC-01：旧通道重连没有恢复响应时有界转为强关
- Condition: 旧恢复协议，已就绪或合作关闭尚未广播承诺的通道，连接已恢复但没有对端恢复响应。
- Expected: 连续等待超过 300 秒后在下个维护周期触发强关；普通离线与已广播承诺不在该规则内。
- Observable: 原 funding 的消费交易、链上承诺、公开通道关闭状态。
- Basis: [PR 需求](https://github.com/nervosnetwork/fiber/pull/1711)、[周期检查及适用状态](https://github.com/nervosnetwork/fiber/blob/ab6364f1169bde596a7342f08767e79a4750a8ca/crates/fiber-lib/src/fiber/channel.rs#L6459-L6487)、[正式检查间隔](https://github.com/nervosnetwork/fiber/blob/ab6364f1169bde596a7342f08767e79a4750a8ca/crates/fiber-lib/src/fiber/network.rs#L185-L192)。
- Source: explicit
- Testing: required

### SPEC-02：离线不计入恢复超时，正常恢复后不误关
- Condition: 对端再次离线，或恢复响应成功且付款处理已完成；V2 正常恢复完成也属于健康对照。
- Expected: 本 PR 的计时策略是离线清除本项计时，下一次重连重新开始；旧协议收到恢复消息即停止，V2 保留至其完整恢复流程结束。该策略本身没有累计无进展上限，不代表资金必然自动退出；反复刷新计时的独立验收要求见 SPEC-05。
- Observable: 离线/恢复后 funding 未消费，持单结算、双向新付款和余额正确。
- Basis: [PR 说明](https://github.com/nervosnetwork/fiber/pull/1711)、[连接处理](https://github.com/nervosnetwork/fiber/blob/ab6364f1169bde596a7342f08767e79a4750a8ca/crates/fiber-lib/src/fiber/channel.rs#L6859-L6906)。
- Source: explicit
- Testing: required

### SPEC-03：已回答但数据不一致，以及待核对的备份，均不因本项期限广播旧承诺
- Condition: 旧协议收到对端恢复消息但承诺计数差异过大，或正式备份恢复留下待核对通道。
- Expected: 前者停止本项计时；后者排除在自动强关范围之外。没有要求旧副本自动追平或恢复余额。
- Observable: 连接存活但 funding 仍 live、承诺未广播；换回正常最新目录后可付款是旧副本负例的有效性对照。
- Basis: [收到回应先清除计时](https://github.com/nervosnetwork/fiber/blob/ab6364f1169bde596a7342f08767e79a4750a8ca/crates/fiber-lib/src/fiber/channel.rs#L10165-L10172)、[旧数据校验](https://github.com/nervosnetwork/fiber/blob/ab6364f1169bde596a7342f08767e79a4750a8ca/crates/fiber-lib/src/fiber/channel.rs#L10295-L10308)、[正式恢复入口](https://github.com/nervosnetwork/fiber/blob/ab6364f1169bde596a7342f08767e79a4750a8ca/crates/fiber-lib/src/store/restore.rs#L10-L30)。
- Source: explicit
- Testing: required

### SPEC-04：运维后资金可兑现且其他通道继续工作
- Condition: 真实出资通道经历升级、数据丢失、正常重启或强关，原像由测试控制，另有健康对端。
- Expected: 原本金及应得 TLC 按独立账本到账，无重复付款/消费；健康通道继续支付。本端正常重启后仍最终触发关闭，不要求持久化重启前已等待的秒数。
- Observable: CKB/UDT 收款输出、实际费用、发票和付款终态、健康通道新付款与余额。
- Basis: PR 解决 TLC 长期停滞的目标，结合真实资金通道的验收需求推导；源码触发强关本身不证明到账。
- Source: inference
- Testing: required

### SPEC-05：已请求结算的入站付款应有不被重连反复推迟的退出期限
- Condition: Legacy 通道的本端持有有效最新承诺，仅有已承诺入站 TLC，已提交正确原像；同身份对端丢失通道数据，反复在单次 300 秒超时前断开并重连，始终没有恢复进展。
- Expected: 累计在线无进展期限为 300 秒，离线暂停但不清零、重连继续累计；在累计在线 390 秒内广播自动强关，广播后另给 660 秒墙钟验证本金及应收款到账。正常恢复完成、单纯离线以及本端旧数据待核对不据此直接强关。
- Observable: 实际连接时间线、同一 funding 的消费、目标 TLC 与发票的新查询结果、本端最终可用 CKB 收款输出及独立余额账本。
- Basis: 用户指出“SPEC-02 不就会导致资金一直卡住”并要求补例，随后确认累计在线规则并选择“300 秒（建议）”，附加 90 秒检查裕量。风险来源：[重连计时清零](https://github.com/nervosnetwork/fiber/blob/ab6364f1169bde596a7342f08767e79a4750a8ca/crates/fiber-lib/src/fiber/channel.rs#L6857-L6904)、[旧协议 ACK 判定与恢复标记](https://github.com/nervosnetwork/fiber/blob/ab6364f1169bde596a7342f08767e79a4750a8ca/crates/fiber-lib/src/fiber/channel.rs#L6393-L6440)、[恢复期间延后 TLC 操作](https://github.com/nervosnetwork/fiber/blob/ab6364f1169bde596a7342f08767e79a4750a8ca/crates/fiber-lib/src/fiber/channel.rs#L2950-L2968)。这是用户确认的验收要求，不是已证明当前实现满足的行为。
- Source: explicit
- Testing: required

## 现有覆盖与改动影响

三个变更文件分别是 `channel.rs` 的期限/条件/连接时钟，`network.rs` 的测试消息保持种类，以及 `tests/channel.rs` 的四条上游回归。后两处测试设施没有新增公开 RPC。上游覆盖：丢恢复消息会强关、纯离线不强关、拒绝恢复消息后停止计时、状态和时间边界。测试使用 20 秒期限和内部状态/消息钩子，主强关用例只检查关闭状态，未断言真实到账，也没有在途 TLC；因此本批新增的实际升级、资金与运维组合有独立价值。

PR 作者报告两条旧版失败/新版通过及 25 条恢复测试通过；本轮未复跑，只记为作者结果。V2 验证后重放和 funding watchdog 在代码中保留独立分支；本批不据此宣称那些分支已由 Python 覆盖。

- `H32V2-05` → `test_cases/fiber/devnet/compatibility/test_full_hash_old_data.py` 仅一处映射：真实 Legacy 升级、恢复原通道并付款/结算，可复用升级路径，不覆盖丢数据后的 5 分钟等待。
- `PR1728-01` → `test_legacy_settlement_balances.py` 的 CKB、UDT 两个方法：可参考精确链上到账、费用和整体超时；都是正常结算后主动强关，不替代本批自动强关。
- `PR1677-01/02/04/08` → `test_channel_v2_recovery_chaos.py` 四个方法：已有 RPC 重连、正常重启、路由/混合故障及 TLC 收敛断言；预期继续适用，但都是新建 V2，故障窗口短，既不覆盖旧通道也不覆盖 390 秒无响应。本批不改写原评审。
- `test_channel_actor_disconnect_reconnect.py::test_pending_hold_tlc_settles_after_disconnect_reconnect` 与 `send_payment/offline/test_pr1466_reestablish_retry.py` 可借用持单和双向观察，只有正常快速重连，未做长离线/升级/实际链上回收。
- `backup/test_dlp_backup_restore.py` 整个类标记 skip，引用的 `backup_now` 在目标版本也未注册，且旧用例没有长于 5 分钟的无响应观察；不是有效执行覆盖。直接目录回滚与 CLI 恢复进入待核对状态有不同前提，分别保留 `06/07`。
- 八条用例均映射到 `test_cases/fiber/devnet/connect_peer/test_reestablish_timeout.py`；其中 `08` 按用户要求 skip。`08` 补上原评审误排除的反复短连接场景；初版独立复核结论不作为这一新增风险已被覆盖的证据。

## 验证设计

- **独立账本**：故障前读取双方余额，测试端记录每笔成功付款和持单金额。无持单强关时 A 应得故障前余额；`05` 的 B→A 金额 m 成功兑现后 A 应得 A0+m，B 的权益 B0−m；`04` 的同一笔只记一次。CKB 另计预留容量和实际交易输入减输出的费用，UDT 数量精确核对、CKB 费用分开，不以宽松余额容差吞掉付款。
- **资金观察**：检查原 funding outpoint 的消费链，核对承诺和结算输出的收款脚本、资产数量及归属。A 的钱实际成为可用 cell，且没有属于 A 的残留承诺/TLC 资金才算回收；B 空库没有原付款记录，`01/05` 不要求其凭空恢复 `get_payment`。也不以 A 回款推出 B 的空库已恢复。
- **实际入口**：公开 `open_channel/new_invoice/send_payment/get_payment/get_invoice/settle_invoice/list_channels/list_peers/connect_peer/shutdown_channel`、正常 FNN 启停/自动备份/迁移/恢复 CLI，以及 CKB 公开查询和 devnet 出块。每次使用新的 RPC/链查询；日志仅诊断。
- **负例有效性**：`06` 的停机副本来自真实可付款旧通道，之后至少三次已完成的付款使其明显落后；两份副本均不改数据库字段。先成功探针、再回滚观察、最后恢复最新副本成功探针。`07` 先通过 CLI 的真实恢复并在 B 离线时由 RPC 看到 Stale，再让同身份空库 B 连接。禁止用本端未启动或对端未连接的观察冒充保护成功。
- **反复重连的独立判据**：`08` 的起点为 A 保留最新承诺时的余额 A0 与唯一入站付款 m；只提交一次正确原像，预期应收为 A0+m，费用及预留容量另按实际链上输入输出核算。`settle_invoice` 返回成功或发票状态变化均不单独代表到账。逐轮查询 pending TLC、funding 是否 live、承诺是否广播及 A 钱包变化；连通性抖动期间自动关闭后，实际兑付才证明资金退出。故障停止后的长连接触发关闭或人工强关只能用于清理，不计入通过结果。
- **其他期限的边界**：旧协议恢复期间 ACK 超时判定受 `reestablishing` 限制；收到的 TLC 到期主要安排移除，而恢复期间本地移除被延后。发出的 TLC 在 ChannelReady 下另有到期强关路径，因此 `08` 特意不放本端发出的 TLC，并让入站 TLC 的实际到期时间超出全程预算，避免把别的超时误计为恢复期限得到修复。这些是固定源码分析，尚未实测。
- **重现边界**：`01/02/05` 应可在修复前后用相同旧通道副本形成“旧版长期不关/新版有界关”的外部对照，但本轮未运行。`03/04/06/07` 是运维及误关保护回归，不强求 base 必失败。直接观察的有限等待不能证明永远不误关；`06` 的外部现象也不声称逐条解码了握手。
- **未纳入**：强杀持久化承诺、长离线且 TLC 到期退款、长期混合版本运行；V2 精确重放/funding 行为留给现有上游测试。有限轮数反复断连已纳入 `08`，无须无限运行或精确控制协议消息顺序。

## 初版设计核验（七条用例时）

测试项目 HEAD `004e5132bbc00e0b1c0c150fd1a6c06b96395a87`；保留工作区既有改动。本地 `fiber/` 为有修改的 `p2p-tap-full-payment-hash`，HEAD `4319344528ded25e9c024a66f6783736387d21a9`；读取上述固定 Git 对象，没有切换或更新 checkout，也没有使用其定制行为。

`download/fiber/current/fnn --version` 实际输出 `fnn Fiber v0.10.0-rc1 (7aa1d5d 2026-11-09)`；`download/fiber/0.9.1/fnn --version` 输出 `fnn Fiber v0.9.1 (9a561b3 2026-09-10)`。默认链快照为 `source/fiber/data.2026.0929.tar.gz`；合约目录 README 标注 `701d8c8a08790dd61c64f695aaa5fbed22e4b8ad`，与运行链实际部署的对应关系本轮未验证。自动化前须固定构建、通过真实迁移和探针、记录链上 commitment-lock code/data hash；不得用最新 current 的结果替代固定 PR 版本。

结构检查命令：`python3 /Users/guopenglin/.codex/skills/ai-test-agent-skill/scripts/check_test_design.py --root /Users/guopenglin/PycharmProjects/gp5/fiber-py-integration-test --review reviews/pr1711-reestablish-timeout.md` → `cases: 7`，退出 0；SPEC-04 提示预期需人工确认，已在前文列明。这只检查文档结构，不证明运行行为。

编辑前后均运行 `python3 scripts/check_test_map.py`，退出 1：既有孤儿 `PMP-01, PMP-02, PMP-03, PMP-04, PMP-05, PR1677-09, SETTLE-01`，无重复评审 ID；用例数由 78 增至 85，已映射仍为 72。本批 0/7 映射，符合设计阶段；未跨范围修改原有问题。

独立 B 复核：只读子代理 `/root/pr1711_design_review`，`fork_turns=none`，读取原始 PR 元数据/三文件 diff、固定源码和本评审，无继承主代理对话；不声称主机级隔离认证。结论无阻断项；两项 P2 已处理：上游单测仅称代表性条件反例，`05` 增加合作关闭等待与 funding live 的外部前提。B 核对了旧通道创建版本、空库对端、直接回滚/CLI 恢复差异和 300+60 秒期限；节点、资金和总耗时均未实测。

可审阅的 [文档补丁](/private/tmp/pr1711-test-analysis/changes.patch)、[验证回执](/private/tmp/pr1711-test-analysis/reports/document-checks/execution.json)、[原始验证输出](/private/tmp/pr1711-test-analysis/reports/document-checks/execution.log)、[回滚脚本](/private/tmp/pr1711-test-analysis/rollback.py) 随本批保留。验证在隔离副本执行文档添加、结构检查和回滚；回滚只删除内容仍与本批一致的新文档，不触及既有文件。这不是节点测试结果。原始材料为同目录的 `pr.json`、`product.diff`。

## 反复重连补例核验

本轮仅新增 `PR1711-08`、补充 SPEC-05，并修正 SPEC-02 的证明边界；原 01～07 七行逐字保留。用户纠正已追加到 `reviews/review-feedback.md`，未修改自动化或 TEST-MAP。

独立 B 增量复核沿用只读会话 `/root/pr1711_design_review`，重新检查固定源码与新增设计。指出的 P1“总期限是否含离线、到期时如何判广播”和 P2“提前退出与至少三轮冲突”均已修订，并经有界复核确认解决。复核时累计在线 T、离线暂停但不清零仍为候选规则；随后用户已确认规则并选择 T=300 秒，当前用例据此校准观察窗口。该复核没有执行节点，也没有把有限观察当作永久锁死的证明。

结构检查同上命令输出 `cases: 8`，退出 0，列出 `PR1711-08` 的待确认预期及 SPEC-04/05 的推论。`python3 scripts/check_test_map.py` 退出 1，仍为原有七个孤儿映射，无重复评审 ID；当前本评审 0/8 映射。全仓其他任务的映射变动不归入本次新增用例。

本轮补丁、隔离副本验证与回滚位于 [/private/tmp/pr1711-test-analysis/reconnect-case](/private/tmp/pr1711-test-analysis/reconnect-case)：[changes.patch](/private/tmp/pr1711-test-analysis/reconnect-case/changes.patch)、[execution.json](/private/tmp/pr1711-test-analysis/reconnect-case/reports/document-checks/execution.json)、[rollback.py](/private/tmp/pr1711-test-analysis/reconnect-case/rollback.py)。此回滚恢复补例前的两份文档，保留之前已有的七条用例与反馈；它与初版“删除新文档”的回滚用途不同。


## PR1711-08 自动化与实测

用户确认后仅实现 `PR1711-08`，原 `01`～`07` 七行保持原文且未自动化。本评审映射为 **1/8**；复选框表示存在映射，本次节点结果为失败。

当前代码：[test_reestablish_timeout.py](../test_cases/fiber/devnet/connect_peer/test_reestablish_timeout.py)。以下是重命名前的历史执行记录：当时测试默认升级至 `current`，使用 `PR1711_FNN_BIN=.build/pr1711/fnn` 指向独立构建的固定 squash 提交 `ab6364f1169bde596a7342f08767e79a4750a8ca`。未修改产品源码、恢复期限或 P2P 消息，使用独立端口 27114/27115/27127～27130 和测试目录 `tmp-pr1711`。

构建命令（在 `.build/pr1711-source`）：`cargo build --release --locked --offline --bin fnn --target-dir ../pr1705-target`，输出 `Finished release profile [optimized] target(s) in 1m 54s`，退出 0。固定二进制输出 `fnn Fiber v0.10.0-rc1 (ab6364f 2026-11-06)`，SHA-256 为 `a4a0a1f45e973635606dbb8cd0777b289a2bf0f074cdfed160bc7b082a916a29`；源工作树无变更。旧节点输出 `fnn Fiber v0.9.1 (9a561b3 2026-09-10)`，SHA-256 为 `8db5416d9a23b6df6799f4d716c78400913358510315ca2d468466df17899984`。实际链上 commitment-lock code cell 为 `0x157446f77b9468f63ae741dc66e6e987b470eb2e5f66fdde62d7c90f96042202:0`，数据 hash 为 `0x91c057ff3bf85e43ccd4baad81b9d675d1e982b85d4343e1741ac08b99e85c93`。

聚焦执行入口：

```bash
PR1711_FNN_BIN=.build/pr1711/fnn venv/bin/python -m pytest   test_cases/fiber/devnet/connect_peer/test_pr1711_reestablish_timeout.py::TestReestablishTimeout::test_inbound_settlement_exits_despite_repeated_reconnects   -q -s -o log_cli_level=WARNING
```

本次通过 skill 的 `run_with_evidence.py` 包装执行，并生成原生 JUnit；完整实际 argv、输入哈希、源提交、原始输出和退出状态见 [execution.json](../reports/pr1711-08-exact/execution.json) 与 [execution.log](../reports/pr1711-08-exact/execution.log)。最初沙箱端口绑定报 `PermissionError`；获准启动本地节点后完成正式执行，以下结果来自该次实际执行。

- **前置条件成立**：官方 v0.9.1 建通道并双向付款；双方正常停机升级，原 channel_id、funding、余额及最新承诺 hash 保持一致，升级后双向探针成功。B→A 唯一持单稳定 60 秒，实际 TLC 到期时间扣除正式构建提前移除裕量后仍覆盖 28 分钟；A 没有出站在途 TLC。B 停机移走 store 后保留原身份，三轮均由双方 `list_peers` 确认连接，B 的通道列表为空。A 第一次重连后只提交一次原像，RPC 返回 `{}`。
- **实测失败**：三个确认在线窗口分别为 **150.0207、149.9889、90.0131 秒**，两次停机间各保持至少 10 秒已观察到的离线；累计 **390.0228 秒**。最后查询中原 funding 仍 `live`，没有交易池或已确认交易消费它，通道仍 `ChannelReady`、唯一入站 TLC 仍 `Committed`、发票仍 `Received`，A 钱包净增 **0**。A 故障前通道本金为 **402 CKB**，另有 **2.00000017 CKB** 应收款；均未通过本次自动退出回收。
- **原始判定**：`AssertionError: PR1711-08: 累计在线 390.02s 后仍未自动广播强关`；pytest 输出 `1 failed in 533.74s (0:08:53)`，退出 **1**；JUnit 为 1 failed、0 errors、0 skipped。回执 `inputs_unchanged: true`。失败保留为普通断言，没有标记 skip/xfail，也没有用后续长连接、恢复 B 旧库或人工强关改判。
- **证明边界**：已证明固定 PR 版本未满足用户确认的累计在线期限。尚未进入自动广播之后的 660 秒资金核对分支，该分支本次没有运行验证；有限观察也不证明资金永久不可取回。测试结束正常停止并清理本次节点目录，连接时序与完整节点日志保存在 [运行证据目录](../reports/pr1711-08-kbno5o2i/)。

收集检查输出 `1 test collected in 0.20s`，退出 0，仅证明发现规则有效。结构检查输出 `cases: 8`，退出 0；SPEC-04 的既有确认提示对应尚未进入自动化的其他用例。项目级 `python3 scripts/check_test_map.py` 输出 `review cases: 86`、`automated cases: 75`，退出 1，仍为原有七个孤儿映射 `PMP-01`～`PMP-05`、`PR1677-09`、`SETTLE-01`，无重复评审 ID；本次仅新增 `PR1711-08` 映射。该分类已在 Makefile 和既有 connect_peer CI 目录范围内。

本轮的 [补丁](/private/tmp/pr1711-test-analysis/automation/changes.patch)、[补丁及回滚验证回执](/private/tmp/pr1711-test-analysis/automation/reports/artifact-checks/execution.json)、[回滚脚本](/private/tmp/pr1711-test-analysis/automation/rollback.py) 保留了修改前评审及当前测试副本。回滚只在两份文件仍与本次输出一致时恢复评审并删除新测试；不碰其他工作区改动。


## 后续自动化范围（本批）

用户要求“08用例先skip 补充其他用例”，并确认“只用 v0.9.1，04 加 V2 对照”。现有七条行为的预期保留，旧版变体按该决定收窄；`08` 复用公共准备、观察及资金核对方法并标记 skip，保留累计等待要求和历史失败结论。

`PR1711-01/05` 各有 CKB 和 UDT 两个方法，`04` 有 Legacy 与新建 V2 两个方法，`02/03/06/07` 各一个方法，合计十个执行变体；加上 skip 的 `08`，原生 pytest 收集为十一条。八个 ID 均有映射，skip 不计为行为验证通过。

公共辅助仅复用通道准备、持单、启停、连接观察和资金核对。回归代码不管理 evidence 文件或 signal；运行记录由外部命令保存。自动强关仍使用正式 300 秒期限；等待上链领取时通过公开 devnet `generate_epochs` 生成真实区块以满足相对锁定期，不修改系统时钟或节点超时。`05` 的 Paid 与 TLC 终态检查保留在资金回收的 660 秒总期限内。

### 本批实测与验证

本批分组运行，统一使用 `PR1711_FNN_BIN=.build/pr1711/fnn` 固定到 `ab6364f1169bde596a7342f08767e79a4750a8ca`，沿用上文记录的正式二进制和 v0.9.1 身份。各组使用独立端口和目录；完整 argv、输入哈希、原始输出、原生 JUnit 和退出码保存在对应回执中。初跑与修正后的回执分别保留。

最终结果：`PR1711-01`～`PR1711-07` 的 **10 个变体均已分批通过**，`PR1711-08` 按要求 skip。历史重跑的失败和重复 skip 不累加为最终结果。所有回执均为 `inputs_unchanged: true`；早期通过的测试源码副本保存在 [first-pass-test.py](/private/tmp/pr1711-test-analysis/remaining-cases/first-pass-test.py)，后续复核覆盖了准备清理、钱包查询和 CLI 恢复调整。

| 执行组 | 原生结果 | 退出码 | 说明与回执 |
| --- | --- | --- | --- |
| `01` CKB | `1 passed in 425.74s` | 0 | 自动强关、实际本金及健康通道均通过；[回执](../reports/pr1711-01-ckb/execution.json) |
| 资金组初跑 | `2 failed, 1 passed` | 1 | `05` CKB 通过；两条 UDT 已核对链上领取交易，但钱包净增检查超时；[回执](../reports/pr1711-funds/execution.json) |
| `04` Legacy / V2 | `2 passed` | 0 | 离线、原目录恢复、持单结算和后续完整观察均通过；[回执](../reports/pr1711-healthy/execution.json) |
| 备份组初跑 | `1 failed, 1 passed, 1 skipped` | 1 | `06` 通过；`07` CLI 覆盖既有只读密钥失败；`08` skip；[回执](../reports/pr1711-backups/execution.json) |
| 重启组初跑及首次重跑 | 各 `2 errors` | 各 1 | 初跑为准备阶段共享临时文件冲突；首次重跑被残留测试进程占用端口阻止，均未执行行为断言；[初跑](../reports/pr1711-restarts/execution.json)、[首次重跑](../reports/pr1711-restarts-retry/execution.json) |
| `01/05` UDT 复核 | `2 passed in 938.04s` | 0 | 本金、持单款、CKB 费用及钱包实际净增均正确，`05` 发票和 TLC 终态也通过；[回执](../reports/pr1711-udt-final/execution.json) |
| `07` 恢复及 `08` skip 复核 | `1 passed, 1 skipped in 456.49s` | 0 | 正式 CLI 恢复身份及 Stale 状态，空库对端重连后完整观察通过；[回执](../reports/pr1711-restore-final/execution.json) |
| `02/03` 重启组复核 | `2 passed in 1527.24s` | 0 | 本端重启及对端离线后重新计时均自动关闭并回收本金；[回执](../reports/pr1711-restarts-final/execution.json) |

已修正测试准备和观察方式：各组启动错开，避免框架共用 `/tmp/tmp.data`、`/tmp/demo.json`；准备失败时停止并清理本用例节点；UDT 钱包查询使用 A 的实际锁参数；`07` 正常停机后保留现有只读身份密钥，让 CLI 向空目标路径恢复备份。最后一项避免覆盖既有只读文件，本例验证的是恢复后的待核对保护；直接覆盖只读密钥的 CLI 行为不在本批修复。产品源码、正式超时及用例预期均保持不变。

已完成的正向证据包括：`01` CKB 在 333.79 秒、UDT 在 331.08 秒自动强关；`05` CKB 在 352.98 秒、UDT 在 351.61 秒自动强关，持单和本金分别领取并按实际费用核对；`03` 本端重启后 361.49 秒自动强关并到账。`04` 的两个版本与 `06/07` 均完成各自的完整负向观察。`02` 先观察在线 150 秒和离线 390 秒均不关闭，第二次连接后 349.33 秒自动强关，本金扣 784 Shannon 结算费后到账。

收集检查：`venv/bin/python -m pytest test_cases/fiber/devnet/connect_peer/test_pr1711_reestablish_timeout.py --collect-only -q` → `11 tests collected in 0.18s`，退出 0。结构检查命令同上 → `cases: 8`，退出 0，仍保留 SPEC-04 的推论提示。最终 `python3 scripts/check_test_map.py` → `review cases: 94`、`automated cases: 82`，退出 1；孤儿仍为原先 `PMP-01`～`PMP-05`、`PR1677-09`、`SETTLE-01`，无重复 ID。本批为 **8/8 映射、10 个执行变体通过、08 skip**；全仓新增的其他评审不计入本批范围。所有本批测试目录及监听端口均已清理。

本批交付：[修改后文件副本](/private/tmp/pr1711-test-analysis/remaining-cases/modified)、[补丁](/private/tmp/pr1711-test-analysis/remaining-cases/changes.patch)、[补丁与回滚验证回执](/private/tmp/pr1711-test-analysis/remaining-cases/reports/artifact-checks/execution.json)、[回滚脚本](/private/tmp/pr1711-test-analysis/remaining-cases/rollback.py)。隔离副本验证从原 1/8 映射应用补丁到 8/8、11 个测试方法且仅 08 skip，并恢复三份文件；反馈的回滚基线保留其他任务同期追加的内容。回滚须显式传入 `--root`，且仅在三份文件仍与本批输出一致时执行。

## 长期回归入口

按用户要求，测试改名为 `test_reestablish_timeout.py`，升级目标直接使用 `FiberConfigPath.CURRENT_DEV`，旧通道继续由仓库 v0.9.1 创建。移除 PR 专用二进制和端口环境变量，端口继承 `FiberTest`，数据目录使用 `tmp-reestablish-timeout`。文件说明、skip 原因和失败信息按行为表述；既有 `TEST-MAP` ID 保持稳定。

按后续反馈，密钥保留、正式 CLI 恢复和节点停启集中到 `FiberTest.restore_fiber_from_backup`，用例只调用该方法并核对 Stale 状态、身份及重连后的保护行为。删除版本包装、`--version` 查询、版本打印和旧提交号断言；测试文件不再导入 `os` 或 `subprocess`。

当前运行入口：`venv/bin/python -m pytest test_cases/fiber/devnet/connect_peer/test_reestablish_timeout.py -q -s`。上文固定提交、旧文件名、环境变量和端口均为历史实测记录，不作为当前运行配置；原有 10 个通过结果属于该次固定构建执行。

本次维护验证：新入口收集到 11 条，单独执行已跳过的反复重连用例输出 `1 skipped`，均退出 0。封装后使用仓库 `current`（`7aa1d5de724fb28b8ddfc663f5da792ff48f1ee9`）执行 `venv/bin/python -u -m pytest test_cases/fiber/devnet/connect_peer/test_reestablish_timeout.py::TestReestablishTimeout::test_cli_restored_stale_channel_waits_without_force_close -q -s -o log_cli_level=WARNING`（运行时另传原生 JUnit 路径），输出 `1 passed in 458.68s`，退出 0；完整命令、二进制哈希和结果见 [聚焦回执](../reports/reestablish-restore-current/execution.json)。8 条评审与 11 处映射保持一致；结构检查退出 0，全仓映射检查仍报原有七个孤儿。维护的 [补丁](/private/tmp/reestablish-regression-cleanup-20261110/changes.patch)、[补丁及回滚验证回执](/private/tmp/reestablish-regression-cleanup-20261110/reports/artifact-checks/execution.json)、[回滚脚本](/private/tmp/reestablish-regression-cleanup-20261110/rollback.py) 单独保存。
