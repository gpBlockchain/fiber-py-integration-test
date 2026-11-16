# V2 恢复超时与入站款项链上领取测试分析

## 导读

- **目的**：对端反复重连也应结束长期无进展的恢复；收款方已交出正确原像时，即使对端一直离线，也应在到期前把钱转到链上领取。
- **行为变化**：以前每次重连重新给五分钟，离线时已请求结算的入站款项还可能一直排队；现在 V2 累计在线恢复时间，离线暂停，成功恢复后清零，并另设与连接无关的入站款项保护期限。本地节点重启仍重新计时，V1/Legacy 保持原规则。
- **主要风险**：只关闭了通道却没拿到钱；只救回最早到期的一笔，漏掉其他待收款；中继已付给下游却收不到上游款；离线或成功恢复后误关正常通道。
- **本仓库补充**：真实 release 节点、公开 RPC、停启/保留身份丢库/恢复完整目录，以及真实 CKB devnet 上的资金结算。本次覆盖新建 V2 的 CKB、UDT；已有 V2 升级路径保留为候选。
- **不重复的层次**：上游已检查计时数学、完整哈希匹配、精确 TLC 绑定、通知去重、同一轮处理中的关闭竞争及状态排除。这里不修改数据库字段、拦截指定消息或缩短二进制中的超时。上游模拟链与合成结算凭据不等于真实链上到账。

依据：[PR #1733](https://github.com/nervosnetwork/fiber/pull/1733)。设计阶段之后，用户已确认先执行下列收敛范围；运行结果见文末。

### 分析版本与影响

- GitHub 核对时间：2026-11-10。base tip 与 merge-base 均为 `7aa1d5de724fb28b8ddfc663f5da792ff48f1ee9`；head 为 `b54235ac6a3fb1a3a48fd555b6cb86822600ad35`，共四个提交、八个文件。
- `channel.rs`：运行期恢复计时、入站 claim 的到期保护、关闭时补发原像通知；`channel_v2.rs`：恢复计时任务及成功恢复后的清零；其余改动为测试夹具、测试用消息匹配、运行期字段样本更新。无新增数据库持久字段或迁移格式。
- 本地测试仓库 HEAD：`004e5132bbc00e0b1c0c150fd1a6c06b96395a87`，已有其他任务的未提交改动。`fiber/` 是另一个带修改的 checkout（`4319344528ded25e9c024a66f6783736387d21a9`），本分析使用 GitHub 上的固定 head 源码，未将该 checkout 当成 PR 目标。
- 设计阶段实测 `download/fiber/current/fnn --version`：`fnn Fiber v0.10.0-rc1 (7aa1d5d 2026-11-09)`，是本 PR 基准；`download/fiber/0.9.1/fnn --version`：`fnn Fiber v0.9.1 (9a561b3 2026-09-10)`。本次执行使用用户指定的 `download/fiber/received-tlc-claim-protection/fnn`：`fnn Fiber v0.10.0-rc1 (b54235a 2026-11-10)`，SHA-256 `983cb4bb32ec85282c2a5c439f9a95721c646dc23a4bf617b84735262ba67837`；未覆盖 current 或 fix-tp-mpp。
- 当前框架使用 `source/fiber/dev_config_3.yml.j2` 与 devnet 快照：FundingLock type hash `0xe7576045a47bb4cc172ffd0306ce01e232dc43805becb356e58f8bda9df51e26`，CommitmentLock type hash `0xf3775d5328de71717f2c5614fa06b9b93c48b7d90c1e135c0812c74ee3126453`。type hash 标识部署，不固定可升级合约的代码版本；执行前需读链上 dep cell 的实际代码并核对部署/升级记录，不能仅凭旧 README 推定合约版本。

## 本次执行范围

用户对照 #1711 后已将首批收敛为三个行为、四个执行变体：`PR1733-01` 仅 CKB，`PR1733-04` 新建 V2 的 CKB / UDT 重启，`PR1733-07` 仅 CKB 中继。`02/03/05/06/08` 以及旧 V2 升级、额外资产和断连组合保留为候选，未在本次执行。表中复选框仍仅表示映射存在，不表示整行所有候选变体已经执行。

**耗时纠正与用户确认**：首轮运行发现 release 路由会统一追加随机隐私延期，最少 4 小时；原表中持单用例的自然等待预算因此失效。当前付款将 `tlc_expiry_limit` 收紧到路由基础期限 + 4 小时 + 1 秒，使随机项固定为一档，并校验 RPC 实际 expiry。用户已明确允许使用现有系统时间封装加速：跳到实际保护窗口前至少两分钟，再真实等待触发与链上回收；不修改二进制或数据库，不人工强关。累计重连仍按单调钟真实等待。测试结束调用 `restore_time()` 并验证恢复误差。

`PR1711-08` 仍为已标记跳过的 Legacy 用例；本 PR 明确只改 V2，不把它取消跳过，也不把其旧预期改写成已修复。

## 用例

### 公共前提与等待窗口

- 默认 A 为收款/受保护节点，B 为付款或丢库对端；路由用例明确改用 A→B→C。用实际未修改的 release 构建，启用内置 Watchtower，所有通道以 `public=true` 创建；每次先验证通道正常双向付款，记录双方账本、funding outpoint 和钱包余额，再施加故障。
- **V2 丢库重连**：正常停止 B，保存完整原目录，将 B 的通道数据库移到备份路径，保留密钥、配置和身份，空库启动。新查询须确认身份未变、B 无旧通道、A 保留原通道且双方 peer 在线。该构造利用空库对端没有旧通道可恢复，不依赖测试钩子。A 连续运行；若该具体版本自行断开或恢复旧通道，按前置条件失败处理，不能把启动等待计成已连接时间。
- **恢复时间**：release 的 `REESTABLISH_TIMEOUT=300s`，维护间隔 `I=60s`。累计在线无进展超过 300 秒才应触发；以 390 秒作广播观察上限（含维护与 RPC 裕量）。通过新鲜的 `list_peers` 查询划分在线区间，使用单调钟累计并记录轮询误差；不是从 `start()` 开始计时。离线暂停。每个短连接都低于 300 秒；不靠最后追加长连接使旧版也通过。
- **款项期限**：固定承诺延迟为 1 epoch；release 按 4 小时/epoch 换算，保护量 `S=2/3 × 4h=9,600,000ms`。读取 RPC 返回的每笔实际 TLC expiry `E`，其保护触发条件为 `now > E-S-I`。广播最迟观察到 `E-S+30s`，早于 `E-S-I` 的广播视为提前触发（排除其他关闭原因）。这些常量不是可配置 RPC 超时。
- **把长 expiry 变成可重复的短观察**：经 `new_invoice.final_expiry_delta` 选取 `S+10min`（第二笔 `S+15min`），未计隐私延期时约在付款后 9～10 分钟触发；release 实际还追加至少 4 小时，原估计已被实测纠正。本次以返回的实际 `E` 校准，按用户确认使用系统时间封装加速。发票有效期、付款 timeout 覆盖场景，测试须在发票仍为 Received 时交出原像。多跳增加上游路由 delta 时，按上游实际 `E` 重新计算，见 `07`。
- **链上回收**：使用 devnet 真实自动出块、正常合约和 Watchtower。广播与到账分别检查；广播后另给 660 秒回收观察窗，执行前验证该 devnet 的 epoch 推进足以满足锁定期。行中耗时是单个变体的预算，不含编译；环境不满足时报告环境限制，不能缩短合约时间或伪造回执。
- CKB、UDT 共用步骤但独立执行和核账；UDT 数量与 CKB 手续费/预留容量分开核算。`04` 的两笔不同 hash 分别使用 `sha256`、`ckb_hash`，同一笔只提交一次原像。`[ ]` 表示尚无 TEST-MAP，不表示运行失败。

<!-- TEST-CASES-BEGIN -->
| 用例 | 场景 | 预期结果 | 防止的问题 | 优先级 |
| --- | --- | --- | --- | --- |
| `PR1733-01` | - [x] 新建无在途款的 V2 CKB A–B；B 保留身份丢库，以 140＋140＋110 秒在线窗口反复重连，首轮后离线 390 秒，另一健康 A–C 持续付款；依次使用 B 正常停启和 RPC 断连（约 13～20 分钟，上限 30 分钟） | 首段离线及累计在线未满 300 秒时 funding 未消费，累计在线 390 秒内自动广播强关；A 本金扣实际费用后到账，A–C 付款与账本正常 | 每次重连重置期限、把离线算入期限，或故障通道拖垮其他通道 | P0 |
| `PR1733-02` | - [ ] 无在途款的 V2 A–B 中，B 丢库重连在线 150 秒后正常停止 A；A 从原目录重启，再与空库 B 持续连接（17～23 分钟，上限 27 分钟） | A 保留通道与余额；重启后的新在线窗口未满 300 秒不强关，390 秒内自动强关并收回本金，无重复结算 | 本端重启后提前关通道，或恢复超时任务丢失导致永久等待 | P1 |
| `PR1733-03` | - [ ] V2 的 B 丢库重连等待 150 秒后，停 B 并恢复故障前完整目录，双向付款成功并观察 390 秒；再让 B 保留身份丢库，持续在线（24～31 分钟，上限 35 分钟） | 成功恢复后通道不被遗留任务关闭，付款与余额正确；第二次故障重新获得完整 300 秒，390 秒内自动强关并回收余额 | 恢复成功却遗留累计时间，误关已恢复或后来再次恢复中的通道 | P1 |
| `PR1733-04` | - [x] V2 B→A 两笔不同哈希、先后到期的持单均为 Received 后停 B；A 各提交一次原像并正常停启，从原目录恢复，B 一直离线；只跑 head 新建通道，资产为 CKB、UDT；按实际 expiry 调时到最早保护窗口前至少两分钟，再真实等待（每变体执行预算 30 分钟） | 最早款项进入保护窗口后按期限自动强关，Watchtower 实际领回两笔应收款及 A 本金，金额各记一次，发票最终 Paid，无遗留待领取资金 | 离线款项排队到超时、重启/升级丢掉领取能力，或只救回最早一笔 | P0 |
| `PR1733-05` | - [ ] V2 B→A 持单已 Received，B 保留身份丢库；A 提交原像，B 以小于 150 秒的窗口重连；选择实际 TLC 期限使 claim 广播上限早于累计恢复 300 秒，A 连续运行（13～21 分钟，上限 25 分钟） | 即使每轮连接都很短，也按 TLC 的保护期限自动强关并实际取得本金和应收款；不等累计恢复超时，不人工强关 | 仅修复五分钟计时，却让更早到期的应收款错过领取窗口 | P0 |
| `PR1733-06` | - [ ] V2 B→A 持单为 Received，A 请求合作关闭并确认仍在等待该款、funding 未消费；随后停 B，A 提交原像，B 保持离线（18～25 分钟，上限 30 分钟） | 等待合作关闭也在该款保护窗口内自动转为强关，A 本金和应收款实际到账一次，发票最终 Paid | 已进入合作关闭的通道漏掉入站款项保护，关闭状态长期卡住 | P1 |
| `PR1733-07` | - [x] V2 A→B→C 逐跳 delta 经 RPC 设为 160 分钟，路由持单在 C Received 后停 A，C 提交原像；确认 C Paid、B–C 已结算，再正常重启 B，A 一直离线；仅跑 CKB，按上游实际 expiry 调时到保护窗口前至少两分钟，再真实等待（执行预算 30 分钟） | B 在上游款项保护窗口内自动强关 A–B 并真实领回应收金额及路由费；B–C 继续付款，B 的两侧合计变化仅为应得费用扣链上费用，不重复支付 C | 中继付了下游却收不到上游款，或合成结算回执掩盖真实链上亏损 | P0 |
| `PR1733-08` | - [ ] V2 B→A 持单为 Received，原像只留在测试端，停 B 并保持未提交结算；持续观察至该款保护窗口上沿之后 90 秒，B 始终离线（11～15 分钟，上限 18 分钟） | 该观察窗内 A 不因未授权入站款主动强关，funding 保持未消费，A 余额不增加，节点 RPC 与另一健康通道付款正常 | 把未请求结算的普通持单当成已授权领取，提前关闭正常通道或凭空收款 | P1 |
<!-- TEST-CASES-END -->

## 测试树

<!-- TEST-TREE-BEGIN -->
- V2 恢复等待有界，且真实资金可退出
  - 同一 A 进程中多次短连接，含长离线与健康通道隔离：PR1733-01 [primary] -> SPEC-01, SPEC-04
  - 本端正常重启重新计时：PR1733-02 [primary] -> SPEC-01, SPEC-04
  - 完成恢复后清零，下一次故障从头计时：PR1733-03 [primary] -> SPEC-01, SPEC-04
- 已请求结算的 V2 入站款项保护
  - 对端一直离线，多笔款项，收款端正常重启：PR1733-04 [primary] -> SPEC-02, SPEC-04
  - 对端反复重连，款项保护先于恢复超时：PR1733-05 [primary] -> SPEC-02, SPEC-04
  - 合作关闭仍在等待持单：PR1733-06 [primary] -> SPEC-02, SPEC-04
  - 路由下游已收款，上游离线，中继重启后链上领取：PR1733-07 [primary] -> SPEC-02, SPEC-04
  - 未提交原像的普通持单不得提前关通道：PR1733-08 [primary] -> SPEC-03, SPEC-04
- 本轮不新增的层次
  - 计时精确毫秒边界、饱和运算、重复事件、完整哈希/精确 TLC 匹配、同 hash 通知去重、已确认/失败移除/隔离状态筛选：[not_applicable] — 上游进程内测试已有覆盖，按项目偏好不复制。
  - 本地原像库字段缺失、特定消息乱序、单轮关闭竞争：[not_applicable] — 需要内部改库或精确消息钩子，超出本项目用例偏好；正常重启仅证明外部领取结果。
  - funding 确认与缺失恢复握手交叉的候选漏检：[not_applicable] — 特定先后关系尚无稳定公开接口构造；保留源码风险，恢复点见下文。
  - 外部钱包等待时间进入恢复计时：[not_applicable] — 已定位运行期计时残留，但未签名阶段会拒绝强关命令，评审评论声称的立即终止尚未建立；不生成证明不了问题的测试。
  - V1/Legacy 不变规则：[not_applicable] — 本轮复用既有 PR1711-02/03/04，不新增行；PR1711-08 的 Legacy 累计期限仍未解决。
<!-- TEST-TREE-END -->

## Spec

### SPEC-01：V2 按在线无进展时长累计恢复预算
- Condition: V2 通道的本地进程持续运行，对端连接但未完成验证恢复。
- Expected: 跨重连累计超过 300 秒触发有界关闭；离线暂停；成功恢复清零；本地 actor/进程恢复重新计时；V1 沿用每连接窗口。
- Observable: peer 在线区间、funding 消费、自动关闭交易、恢复后的新付款；不读取私有计时字段。
- Basis: PR 描述的累计等待、暂停、成功恢复清零和本地重启约束；固定 head 的 [计时与检查](https://github.com/nervosnetwork/fiber/blob/b54235ac6a3fb1a3a48fd555b6cb86822600ad35/crates/fiber-lib/src/fiber/channel.rs#L6153) 及 [V2 恢复](https://github.com/nervosnetwork/fiber/blob/b54235ac6a3fb1a3a48fd555b6cb86822600ad35/crates/fiber-lib/src/fiber/channel_v2.rs#L1326)。
- Source: explicit
- Testing: required

### SPEC-02：已授权且未确认的入站 claim 在到期前上链，保护同通道全部待收款
- Condition: V2 通道可强关，本笔已承诺入站 TLC 已请求使用正确原像结算，尚未确认；涵盖普通收款与已结算下游的转发。
- Expected: 到 `E-S-I` 之后触发维护时自动强关，与在线/离线/恢复状态无关；同通道较晚到期的其他授权款也保留领取能力；等待合作关闭仍受保护。
- Observable: 实际 expiry、关闭交易、各笔领取交易、钱包资产与路由两侧账本。
- Basis: PR 描述的 claim fallback、全部 pending claims 原像补发；[固定 head 的领取保护](https://github.com/nervosnetwork/fiber/blob/b54235ac6a3fb1a3a48fd555b6cb86822600ad35/crates/fiber-lib/src/fiber/channel.rs#L3896)。
- Source: explicit
- Testing: required

### SPEC-03：普通持单和未取得本笔结算授权的记录不触发该领取保护
- Condition: 入站持单尚未提交正确原像，或不满足本笔已授权未确认 claim 的条件。
- Expected: 不仅凭快到期或其他款项的原像启动该领取保护；本地数据落后、已广播关闭等排除条件保持有效。
- Observable: 对端离线时无提前广播、余额不增，节点和健康通道仍可用。
- Basis: PR 的 exact TLC/full hash 校验要求；[固定 head 的 claim 资格判断](https://github.com/nervosnetwork/fiber/blob/b54235ac6a3fb1a3a48fd555b6cb86822600ad35/crates/fiber-lib/src/fiber/channel.rs#L7315) 及上游相应负例。
- Source: explicit
- Testing: required

### SPEC-04：关闭和重启之后以真实资金守恒证明结果
- Condition: 真实 CKB devnet、正常合约、Watchtower、固定账本和付款集合。
- Expected: 收款端取得原本金加授权应收款，扣除实际承担的链上费用；中继已付的下游款由上游领取补回，只保留应得路由费；所有金额只变动一次，健康通道仍能付款。
- Observable: 链上输入输出和领取目的锁、UDT 数量、钱包与未关闭通道余额、发票/付款终态。
- Basis: PR 的“protect received TLC claims”目标，以及本仓库 AGENTS.md 要求的真实链上资金观察；金额通过测试预设付款与独立资产守恒式计算，不采用被测节点给出的“应收总额”。
- Source: explicit
- Testing: required

## 验证设计

### 触发隔离与可重复性

- `01`～`03` 不挂任何 TLC，排除到期保护和 ACK 超时冒充累计恢复超时。`01` 的长离线放在累计未过 300 秒时，B 停启仅影响 B；RPC 断连变体先完成一次丢库，再用公开断连/连接切换。健康通道探针不能改变故障通道的状态。
- `04`、`06`、`07` 让故障对端持续停机，排除恢复计时；本地不得有其他临近到期的发出 TLC。提交原像成功后始终保留原数据库，重启后不再次提交原像。等待窗口内即使 RPC 暂时显示 Paid，也继续核查真实领取。
- `05` 在准备时按实际 `E` 校准：从首次空库连接到 `E-S+30s` 保留 120～240 秒观察窗，少于 300 秒；在保护窗口前做一次短断连，之后继续短连接。发票 Received 和提交原像必须在该窗口前完成，若搭建耗尽余量则场景前提失败，不能改期限或将其他超时的关闭算通过。
- `07` 正常 release 默认逐跳 delta 是 4 小时，最小值才是 160 分钟。需在付款前对 A–B、B–C 各方向通过 `update_channel(channel_id, tlc_expiry_delta=hex(9_600_000))` 显式设置，读回 `list_channels.tlc_expiry_delta` 并等待路由更新生效。两跳中，上游实际 expiry 应比下游增加约 160 分钟；若下游 delta 取 `S+10min`，计入最少 4 小时的路由隐私延期，上游 claim 保护约在付款后 409～410 分钟触发；本次用系统调时跳过窗口前的空等。用上下游 RPC 中实际 `E` 差值校验前提；若逐跳 delta 仍采用默认 4h，再叠加最少 4h 隐私延期，需约 489～490 分钟才触发；都应按实际 E 调时，不套用旧的自然等待预算。发票有效期、支付 session timeout 和测试超时均设为覆盖该长场景。B 重启后先确认 B–C 仍能新付款，再等待 A–B 回收。C Paid、B–C 借记发生于重启前，排除尚未付给下游的伪前提。
- `08` 的 B 持续离线，A 没有发出 TLC、原像或关闭请求；在 `E-S+90s` 结束行为观察。此时维护可能已排队 `ExpiryTooSoon` 失败，B 恢复也可能因其发出 TLC 临期而合法强关，所以不承诺原持单恢复后还能成功付款，不把后续清理交易算作 A 误关。健康通道先单独记账，避免探针收入污染故障通道的余额判定。仅证明未授权持单不会误触发，不声称证明所有内部排除分支。

### 源码风险，尚非已复现缺陷

- **外部钱包等待被累计，但“立即终止”未成立**：固定 head 的 `channel_v2.rs:1372` 清除 `reestablishing` 却保留运行中的 timer；后续断连可保存整个等待时长，下次连接检查延时可只剩 1ms。不过 `CheckActiveChannel` 经 `notify_network_actor_shutdown_me` 发送的是 `Shutdown(force=true)`，而 [关闭入口](https://github.com/nervosnetwork/fiber/blob/b54235ac6a3fb1a3a48fd555b6cb86822600ad35/crates/fiber-lib/src/fiber/channel.rs#L2707) 拒绝尚未签名的注资状态，处理错误本身不停止 actor。因此 [PR 评论](https://github.com/nervosnetwork/fiber/pull/1733#discussion_r4524151395) 所称的立即终止尚未建立；只观察原请求存活也证明不了计时正确。暂不新增用例，后续须先追踪签名之后是否有不依赖精确时序的实际用户影响。
- **资金确认后可能漏掉恢复超时**：`on_new_channel_ready` 把连接标成 Online；缺少 `recovery_peer_v2` 时仍保留恢复标记和计时，而新 `reestablish_timed_out` 只接受 Syncing。[评审意见](https://github.com/nervosnetwork/fiber/pull/1733#discussion_r4524151534) 指出可能因此永久等待。路径涉及“已收对端 ChannelReady、重连握手缺失、本地 funding 确认随后到达”的组合；本仓库暂不生成依赖精确时序的用例。后续先检查上游是否补覆盖该交叉路径或修复条件，再评估公开运维是否能稳定构造。

### 独立资金判据

- 关闭前固定 A 的可用余额 `A0`、已授权待收金额集合 `{m_i}` 与费用承担方。CKB 最终应归 A 的资产为 `A0+Σm_i+归属A的预留容量−A承担的实际链上费用`；通过真实 funding→commitment→claim/settlement 消费链定位收款输出，不能只用全钱包总额碰巧相等判通过。
- UDT 独立核对 `A0_udt+Σm_i`，CKB 容量和手续费另算；读取实际输出数据，不把 UDT 当作 Shannon。
- 路由 B：上游领取应收 `m+fee`，下游已付 `m`；合计资产变化应为 `fee−B承担的链上费用`。B–C 新探针的收支单独记账。A 保留数据但离线，恢复 A 后还可检查原付款终态；这项不能替代 B 已实际拿到钱。
- 付款 Success、发票 Paid、通道 Closed、日志写出原像均非独立到账证明。关闭后 RPC 可能保留已终结 TLC 记录，不硬断言 `pending_tlcs == []`；检查无未兑现的本方债权，并由链上输出确认一次性兑现。
- 用固定 base/head 分别运行 `01` 与 `04` 的代表变体，保留相同输入、等待和断言。目标是 base 暴露故障、head 完成行为；本轮尚无该运行结论。`01` 不持单，因此 `04` 的改动不能掩盖计时问题。

### 现有覆盖与复用边界

- `test_cases/fiber/devnet/connect_peer/test_reestablish_timeout.py`：`PR1711-01/02/03/05` 验证旧通道，保留原预期；`04` 已有新建 V2 的正常离线/恢复对照，可原样回归。`08` 明确使用 v0.9.1 Legacy 且被 skip，本 PR 不改变其状态。该文件的丢库备份、peer 查询和链上消费查询可复用，不能把它的 24h TLC expiry 原封不动用在新 claim 用例。
- `test_cases/fiber/devnet/connect_peer/test_channel_v2_recovery_chaos.py` 的 `PR1677-01/03/04` 提供 RPC 断连、重启、多跳收敛模式；正常短故障会成功恢复，不能证明“累计无恢复进展”的新语义。
- `settle_invoice/test_settle_invoice.py` 的正常持单/错误原像/多跳结算与 `watch_tower/test_hash_algorithm.py` 的人工强关领取可复用操作，不直接当成本 PR 的自动触发覆盖。PR 上游离线重启测试直接改 TLC expiry 和原像库；转发测试注入 `OnChainTlcSettlement`，都没有证明本仓库所需真实领取结果。
- 外部注资的既有 `test_external_funding_restart.py` 保留原语义；不把其存活检查当成恢复计时残留的证明。
- 当前 3/8 ID 有映射：`01`、`04`、`07`，对应 `test_cases/fiber/devnet/connect_peer/test_v2_claim_recovery.py` 的四个执行变体（CKB/UDT 用参数化共用完整步骤）。仅覆盖上文明确的首批变体；其余候选未实现。映射不等于执行通过。

### 检查与独立评审

- 结构检查：`python3 /Users/guopenglin/.codex/skills/ai-test-agent-skill/scripts/check_test_design.py --root /Users/guopenglin/PycharmProjects/gp5/fiber-py-integration-test --review reviews/pr1733-v2-recovery-claim-protection.md` → `cases: 8`，退出 **0**。检查仅证明 Spec/树/用例链接结构。
- 映射检查：`python3 scripts/check_test_map.py` → `review cases: 94`、`automated cases: 82`、`duplicate review IDs: none`，退出 **1**。修改前已为退出 1（86 条评审、82 条映射）；前后均有相同七个孤儿映射 `PMP-01`～`PMP-05`、`PR1677-09`、`SETTLE-01`，本轮未扩范围修复。新增八行均未映射，符合评审阶段。
- 独立 B 会话 `pr1733_design_review` 只读审查原始 PR、固定源码、全部设计及既有测试，未继承主会话历史；提出并修正逐跳默认值与预算、未签名注资强关的反证、公开通道前提和计时边界。最终复核结论：**01～08 无剩余设计阻断**；没有真实节点运行结论。
- 上述检查及独立 B 结论来自原设计阶段，不代表后续测试已通过。执行阶段已按用户确认收敛范围；本轮未改旧 Legacy 跳过标记。


### 本次执行记录（2026-11-10）

- `PR1733-01` CKB：`FIBER_TEST_BIN=download/fiber/received-tlc-claim-protection/fnn FIBER_CLAIM_PORT_BASE=28200 venv/bin/python -u -m pytest test_cases/fiber/devnet/connect_peer/test_v2_claim_recovery.py::TestV2ClaimRecovery::test_reconnects_preserve_online_recovery_budget -q -s -o log_cli=false` → `1 passed in 777.89s (0:12:57)`，退出 **0**。原始命令（含 JUnit 路径）及结果在 `reports/pr1733-reconnect-ckb/execution.json`、`execution.log`、`native-results.xml`；该次执行的测试源码副本也保存在同目录。
- 在线三段分别为 `140.47 / 139.95 / 18.37` 秒，合计观察 `298.80` 秒（RPC 确认起点有秒级差异）；首段后的 `390` 秒离线无强关，第三段自动关闭。收回本金及预留容量 `50,100,000,000` Shannon，实际结算费 `785`，钱包净增 `50,099,999,215` Shannon；健康通道余额与双向付款均通过。
- 自动关闭交易 `0x1373171960c623c27a8d9ed347d50b07b33e7ba0587af6bf8409dde072f64a54`；兑现交易 `0x0bcda93c62fa0aecc25635baa3f0aae62de4d998b455bea18526816284a0165e`。devnet CommitmentLock 实际代码 SHA-256 为 `ebf8614c1c7970ba9566820e07dd4c3402ea1ce57f3c08e6f2142f5b448954a8`。
- `PR1733-04/07`：首次未限制随机延期，实际观察到约 20 / 22 小时后的保护窗，已主动停止，退出 **2**，不计行为通过。记录保留在 `reports/pr1733-initial-expiry-check/`。修正路由预算并加入用户允许的系统调时后，再次启动因另一条测试占用 `281xx` 端口而在 setup 失败：`3 errors`，退出 **1**，记录在 `reports/pr1733-claims-port-conflict/`。用户随后允许挂起并行测试，使用 `284xx` 顺序执行三个领取变体。CKB 和 UDT 的行为断言均通过；中继观察期间出现下述环境中断，不能据此认定节点缺陷。
- 文档结构检查 → `cases: 8`，退出 **0**；pytest 收集 → `4 tests collected`，退出 **0**；`git diff --check` 退出 **0**。项目映射检查 → `review cases: 106`、`automated cases: 86`、`duplicate review IDs: none`，退出 **1**；仍为原有七个孤儿映射，本次三个 ID 均有对应映射。全项目行数另受并行任务增加的评审影响，非本次新增行数。


### 调时执行与中断排查（2026-11-11）

- `reports/pr1733-claims-accelerated/native-results.xml` 中，CKB 与 UDT 方法分别报告通过（417.202 秒、403.956 秒）；CKB 净到账 `50,499,997,325` Shannon，UDT 净到账 `40,500,000,035` 最小单位，UDT 场景的 CKB 容量净增 `18,199,995,607` Shannon。两种场景均实际领回两笔不同哈希算法的应收款和本金，发票最终 Paid。
- 中继失败日志相邻时间从 `2026-11-11T07:13:29.148884Z` 跳到 `2026-11-11T16:17:47.974082Z`，跨过 9 小时 4 分，直接越过保护观察窗；这是环境中断，不能当成“中继确定不强关”的产品结论。原始 JUnit 还包含时间恢复失败的 teardown error；整次运行退出 **1**，执行包装器将其标为 blocked，因此不把该批报告说成整套通过。
- 原 `restore_time()` 的 NTP 请求超时，固定减一小时的降级也因 sudo 认证失败而未执行。已扩展现有封装：`change_time` 保留秒并返回命令是否成功；`restore_time(reference_timestamp=...)` 按保存的时间直接恢复。参考时间使用包含系统休眠的单调时钟计算，无需 NTP。实测推进一分钟再回滚，恢复偏差 `-0.499` 秒，退出 **0**；见 `reports/pr1733-clock-recovery/roundtrip.log`。
- 系统于 09:28 重启，旧的挂起进程已退出。并行退款测试随后重新启动为三个变体；等它们各自进入长期等待后再次挂起，并记录到 `reports/pr1733-relay-accelerated/paused-processes.json`。本次仅重跑中继，使用 `caffeinate -i` 防止自动休眠；观察中若时间跳变或发生休眠，单独报环境错误。最终恢复结果以该目录的执行记录与恢复记录为准。


### 中继复跑与最终收尾

- 中继单独复跑：`FIBER_TEST_BIN=download/fiber/received-tlc-claim-protection/fnn FIBER_CLAIM_PORT_BASE=28400 venv/bin/python -u -m pytest test_cases/fiber/devnet/connect_peer/test_v2_claim_recovery.py::TestV2ClaimRecovery::test_relay_claims_offline_upstream_after_restart -q -s -o log_cli=false`，外层使用 `caffeinate -i` 和执行记录包装器。原始结果 `1 passed in 374.52s (0:06:14)`；native 命令及包装器退出码均为 **0**，输入哈希未变。完整命令、日志和 JUnit 在 `reports/pr1733-relay-accelerated/`。
- 自动强关交易 `0x9ac5dba99e2d82d380a8db649188453bef8fdc29d59f30e7b7445a46f2736c56` 位于预定保护窗口。上游实际领取 `200,200,018` Shannon，下游已支付 `200,000,017`，路由费 `200,001`；本金及应收款钱包净增 `50,300,198,319` Shannon，领取手续费为 `914+785=1,699`。结合下游借记，中继两侧净变化为应得路由费减领取手续费，未重复支付下游；最后的下游双向付款探针通过。
- 系统时间已按保存的参考时间恢复，误差 `-1.175` 秒。随后执行 `reports/pr1733-relay-accelerated/resume_paused.py`，记录中的 **21 个进程**全部恢复，三个并行退款变体的六个节点 RPC 均可用，上游通道仍为 `ChannelReady`；见同目录 `resume.log`。
- 最终已取得四个执行变体的行为通过记录：累计重连 1 项、离线 CKB/UDT 2 项、中继复跑 1 项。它们来自分批运行；原中断批次的 failure/error 原样保留，未改写为成功，也未重跑已通过的行为。时间恢复及环境检查的修正已由一分钟往返验证和中继复跑实际覆盖。
- 最终 `git diff --check` 退出 **0**。项目映射检查为 `106` 条评审、`87` 条映射，退出 **1**，仍只有原有七个孤儿映射且无重复评审 ID；并行任务新增的映射不计为本次覆盖。本次没有覆盖其余五个候选 ID、旧 V2 升级或 base/head 对照。
