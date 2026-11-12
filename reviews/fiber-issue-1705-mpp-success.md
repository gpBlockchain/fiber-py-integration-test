# Issue #1705：MPP 成功判定与剩余 TLC 结算测试分析

## 导读

- **目的**：修复多路径付款中，一个分片已被领取、其他分片失败后整笔付款又被判失败的问题；尤其防止 trampoline 中继已经付出钱，却向上游退款。
- **行为变化**：发送方取得属于这笔付款的有效原像（preimage），或已有成功分片，就应显示 `Success`。其余已发出的 TLC 继续结算，付款成功不再等于所有资金锁定都已解除。收款方原有的发票金额、MPP 汇总及原像校验规则保留。
- **主要风险**：过早退款造成中继损失；成功后再次发款；重启漏掉已知原像；中继只在链上得到原像却未恢复上游结算；链上超时已退款却一直扣着上游资金；大量历史付款拖慢启动。
- **测试层**：采用 Fiber 测试项目的黑盒长期回归偏好，通过正式节点 RPC、启停/升级、真实 CKB devnet 交易和余额观察；以现有 Python 拓扑为复用起点。评审与自动化保存在 Fiber 测试仓库，原始分析输入作为本机证据保留。
- **测不到的**：公开 RPC 未暴露付款 actor 数量、attempt 唤醒索引、写库次数；付款成功或 TLC 消失均不证明 actor 已退出。精确的“写通道成功、尚未写付款状态”崩溃窗口和旧 V1 协议内部状态依赖上游专门测试。
- **测不到的**：标准收款流程不会故意只领取一个分片并拒绝另一个；`remove_tlc`、`check_channel_shutdown` 属 debug 专用 RPC，本文不据此设计正式构建的黑盒用例。该恶意收款核心反例已有上游进程内测试，须单独保留其运行证据。

依据：[验收 issue](https://github.com/nervosnetwork/acceptance-internal/issues/1705)、[Fiber PR #1732](https://github.com/nervosnetwork/fiber/pull/1732)。2026-11-10 读取快照：PR open；base `7aa1d5de724fb28b8ddfc663f5da792ff48f1ee9`，head `fb49c3dcbb2812bd85d9063a5f155296d5a1398a`，17 个文件。改动涉及付款状态含义、通道通知、重启恢复、上游结算及查询；未见新增 wire 字段或存储 schema，issue 的自动标签 `protocol schema` 不能代替实际影响分析。

2026-11-10 设计阶段完成源码、差异和现有断言的静态分析，当时未启动节点或重跑 PR 作者报告的测试；2026-11-12 的自动化及节点执行结果见文末。一次性核查精确 head 的 GitHub workflow：E2E、Continuous integration、WASM 单测及 OpenRPC 生成均 completed/success；[E2E run](https://github.com/nervosnetwork/fiber/actions/runs/42756585944)、[CI run](https://github.com/nervosnetwork/fiber/actions/runs/42756586018)。这是 workflow 级结果，不等于本文新增用例已执行，也不消除同 head 已报告的缺口。原始输入保存在 inputs（本机证据，未随 PR 提交） 和 diff（本机证据，未随 PR 提交）。

## 需要你决定

已确认（2026-11-10）：保留 MPP1705-07、MPP1705-08 两条 P0 trampoline 回归用例，分别覆盖中继正常运行和重启后，在全部下游无原像且链上退款确认后释放上游锁定资金。此次确认针对这两条用例的场景和预期；复选框只表示 TEST-MAP 状态。2026-11-11 已确认 MPP1705-07 首例实现规范；MPP1705-08 的两个重启变体已按该规范实现。运行状态见下方自动化验证。

已确认（2026-11-12）：用户要求“自动化所有的用例”，本批 01～14 均按已确认的 07/08 首例规范进入实现，不再逐条等待确认。12 的未定性能阈值保持未定；实现采集及功能校验，不把采集完成计作性能验收通过。

- **建议与开发同步，优先处理链上超时回归**：同一 head 的[既有评审](https://github.com/nervosnetwork/fiber/pull/1732#issuecomment-6760148569)报告 actor 不退出、trampoline 上游长期不退款。源码显示 pending 判定依赖 `REMOVE`，而两条 origin-payer 超时路径只设置 removal reason，静态路径与报告吻合；本轮没有重新复现。MPP1705-07、MPP1705-08 应作为修复验收重点；actor 退出还需开发侧补内部断言。
- **MPP1705-12**：待确认启动性能验收阈值（历史规模、启动至可付款的时限、RSS 上限及相对 base 退化比例）。评审中的“8,000 笔约 13 秒”是其 debug 本地测量，不能作为 release 基线或统一门槛。
- **慢测预算**：MPP1705-07～09 涉及真实链上超时。正式构建的最小 final TLC delta 为 160 分钟；转发还会增加期限差，合约等待另计。建议安排慢速回归；不把改系统时间、修改编译常量或直接写数据库作为前提。
- **升级覆盖边界**：MPP1705-10 使用 base 能经正常操作创建的“已拿到原像但另一分片尚未结算”数据。旧库中“Failed 且已有成功分片”的精确变体，本轮只核对上游 store 测试；进入黑盒升级验收前需有可重放的真实旧版数据来源。

## Spec

### SPEC-01：已领取分片建立成功，未完成分片继续结算
- Condition: 本付款的一个 TLC 返回与付款 hash 算法匹配的原像，或持久化 attempt 已成功。
- Expected: 付款变为 Success，返回原像并清除付款级旧错误；其他分片的迟到结果不降级成功，不触发新的付款/重试；已有 TLC 正常走完。
- Observable: get_payment / list_payments 的新响应、开放通道 TLC、最终通道/链上余额；actor 退出另行验证。
- Basis: [PR 行为契约](https://github.com/nervosnetwork/fiber/pull/1732)、[状态计算](https://github.com/nervosnetwork/fiber/blob/fb49c3dcbb2812bd85d9063a5f155296d5a1398a/crates/fiber-types/src/payment.rs#L1099)、[原像观察与退出条件](https://github.com/nervosnetwork/fiber/blob/fb49c3dcbb2812bd85d9063a5f155296d5a1398a/crates/fiber-lib/src/fiber/payment.rs#L1292)。发票足额检查保留；Success 不额外承诺所有分片已结清或 fee 已最终稳定。
- Source: explicit
- Testing: required

### SPEC-02：中继先保护已付款事实，再决定上游结算
- Condition: trampoline 的下游付款有有效原像、仍有未知结果的 TLC，或全部 TLC 已有失败结果。
- Expected: 有效原像推动上游领取，不必等付款 actor 退出；个别分片已失败但其他分片对 T 仍未知时，也不得先退款，付款会话的 Failed 标记不替代全部 TLC 结果；没有成功证明且失败结果全部确定后正常失败上游。
- Observable: 发送方付款状态、上游开放通道的锁定资金和余额、中继全路径净资金变化；未知链上结果与已确认退款分开建立前提。
- Basis: [上游结算实现与注释](https://github.com/nervosnetwork/fiber/blob/fb49c3dcbb2812bd85d9063a5f155296d5a1398a/crates/fiber-lib/src/fiber/network.rs#L7871)、[超时回归报告](https://github.com/nervosnetwork/fiber/pull/1732#issuecomment-6760148569)。预期来自“不在下游结局未知时退款”的付款安全规则，不把当前疑似卡住行为写作正确预期。
- Source: explicit
- Testing: required

### SPEC-03：重启恢复原像、结算和未完成工作
- Condition: 通道结果或链上结算已持久化，发送方/中继尚有 TLC 工作，或上游离线错过结算。
- Expected: 重启先恢复成功证明，再消费旧失败/重试；首次仅在链上出现的有效原像也应恢复付款成功，并驱动中继完成上游领取；继续已有结算并恢复上游结果，不重复扣款；无原像的确定失败同样应完成退款。
- Observable: 同一数据目录重启后的新 RPC 结果、链上确认和余额；内部写入间隙不是黑盒用例的声明前提。
- Basis: [启动恢复](https://github.com/nervosnetwork/fiber/blob/fb49c3dcbb2812bd85d9063a5f155296d5a1398a/crates/fiber-lib/src/fiber/network.rs#L8508)、[付款启动原像观察](https://github.com/nervosnetwork/fiber/blob/fb49c3dcbb2812bd85d9063a5f155296d5a1398a/crates/fiber-lib/src/fiber/payment.rs#L982)、PR 描述的 durable channel recovery；[链上领取恢复后调用上游结算](https://github.com/nervosnetwork/fiber/blob/fb49c3dcbb2812bd85d9063a5f155296d5a1398a/crates/fiber-lib/src/fiber/network.rs#L5190)。
- Source: explicit
- Testing: required

### SPEC-04：新旧付款查询一致反映成功证明
- Condition: 新付款或升级前保留的付款具有已领取分片，另一些分片尚未完成。
- Expected: get_payment、无过滤列表与 Success 过滤均显示成功；Failed/Inflight/Created 过滤不包含它，分页不漏项或重复；只做查询不等同于验证底层记录已写回。
- Observable: 预先记录的付款 hash 集合、状态、原像与排他游标；完成所有页后比较集合。
- Basis: [查询过滤改动](https://github.com/nervosnetwork/fiber/blob/fb49c3dcbb2812bd85d9063a5f155296d5a1398a/crates/fiber-lib/src/store/store_impl/mod.rs#L1503)、[历史部分成功测试](https://github.com/nervosnetwork/fiber/blob/fb49c3dcbb2812bd85d9063a5f155296d5a1398a/crates/fiber-lib/src/store/tests/store.rs#L1822)。
- Source: explicit
- Testing: required

### SPEC-05：付款成功后等待真实资金结果再断言收尾
- Condition: 单路/MPP、CKB/UDT、自付或并发付款经过这次状态/生命周期改动。
- Expected: 有效付款保持成功，invoice 正常结算，开放通道锁定资金最终释放，链上分支交付给正确一方；相同付款不多扣、其他付款继续可用。
- Observable: 逐资产余额守恒、独立统计的矿工手续费、invoice、开放通道 TLC 以及后续健康付款；不靠全局空池断言掩盖其他在途付款。
- Basis: PR 保留发票和通道验证的承诺、[混合资产自付等待调整](https://github.com/nervosnetwork/fiber/blob/fb49c3dcbb2812bd85d9063a5f155296d5a1398a/crates/fiber-lib/src/fiber/tests/payment.rs#L4838)、既有 Python MPP/自付测试。此处将既有资金不变式用于真实节点集成回归。
- Source: inference
- Testing: required

### SPEC-06：历史付款规模下启动仍满足部署可用性预算
- Condition: 同一硬件/构建类型/后端，有 0、1,000、8,000 笔公开接口产生的终态付款，通道数和 attempt 密度受控。
- Expected: 完成启动并处理新付款；启动时间、首笔付款时间和峰值 RSS 满足待确认的预算，历史余额和状态不被重放破坏。
- Observable: 从进程启动到 node_info 与真实新付款分别可用的耗时、RSS、付款结果；保留 base/head 分布和失败样本。
- Basis: [新增全历史恢复扫描](https://github.com/nervosnetwork/fiber/blob/fb49c3dcbb2812bd85d9063a5f155296d5a1398a/crates/fiber-lib/src/fiber/network.rs#L8510)、[既有性能风险报告](https://github.com/nervosnetwork/fiber/pull/1732#issuecomment-6760148569)；具体 SLA 未定义。
- Source: inference
- Testing: required

## 测试树

<!-- TEST-TREE-BEGIN -->
- MPP 成功证明与资金最终结果
  - 一个分片成功、另一分片在链上
    - 普通发送方提前成功并完成资金交付：MPP1705-01 [primary] -> SPEC-01, SPEC-05
    - trampoline 上游及时领取且中继不亏损：MPP1705-02 [primary] -> SPEC-01, SPEC-02
  - 重启与离线
    - 发送方成功后重启，剩余分片继续结算：MPP1705-03 [primary] -> SPEC-01, SPEC-03
    - 中继持有成功证明、上游离线，恢复后只结算一次：MPP1705-04 [primary] -> SPEC-02, SPEC-03
    - 发送方离线错过链上原像，启动后追上：MPP1705-05 [primary] -> SPEC-01, SPEC-03
    - 中继首次从链上恢复原像，再向上游完成领取：MPP1705-13 [primary] -> SPEC-01, SPEC-02, SPEC-03, SPEC-05
  - 无成功证明的失败路径
    - 下游链上结果未知时暂不退款：MPP1705-06 [primary] -> SPEC-02
    - 一片失败已送达、另一片结果被离线中继隔离时不退上游：MPP1705-14 [primary] -> SPEC-02, SPEC-05
    - 已确认下游超时后在线中继释放上游资金：MPP1705-07 [primary] -> SPEC-02, SPEC-05
    - 同一确定超时结果在重启后完成上游退款：MPP1705-08 [primary] -> SPEC-02, SPEC-03
    - 普通发送方链上退款后可继续付款：MPP1705-09 [primary] -> SPEC-03, SPEC-05
  - 查询与升级
    - base 的可公开构造在途数据升级，查询/过滤/分页反映成功：MPP1705-10 [primary] -> SPEC-01, SPEC-03, SPEC-04
    - [pending] 旧库 Failed 且已有成功 attempt — 原始 fixture 及其公开操作生成过程待提供；恢复入口为 base 的部分 MPP 失败重现，当前只核对 store 原生测试
  - 并发、资产与自身付款
    - CKB/UDT 自付和普通 MPP 并发、断连重启后守恒：MPP1705-11 [primary] -> SPEC-01, SPEC-03, SPEC-05
  - 启动性能
    - 历史规模、数据库后端和 base/head 对比：MPP1705-12 [primary] -> SPEC-06
  - 上游原生测试保留的精确性质
    - [not_applicable] 恶意只领取一片、拒绝另一片及相反消息顺序 — 正常发票接口不产生该行为，debug remove_tlc 和内部钩子不符合本次黑盒偏好；核对 trampoline 原生反例
    - [not_applicable] 错误原像、错误 attempt/通道归属及旧 V2 receipt 匹配 — 直接构造内部上下文不符合本次偏好，交由原生 channel/payment 测试；本次不声称重跑
    - [not_applicable] actor 数量、未发 attempt 历史、索引释放、通知/写库恰好一次 — 公开接口无直接观察；核对 closed_add_tests 与 actor 清理断言
    - [not_applicable] 精确写库崩溃窗口和旧 V1 协议注入 — 依赖私有状态或精确时序；公开重启场景只声明端到端恢复，不替代上述性质
<!-- TEST-TREE-END -->

## 用例

公共约定：P 为付款方，T 为 trampoline 中继，R 为收款方，M 为普通中继。拓扑 D：`P ==两通道== M — R`；拓扑 T：`P — T ==两通道== R`，发票开启 `allow_mpp` / `allow_trampoline_routing`，显式使用 `trampoline_hops=[T]`，不允许绕开 T。普通 MPP 同样开启 `allow_mpp` 并允许至少两片。两分片通道每条实际可发金额小于发票总额、两条合计足够；以 `list_channels.pending_tlcs` 核实分片身份、金额及两端 Committed，不预设路由器恰好一半。使用 hold invoice，测试独立生成原像及 SHA-256/CKB hash，等 `Received` 且两片已提交后再操作。未凑齐阶段有短 hold timeout；Received 后忽略该短定时器，但仍受真实 TLC expiry 约束；准备超时直接报前提未建立。

拓扑 TF（14）：上游 `P — T`，下游 A 路 `T — R`、B 路 `T — M — R`，M 为普通中继。两个 T 出向通道的可发金额均小于发票总额、两路合计足够，M–R 余额足够承载 B；显式指定 trampoline T、max_parts=2，以逐跳 TLC 身份确认实际经过两路。此拓扑增加 M 是为了隔离一条失败返回路径，不依赖精确消息时序。

金额及观察：默认路由费设为 0，单独记录无法设零的费用；保存每端起始通道余额、锁定金额及专用链上收款 lock。余额比较按 CKB/UDT 分开，链上手续费由所消费 inputs 与 outputs 独立计算。关闭通道允许保留历史 TLC，因此最终链上交付要看对应 outpoint 消费、交易确认和接收 lock/资产金额，不要求 `pending_tlcs=[]`。get_payment 与 list_payments 共用状态逻辑，两者一致只算查询一致性，资金正确性由接收方与链上数据提供独立证据。

时间预算（设计值，未实测）：`W开通` 为 120 秒单阶段观察窗；`W结果` 为原像/退款证据可用后最多 420 秒（包含 300 秒后台关停检查、60 秒付款检查与余量）；`W链` 为实际 commitment delay/epoch 与必要交易确认的等待；`W超时` 按 RPC 记录的 TLC expiry 及合约要求计时。正式构建最小 delta=9,600,000ms（160 分钟），debug=1,333ms；二者数据不混作同一性能或时限证据。正常用例另有约 5 分钟拓扑准备；链上用例耗时按 `W链` 单列，不能承诺全部十分钟内完成。测试用 CKB devnet 正常出块推进 epoch，不修改主机时钟。超时是失败/前提不满足，不能跳过后报告通过。

MPP1705-01～14 均已建立 TEST-MAP；复选框只表示映射存在，不表示运行或性能验收通过。分组入口与实际结果见文末。

<!-- TEST-CASES-BEGIN -->
| 用例 | 场景 | 预期结果 | 防止的问题 | 优先级 |
| --- | --- | --- | --- | --- |
| `MPP1705-01` | - [x] 拓扑 D 两片已接收，M 强关其中一条 P–M 通道；暂停出块保留结算窗口，R 用正确原像结算；分别覆盖 sha256/ckb_hash，再恢复出块（约 5 分钟准备＋W结果＋W链） | 链上分片尚未完成时 P 已 Success、原像正确且 failed_error 为空；其后保持成功，R 收足且两条路径最终只交付应有金额 | 把全部分片结清当成成功前提，或只验证成功不验证资金 | P0 |
| `MPP1705-02` | - [x] 拓扑 T 两片已接收，R 强关一条 T–R 通道，保留链上未结算窗口并结算 invoice，随后推进链（约 5 分钟准备＋W结果＋W链） | T 取得原像后 P 即收到成功并结清 P–T 锁定资金；T–R 剩余分片继续结算，中继扣除实际链费后不因上游退款损失本金 | 下游已付款但上游退款，或上游必须等下游 actor 退出 | P0 |
| `MPP1705-03` | - [x] 建立 01 的 Success 且链上分片尚未结算状态，正常停启 P、保留数据目录；恢复连接及出块（约 5 分钟准备＋2×W结果＋W链） | 重启后同一 hash 和原像保持成功，剩余资金交付完成，余额不重复变化，健康通道可再付款 | 成功只在内存中、重启重付或丢失剩余工作 | P1 |
| `MPP1705-04` | - [x] 拓扑 T 分片已接收后停止 P；一条 T–R 通道强关、R 结算，等 T 可查询成功后重启 T，再启动 P；结算后再重启一次（约 5 分钟准备＋3×W结果＋W链） | P 恢复后取得同一原像并成功，上游只扣一次、T 无本金损失；再次重启不新增扣款或锁定 | 成功通知丢失、上游恢复不结算或重复领取 | P1 |
| `MPP1705-05` | - [x] 拓扑 D 两片已提交，核实 P 未成功且 payment_preimage 为空后停止 P；M 强关一条 P–M 通道并由 R 结算；确认使用原像的链上消费后停止 M，P 无 Fiber 对端地启动查询，再恢复 M 收尾（约 5 分钟准备＋W链＋W结果） | 尚无 Fiber 对端时 P 即经链上追赶恢复 Success 和匹配原像；恢复 M 后继续成功且最终交付与原交易一致 | 把重连后收到的原像误当链上恢复，或链上已领取仍永久失败 | P0 |
| `MPP1705-13` | - [x] 拓扑 T 两片 Committed，确认 P/T 未成功且无原像后停 P/T；R 强关一条 T–R 并结算发票，确认该分片凭原像链上领取后停 R；T 无 Fiber 对端启动，查证后仅恢复 P 结算上游，最后恢复 R 收尾（约 5 分钟准备＋W链＋3×W结果） | 恢复任何 Fiber 连接前 T 已 Success 且原像正确；R 仍离线时 P 恢复同一原像并成功，开放 P–T 只结算一次且释放锁定；最终 R 收足、P 不多付，T 扣除实际链费后无本金损失 | 把重连后取得的原像误作链上恢复，或链上已领取却未结算上游、重复扣款 | P0 |
| `MPP1705-06` | - [x] 拓扑 T 两片已接收但不提供原像；强关全部 T–R 分片通道，保持到期前、链上结果未定，观察一个 W结果（约 5 分钟准备＋W结果） | P–T 的原付款资金仍锁定，上游既未成功也未退款；其他独立健康付款可完成 | 因下游关闭就提前退款，留下可被领取的下游支出 | P1 |
| `MPP1705-14` | - [x] 拓扑 TF 两片已 Received/Committed 且无原像；停 M 并确认断连后，R 取消发票，等 A 在 T 端完成失败握手、B 原 TLC 仍 Committed，观察一个 W结果，再恢复 M 传回 B 结果（约 5 分钟准备＋W开通＋3×W结果） | 混合窗口内 P–T 保持开放，原付款无原像且全额锁定，不因 A 失败提前退款；M 恢复后 W结果 内上游 Failed、全额退回含费用预算的原锁定金额，各目标 TLC 解锁、所有原通道保持开放且余额恢复，无重复扣款 | 把首个失败当成整笔付款已无在途支出，提前退上游；或最后一个失败到达后仍不退款 | P0 |
| `MPP1705-07` | - [x] 延续 06，P/T 在线，停止 R 保证没有原像；正常推进至全部下游 TLC 的退款交易已确认且资金归 T（约 5 分钟准备＋W超时＋W链＋W结果） | W结果 内 P–T 释放对应锁定金额、付款失败且无原像；P 收回原本金，健康上游通道继续可付款 | 已确认下游超时仍无限推迟上游退款 | P0 |
| `MPP1705-08` | - [x] 独立重建 07 的公开状态，分别在下游关闭未退款、退款已确认两个阶段正常重启 T，P 保持在线（每变体约 5 分钟准备＋W超时＋W链＋W结果） | 重启前未知结果不提前退款；全部下游退款确认后上游正常失败并释放本金，不因重启永久挂起或重放扣款 | 启动扫描重新唤醒已确定超时工作并卡住上游 | P0 |
| `MPP1705-09` | - [x] 普通 P–R hold 付款接收后停止 R，P 强关通道；无原像地完成链上超时退款，重启 P 并通过另一健康通道付款（约 5 分钟准备＋W超时＋W链＋W结果） | 旧付款 Failed、无原像，退款到 P 的正确 lock且扣费可核对；新付款成功；本例只证明用户资金和可用性，不声称 actor 已退出 | 直接付款资金回不来、旧结果阻塞后续使用 | P1 |
| `MPP1705-10` | - [x] 用 base 正常创建 01 的已获原像但尚有链上分片状态并记录旧查询；停机备份数据，升级 P 到 head；与普通成功/失败对照付款一起按 limit=1 遍历列表（约 5 分钟准备＋升级时间＋W结果＋W链） | 升级后目标付款由旧在途变 Success、原像正确；无过滤/Success 集合恰含目标，其他状态过滤排除，分页无漏重；剩余链上交付完成 | 升级后继续显示在途，或 Failed 提前过滤误排成功证明 | P1 |
| `MPP1705-11` | - [x] CKB/UDT 环形自付及 MPP 以每批 10 笔共 100 笔唯一 hash 付款；在第 3/6 批用 hold invoice 等到 Received/Committed 后，分别对指定中继 M 做 disconnect_peer/connect_peer 和正常停启，再用原 hash/原像结算（约 15～30 分钟设计预算） | 故障期间确有目标在途 TLC；恢复后原付款完成且成功款不二次扣付，自付各资产本金守恒，相应锁定释放；最后一笔健康付款成功 | 故障未打到在途工作却误报覆盖，或并发恢复后重复扣款和长期锁定 | P1 |
| `MPP1705-12` | - [x] 同一机器对 base/head、RocksDB/SQLite 分别用公开接口积累 0/1,000/8,000 笔终态历史，固定通道数并记录实际分片密度；每规模重复启动 10 次测首笔付款与 RSS（数据准备计时另列，每次启动观察上限暂定 5 分钟） | 待确认：启动/首笔付款时限、RSS 上限和允许退化比例；先提交逐次数据及分布，历史记录和资产不变，新付款完成 | 全历史恢复扫描使大数据库启动慢或占用过高 | P1 |
<!-- TEST-CASES-END -->

## 验证设计

1. **关键前提**：01～04 必须同时留存“正确原像已可观察”和“另一分片尚未链上完成”的证据，再断言提前成功。使用暂停 devnet 出块形成宽窗口，避免精确卡消息；若节点在该场景没有得到早期证明，应报告前提未建立，不能退化为全部结清后检查 Success。05 单独证明原像来自链上：确认链上消费后停止 P 的唯一 Fiber 对端 M，清空启动 bootstrap 对端列表，启动后用 list_peers 核实没有 Fiber 对端；在重新启动 M 前完成 P 的原像/状态检查。CKB RPC 与链上索引服务持续可用。
2. **超时退款前提**：06～08 要区分 commitment 上链、TLC 到期、实际超时退款三件事；只有正确 outpoint 已消费、退款交易确认及收款 lock/金额匹配才算确定失败。上游会在名义到期前进入保护性强关（[条件](https://github.com/nervosnetwork/fiber/blob/fb49c3dcbb2812bd85d9063a5f155296d5a1398a/crates/fiber-lib/src/fiber/channel.rs#L3867)）。07/08 通过公开通道/付款参数加大上游期限裕量，按实际 TLC expiry 与 commitment_delay_epoch 检查：上游 expiry 必须晚于“预计全部下游退款确认时间＋W结果＋上游 2/3 commitment delay 换算毫秒＋60 秒维护周期＋运行余量”；每次重启后重新检查剩余窗口。观察期间 P–T 必须维持开放，避免把上游自身到期强关取回的钱混作中继正常失败传播。裕量不够是前提失败，须调整公开期限参数后重建独立 fixture。
3. **独立预期**：01～05、10、13 的原像由测试生成并用独立 hash 实现核对；付款 hash 及 TLC/channel identity 由创建阶段记录。资金 oracle 为逐资产守恒和已知初始余额，不以 send_payment 返回 fee 或单次状态查询直接推出到账。链上先验证准确消费关系及接收者，再加总容量/UDT；只看到交易条数或 commitment cell 消失不算交付证明。
4. **成功后不再付的可观测范围**：保留 Success 后对应开放通道 TLC identity 和最终净支出；出现新锁定/额外支出即失败。RPC 采样不能证明“从未调过某个内部重试函数”，该精确性质依赖原生测试。链上剩余分片合法支付会改变余额，应与重复付款区分。
5. **升级**：10 使用同一数据目录副本、同样合约及公开创建步骤，仅替换 FNN；分别保留旧查询和新查询。基准 head 是比较用，不在长期自动化中按某版本跳过断言。旧 Failed 部分成功数据尚无符合偏好的稳定构造，不冒充 10 已覆盖；V1/V2 协议也不与 commitment lock 的 Legacy/V1 编码混称。
6. **并发恢复**：11 在环形/多路径图中指定同一个转发角色 M，禁止付款路由绕开 M；第 3 批在 M 的两侧分别调用 disconnect_peer，核实 list_peers 断开后 connect_peer；第 6 批正常停止/启动 M。操作前保留该批 Received/Committed 和 TLC identity，恢复后 settle_invoice 使用原 hash/原像，不由测试另造新付款掩盖丢单。每批结算并检查本金/锁定后再进入下一批。自付的 receiver=P、ordinary MPP 的 receiver=R，CKB/UDT 分开记账。
7. **性能**：12 固定硬件、优化级别、日志级别和后端，逐规模从同一准备快照复制独立运行目录；base 与 head 不共写数据库。分别记录冷/热缓存条件、历史数量、成功/失败比例、attempt 密度、数据准备耗时；输出全部 10 次样本和中位数/范围。RPC 端口能连不等于网络 actor 已可处理付款；以真实新付款完成作为另一计时点。造数失败、启动超时和支付失败均保留，不丢弃再重跑算通过。


8. **中继仅从链上取得原像后结算上游（13）**：沿用拓扑 T 与 hold invoice，通道转发费设为零，但 trampoline 的转付预算必须为正，二者分别记录。固定发票金额 A=1500 CKB、send_payment.max_fee_amount=B=A/100；故障前核对 P/T 两端 Committed 的上游金额一致为 A+F、0<F≤B，且两片实际金额合计为 A，再冻结 F 作为这笔已承诺付款的费用基准，不从付款完成后的 fee 字段反推预期。使用正式 RPC、正常启停及独立 CKB devnet；不修改二进制、数据库或主机时间。预先记录付款 hash、两片实际金额、通道/TLC identity、起始余额和独立生成的原像。确认 invoice=Received、两片两端 Committed，且 P/T 的 get_payment 未成功、payment_preimage 为空，再正常停止 P/T并确认进程退出。原像此时尚未交给任何 Fiber 节点；只在 P/T 停止后向 R 调用 settle_invoice，避免把原像事后隐藏当成“首次链上取得”。
   - **构造链上领取**：R 用 shutdown_channel(force=true) 强关其中一条 T–R；确认 commitment 上链后，以原 hash/原像结算 Received 发票。T 始终离线；另一条 T–R 的链下消息即使已在 R 排队也尚未送达 T。保持 CKB 出块、RPC/索引与 R 的 watchtower 可用，直到准确下游 TLC 对应的链上消费已确认；核对消费关系、领取 witness 中的原像及独立 hash、R 的收款 lock 和金额，排除 commitment 上链或超时退款冒充原像领取。随后正常停止 R。
   - **隔离阶段**：P/R 均停机，T 保留原数据目录、清空启动 bootstrap 对端后启动；仅保持 CKB 链访问，持续检查 list_peers 为空，避免自动重连污染来源。T 进程启动后 W开通 内须取得 node_info 响应；从该首次成功响应起计 W结果（包含链上追赶），针对原 hash 的新 get_payment 响应须为 Success、payment_preimage 等于预先生成的原像且 failed_error 为空；任一 Fiber 对端提前接入即判前提失败。此阶段不要求 P–T 已完成链下结算，也不向 T/P 调用任何提供原像的 RPC。仅在重连后才变成功，不算此阶段通过。
   - **上游与收尾阶段**：完成上述断言后只启动 P、显式连接 P–T，R 继续停机；W结果 内 P 对同一 hash 得到正确原像并 Success，P–T 保持 ChannelReady，原 TLC 的锁定释放，P/T 两端按约定金额仅结算一次。此前必须保有原上游锁定，避免已成功的上游冒充恢复。再恢复 R，用原付款完成剩余分片；W结果 内开放下游对应锁定释放，必要链上确认按 W链 等待。最终按资产合并通道余额和节点所属链上资金，剔除退回的通道保证金、找零与独立识别的链费资金输入；R 实收本金 A，P 净支出 A+F，T 净变化为 F 减其实际承担的链费（若 R 承担链费，则单独核对 R 的净额），禁止用未归属的交易总手续费代替 T 的实际支出；不把另一条路径重新付款当作原付款恢复。
   - **时间与覆盖边界**：准备和每个结果阶段都有限时，链上领取使用实际 commitment delay/确认时间，不作固定分钟数承诺；提前记录各 TLC expiry，给下游原像领取和整个隔离/重连流程留足公开参数可配置的裕量。P–T expiry 须晚于预计链上领取完成、T 追赶、上游结算窗口，再加其 2/3 commitment delay、60 秒维护周期及运行余量；每次启动后核对剩余裕量和通道仍开放。裕量不足为前提失败，禁止靠 P–T 强关结算完成上游断言。本例验证真实链上来源与 trampoline 上游资金结果；02/04 已允许中继先获原像，05 没有 trampoline 上游，本例不重复内部 receipt/actor 清理断言。


9. **首片失败、另一片结果尚未送达（14）**：复用正式构建及公开 hold invoice/cancel_invoice/启停操作，不用 debug remove_tlc、内部事件注入或链上超时替代本场景。拓扑 TF 固定发票金额 V=1500 CKB，A 与 B 每路可发不足 V、合计足够，M–R 可承载 B；通道转发费设为零，trampoline 预算仍为正（max_fee_amount=V/100）。故障前从 P/T 两端 Committed 核对并冻结上游金额 U=V+F、0<F≤V/100，下游两片合计 V；记录每条通道初始余额、原 hash、逐跳 TLC/channel identity、金额和 expiry。确认 R invoice=Received、两片逐跳两端均 Committed，原像只在测试进程，任何节点尚未取得原像。
   - **先隔离 B，再让 A 真正失败**：正常停止 M 并确认进程退出，同时从 T/R 的 list_peers 确认 M 已离线，再调用 R.cancel_invoice 并确认发票 Cancelled；T/R 保持相连，M 在整个混合窗口持续停机。取消作用于整张发票，但 B 的结果停留在离线 M 另一侧，不能把“R 已取消 B”写成“T 已收到 B”。从取消 RPC 首次成功响应起 W结果 内等 A 完成失败握手：T/R 对原 A TLC 的新查询须为已清理或 RemoveAckConfirmed，两端该付款锁定均为零、余额回到付款前，T–R 通道保持 ChannelReady。取消 RPC 返回、T 的聚合 Failed/failed_error 或单端 TLC 消失都不代替这组证据。
   - **混合状态窗口**：从上述 A 失败握手证据齐备时起计完整 W结果=420 秒，不从取消 RPC 起算。整个窗口核对 T–M 原 B TLC 仍 Committed、原金额仍锁定；P–T 原上游 TLC 仍存在且 U 全额锁定，P 付款 Inflight、无原像，两端本金没有退回或交付。所有相关通道均保持开放、funding outpoint 未被消费；M 不得自动恢复连接。T 的聚合状态只作记录，不要求必须 Failed 或必须 Inflight，也不将某次 failed_error 当成逐分片证据。若 B 提前完成、通道强关或 A 未完成失败握手，本例前提未成立，不计为“不提前退款”通过。
   - **最后一个失败到达后收尾**：窗口结束后用 M 原数据目录启动，W开通 内重连原 T–M、M–R。从重连开始直至最终断言，持续检查 P–T、T–R、T–M、M–R 的可查询端均保持 ChannelReady、原 funding outpoint 未消费；M 恢复后覆盖各通道两端，任何自动强关均失败，不以链上取款作为正常退款。随后 W结果 内，原 B 的失败完成，上游 P 付款 Failed、无原像，P–T 释放 U、P 收回包括 trampoline 费用预算的全部原锁定金额；A/B 及上游各端目标锁定清零，付款前通道余额逐一恢复。没有成功分片，也没有链上结算费，故不应留下实际路由扣费或重复扣款；允许历史失败 TLC 行保留，不强制列表为空。全程跟踪原付款与 TLC，另发新付款不作为本例收尾证据。
   - **时间与证明边界**：P 的公开付款 timeout 覆盖整个取消、混合观察及重连退款流程（至少 W开通+3×W结果+准备余量，可设 1800 秒），避免本地请求超时混淆上游失败。逐跳实际 TLC expiry 必须晚于预计流程结束，再加各通道 2/3 commitment delay、60 秒维护周期及余量；启动后再次核对。每阶段有上述明确预算，余量不足直接判前提失败，无需真实到期或修改系统时间。本例证明 T 的公开通道已完成 A 的失败、B 仍在途时不退上游；正式 RPC 不暴露逐 attempt 失败状态，因此不声称已直接验证内部 actor 消费顺序或精确 Failed+pending 分支。原生测试继续负责“恶意失败一片、另一片随后出原像”等内部精确排列；本例也不证明 B 对所有节点都未知。

## 现有覆盖与复用

本地参照项目：本测试仓库，Git HEAD `004e5132bbc00e0b1c0c150fd1a6c06b96395a87`；读取其现有工作树文件，未声称所有文件与该提交完全相同。其 AGENTS.md 指定黑盒、公开操作、避免重复进程内用例与测试钩子。以下为静态核对：

| 现有入口 | 已有断言/能力 | 本次需要补的差别 |
| --- | --- | --- |
| `test_cases/fiber/devnet/send_payment/mpp/test_mpp_force_close_preimage.py` | 平行通道、hold invoice、单通道强关、线上分片清理及链上消费 | 增加链上完成前 payer Success＋原像，精确接收 lock/金额；仅有交易数量/容量差不足以证明交付 |
| `test_cases/fiber/devnet/watch_tower_wit_tlc/test_force_close_mpp.py` | 多/单通道强关、中间节点重启、最终 invoice/payment 状态 | 可复用拓扑；补提前成功、无原像的已确认超时退款，以及持久恢复后的上游资金释放 |
| `test_cases/fiber/devnet/trampoline_routing/test_mpp.py` | trampoline MPP 和两种 hash 算法正常付款 | 现有 Success 等待不足以证明中继本金无损或上游早于下游全部结算 |
| `test_cases/fiber/devnet/list_payments/test_list_payment.py`，评审 PR1721-01～04 | 有效状态过滤、分页与结算迁移 | 复用查询断言；补部分已领取、其他分片尚未完成及升级数据，旧“持久化 Failed 必须优先”预期需调整 |
| `tests/bruno/e2e/watchtower/force-close-mpp`（上游） | 本 PR 已将中途 `Inflight` 改成 `Success` 并检查原像，保留最终结算 | 精确 head 的 E2E workflow 已 success；未逐条检查其执行日志，其 debug 检查关停入口不作为正式构建快进手段 |
| 上游 `tests/trampoline.rs`、`tests/payment.rs`、`payment.rs::closed_add_tests`、`store/tests/store.rs` | 单片成功另一片失败、成功后处理迟到失败/重启、早期原像观察、未发 attempt 不改 Failed、旧库查询 | 保留这些原生性质，不在 Python 重做内部钩子；本轮未运行，不能用作者历史通过数宣称当前全部通过 |

**建议执行顺序**：先确认 01、02 的宽窗口和资金 oracle，以及 14 的部分失败不退款窗口；并行安排开发修复/验证 07、08 的已报告回归；随后 03～05、13、10，再做 11 与已有发票/哈希/自付回归；12 在性能门槛确定后验收。06～09 用独立慢测环境；自动化仍按确认后的首例规范推进。

## 设计与执行证据边界

设计阶段已完成独立复核；该结论只针对设计，不代表本批实现已获独立覆盖审查。07/08 首例规范已确认，本批延续相同公开接口、链上消费和资金断言。原始本机执行记录保留在作者环境，以下列出实际构建、命令和结果，不发布机器绝对路径或未提交的报告链接。

## 2026-11-12：全部已确认用例的自动化

新增文件位于 `test_cases/fiber/devnet/send_payment/mpp/`：
- `test_mpp_success_recovery.py`：01/02 的提前成功及精确链上净额，03/04 重启，05/13 隔离 Fiber 对端的链上原像恢复，10 真实 base 数据升级和分页集合。
- `test_mpp_failure_boundaries.py`：06 全未知持单、14 一片已失败另一片隔离，09 普通付款真实超时退款。
- `test_mpp_batch_recovery.py`：11 十条资金 lane 共用普通中继，十批共 100 笔原 hash；CKB/UDT、自付/普通 MPP，第三/第六批对在途持单断连/重启。每批结算后用公开单通道路由反向调平；这些调平付款单独生成，不冒充那 100 笔目标付款。
- `test_mpp_startup_history.py`：12 的 4 构建 × 3 历史规模 × 10 次启动，完整样本、失败、时延分布及采样 RSS。数据库来自正常付款和停机复制，不编辑 DB；检查实际 RocksDB/SQLite 文件格式。SLA 未设置时结果标记 `pending`。
- `mpp_fixture.py`：复用 07 的准确 outpoint 消费与所有者净额辅助；原像独立生成，开放通道两端余额比较以付款前为基准，链费通过交易输入输出独立计算。

05 的隔离阶段同时暂时停止 R，避免已持久化的 gossip 地址自动接入 P；纯链上原像断言完成后才恢复 M/R。03 选择金额较大的分片通道强关，为原健康通道保留后续付款流动性。13 先登记原像并推进链，再等最终 Paid，不把全部对端离线时的提前 Paid 当作前提。

07/08 原有文件及断言保留。新增的离线辅助负向测试不计为产品用例通过。这里完成的是实现及聚焦验证，不声称独立 B 已复核本批代码；已有 B 结论仍只针对设计。

### 长期回归运行方式

用例直接由项目已有 pytest / Makefile 流程发现和执行，不维护 issue 专用运行脚本。普通回归继承 `FiberTest` 的 `CURRENT_DEV` 构建配置；不要在测试中绑定本机 PR 分支二进制。使用项目 Python 环境，从仓库根目录选择文件、类或测试方法：

```bash
# 成功、重启、纯链上原像恢复（01～05、13）
python -m pytest test_cases/fiber/devnet/send_payment/mpp/test_mpp_success_recovery.py::TestMppSuccessRecovery -v

# 未知结果与首片失败的上游持单边界（06、14）
python -m pytest test_cases/fiber/devnet/send_payment/mpp/test_mpp_failure_boundaries.py -k 'not direct_timeout' -v

# 混合资产批量与故障恢复（11）
python -m pytest test_cases/fiber/devnet/send_payment/mpp/test_mpp_batch_recovery.py -v

# 真实到期退款慢测（07～09），串行执行
python -m pytest \
  test_cases/fiber/devnet/trampoline_routing/test_trampoline_timeout_refund.py \
  test_cases/fiber/devnet/trampoline_routing/test_trampoline_timeout_refund_restart.py \
  test_cases/fiber/devnet/send_payment/mpp/test_mpp_failure_boundaries.py::TestMppFailureBoundaries::test_direct_timeout_refund_then_sender_restart_and_new_payment -v

# 升级使用明确的历史构建；升级后的目标沿用 CURRENT_DEV（10）
MPP1705_BASE_FNN=/absolute/base/fnn python -m pytest \
  test_cases/fiber/devnet/send_payment/mpp/test_mpp_success_recovery.py::TestMppUpgrade -v

# 启动历史规模矩阵（12）
MPP1705_PERF_BUILDS=/absolute/builds.json python -m pytest \
  test_cases/fiber/devnet/send_payment/mpp/test_mpp_startup_history.py -v
```

向相同命令追加 `--collect-only` 只收集，不启动节点。不要并行启动使用公共 CLI 临时文件的节点测试；本批没有独立并发调度器或自动端口分配。慢测按实际 expiry 等待，可能超过一天。既有 MPP CI 直接运行目录，并排除真实到期、升级、批量和性能用例；这些用例可用上述 pytest 节点选择器独立运行，不以排除计作通过。

升级案例保留已确认的 base `7aa1d5d` 输入，用于经公开操作重建历史部分成功状态；这是历史数据来源，不是目标版本锁定。性能 manifest 是四项 JSON 数组，每项含 `label`（base/head）、`backend`（rocksdb/sqlite）、`binary`（绝对路径）、`commit`、`profile`（release）、`flags`（相同的通用优化参数，后端区别另由 backend 表示）。缺少构建或格式不符直接失败。可用 `MPP1705_PERF_THRESHOLDS` 提供已确认阈值：`max_node_info_seconds`、`max_first_payment_seconds`、`max_sampled_peak_rss_kib`、`max_head_base_ratio`；未确认前不设置。缓存记录为未主动清缓存，RSS 为 100ms 周期采样峰值。

### 本批验证结果

实测构建：base `7aa1d5d`（SHA-256 `95b7118f33c4abcfcb80d91583cc815bb8c71b0d303b1b5639b150f096607f39`），head `fb49c3d`（SHA-256 `e930839f8102fbe73403f48788fca632074b891a0416c119df816dee153cb228`）。收据中的 `product` 是现有源码检出 `43193445` 的环境快照，不代表实际执行的是该源码版本；节点版本及上述二进制哈希才标识本次被测构建。

- 调整为原生 pytest 前，常规、升级和批量组合的历史记录（本机运行记录）：11 个原生入口，8 passed、3 failed，退出 1。通过项包括 01 两种哈希、02、04、06、10、11、14；11 完成 100 笔混合付款和在途断连/重启，14 完成 420 秒混合失败窗口及全部退款。首次失败均保留，不用后续结果覆盖原始日志。
- 03/05/13 修复测试夹具后单独重跑：3 passed、退出 0，约 1009 秒（本机运行记录）。05/13 均在 Fiber 对端隔离时恢复正确原像；13 随后在 R 仍离线时完成开放上游结算。03/05/13 原失败分别来自健康探针流动性、gossip 自动重连和全离线时提前等待 Paid，均已通过调整公开前提修正，原资金断言保留。
- 最终辅助负向测试：7 passed，退出 0（本机运行记录），只验证测试辅助；全部入口收集（本机运行记录）为 `16 tests collected`、退出 0，不启动节点。
- 项目原有全局映射脚本退出 1（本机运行记录）：既有 `RSETTLE-01` 重复以及 `PMP-01～05`、`PR1677-09`、`SETTLE-01` 孤儿映射；这些 ID 不在本批范围。本批单文档的结构、14 条映射和补丁/回滚另用限定输入检查，不据此宣称全仓检查通过。
- 07/08 的原有三个慢测入口保留；历史真实运行均因 RPC `request time out` 结束，无通过证据，本批未重新等待其真实到期。09 已实现并收集，本批未运行真实到期。12 已实现完整采集和功能检查，尚缺经过核验的四构建 manifest，未运行 120 次矩阵；性能阈值仍待确认。上述四个 Case 不计为实测通过。

本批没有修改产品源码、时间或编译常量。最终版未重新执行整个 fast/upgrade/batch 组合；运行结果按各收据固定的输入版本解释。


发布前在最新 `v0.10.0` 干净导出上验证辅助测试和全部入口收集。源码以 Black 格式化，语法树与已实测文件逐个比较，除格式外保持相同；真实节点完整组合未在该导出上重跑。
