# PR #1728：旧通道付款移除后的结算余额

## 导读

- **目的**：旧通道的一笔付款从本地承诺中移除时，失败款应退回发送方，成功款应计入接收方，结算金额不能少一笔。
- **行为变化**：修复前，在等待上一轮确认的窗口里，旧通道本地承诺已去掉该付款，却仍从发送方余额中扣除这笔金额；修复后按失败或成功结果计入余额。具体是否表现为承诺签名校验失败、付款卡住或链上结算异常，需要运行证据，本文不把推测写成已发生故障。
- **影响部署**：新增条件只作用于 `channel_features=0` 的 Legacy 通道及其本地承诺；目标版本的新建 V2 通道、Legacy 远端承诺的计算条件不变。这里的“旧通道”是旧节点建好后由新版继续使用的存量通道。历史评审里的 V1 布局名称与本 PR 的 V2 协议名称不要混用。
- **集成层能测到**：两个真实旧节点先建通道，升级双方后，批量完成或取消已接收付款，再强关并核对双方到账；CKB 和 UDT 分别运行。这验证升级、签名交互和链上兑付的完整路径。
- **证明边界**：公开 RPC 没有稳定停住这次 ACK 交错的操作。并发请求可能进入该窗口，但通过一次批量回归不证明命中过修复分支；精确交错和内部余额算式由上游单测负责，不新增依赖消息截留、内部状态改写或测试专用二进制的集成用例。

依据：[PR #1728](https://github.com/nervosnetwork/fiber/pull/1728)，2026-11-10 核对已合并。base / merge-base 均为 `704a14ea11cdf4a3d1799076e6aaa0f3ca4251b1`，head 为 `46ff437800a0743c804ce1a3a5ad4fbd5d3f9974`，merge 为 `7aa1d5de724fb28b8ddfc663f5da792ff48f1ee9`；一个提交、三个文件，生产代码只改一个布尔条件。[固定版本差异](https://github.com/nervosnetwork/fiber/compare/704a14ea11cdf4a3d1799076e6aaa0f3ca4251b1...46ff437800a0743c804ce1a3a5ad4fbd5d3f9974)。最初评审只做静态分析；用户确认后的实现与运行方式见文末。上游 Rust 测试未重跑。

## 需要你决定

`PR1728-01`、`PR1728-02` 两行已获用户明确确认。首例自动化写法也已获用户明确确认，现已实现 `PR1728-02` 的 CKB、UDT 变体，按已确认规范验证剩余用例，无新增人工门禁。两行均验证真实旧通道的付款与资金结果，不承担精确交错的确定性复现。

## 用例

公共约定：

- A 为主要付款方，B 为接收方。两个官方 `v0.9.1 (9a561b3)` 节点用公开 RPC 先建 Legacy 通道，完成一次双向付款并停机备份，再通过目标版本的正常迁移流程升级双方。恢复后核对同一 `channel_id`、funding outpoint、付款前余额和最新承诺 hash，确认原通道仍可用；不以另开通道替代升级。链上强关时核对承诺 args 仍为 57 字节。
- 每行分别使用 CKB 和 UDT 两种资产，独立建链和记账；UDT 使用真实发行资产并在两侧准备足够余额及 CKB 预留。使用直连通道，付款不经过中继；两侧资金、TLC 数量和在途额度均足够。
- 先用一笔正常付款证明升级后的原通道可用，再记余额基线。每轮 A 向 B 发四笔不同 hash、不同金额的持单付款，必须逐笔在 B 观察到 `Received`、A 观察到 `Inflight` 后，才提交结算或取消。执行三轮，每轮提交四个独立 RPC 请求而不逐个等待付款终态，同时 B 向 A 发两笔普通付款；每轮结束等待所有付款和余额收敛再开始下一轮。该负载不约束 P2P 消息顺序，也不声称必然触发 `RemoveWaitPrevAck`。
- 测试使用正常 release 构建，自选原像并独立计算 SHA-256。目标付款的接受、结算/取消在首笔 `Received` 后 30 秒内完成，低于 [release 默认持单超时 120 秒](https://github.com/nervosnetwork/fiber/blob/46ff437800a0743c804ce1a3a5ad4fbd5d3f9974/crates/fiber-lib/src/fiber/config.rs#L73-L77)；超出时该轮失败，避免把自然超时当取消成功。debug 默认仅 20 秒，不直接套用本时间预算。所有 RPC 接受、付款完成和探针等待各有上限；结束后再发一笔正常探针并计入账本，然后由 A 调用 `shutdown_channel(force=true)`。
- **耗时预算**：开启最终通道状态检查时，每个资产变体实测约 11 分钟；默认关闭后在链上结算与到账验证完成时结束，四项实测总耗时 259.62 秒，测试主体各耗时 42–48 秒（另有环境启停时间）。使用各步骤自身的有界等待，不设置进程级 signal 闹钟。两行共四个变体。单次启动/迁移 90 秒、开通/恢复 120 秒、每轮收敛 60 秒；结算后的节点关闭状态预留 660 秒，以覆盖旧版按 300 秒扫描的兼容路径。660 秒是测试等待上限，具体目标构建扫描周期在实施时核对，不按 Watchtower 的短轮询推定。CKB devnet 通过公开出块 RPC 推进承诺锁要求的 epoch；节点自身墙钟超时照常等待。

<!-- TEST-CASES-BEGIN -->
| 用例 | 场景 | 预期结果 | 防止的问题 | 优先级 |
| --- | --- | --- | --- | --- |
| `PR1728-01` | - [x] 两个 v0.9.1 节点建好 Legacy 通道后升级双方；分别用 CKB、UDT，在原通道分三轮把 A→B 已接收的四笔持单并发提交正确原像，同时完成 B→A 普通付款；收敛后探针付款并由 A 强关（每资产默认约 1 分钟；开启最终状态检查时实测约 11 分钟） | 每笔持单 Paid、付款 Success 且原像正确；双方通道余额按实际成功付款净额变化，无遗留在途资金，探针成功；强关承诺和结算交易确认，双方链上到账与独立账本一致，UDT 数量守恒、CKB 仅扣真实链上手续费；开启最终状态检查时双方通道 Closed 且结算等待标记清除 | 已完成付款未计入收款方、金额缺失或重复计账，旧通道升级后付款或兑付卡住 | P0 |
| `PR1728-02` | - [x] 两个 v0.9.1 节点建好 Legacy 通道后升级双方；分别用 CKB、UDT，在原通道分三轮把 A→B 已接收的四笔持单并发取消且始终保密原像，同时完成 B→A 普通付款；收敛后探针付款并由 A 强关（每资产默认约 1 分钟；开启最终状态检查时实测约 11 分钟） | 每笔目标发票 Cancelled、付款 Failed；取消金额全部恢复为 A 可用余额，B 不因取消收款，只有普通成功付款和探针改变净余额；无遗留在途资金，探针成功；双方强关到账与独立账本一致，UDT 数量守恒、CKB 仅扣真实链上手续费；开启最终状态检查时双方通道 Closed 且结算等待标记清除 | 已失败付款仍占发送方资金、错误转给接收方，或退款只在 RPC 中出现而链上少到账 | P0 |
<!-- TEST-CASES-END -->

## 测试树

<!-- TEST-TREE-BEGIN -->
- 旧通道付款移除后的真实资金链路
  - 成功付款计入接收方并可兑付：PR1728-01 [primary] -> SPEC-01
  - 失败付款退回发送方并可兑付：PR1728-02 [primary] -> SPEC-01
  - [not_applicable] 精确固定 RemoveWaitPrevAck 的消息交错 — 依赖控制 CS/RAA 先后顺序，不符合本仓库黑盒长期回归偏好；上游两条新增单测直接覆盖
  - [not_applicable] Legacy 远端承诺 / V2 双向承诺的内部状态排列 — 本 PR 的新增条件不改变这些分支；应在上游补参数化单测，本仓库不复制内部状态夹具
  - [unanalysed] 单侧升级与长期混合版本互通 — 本批固定双方升级；若扩展，先核对目标版本对 v0.9.1 存量通道的重连支持，再设计滚动升级的对端矩阵
  - [unanalysed] 在移除窗口强杀、恢复旧备份、其他通道隔离 — 本批不注入故障；若需扩展，先证明公开运维操作可稳定构造且有独立资金判据，不以命中概率充当覆盖
<!-- TEST-TREE-END -->

## Spec

### SPEC-01：Legacy 付款移除后，本金按成功或失败结果归属并可最终兑付
- Condition: 真实旧节点建立的 Legacy 通道在升级后继续付款；付款被成功结算或明确取消，并最终强关。
- Expected: 成功金额从发送方转给接收方；失败金额保留给发送方；同一金额不遗漏、不重复，链上最终兑付与付款账本一致。
- Observable: 发票与付款终态、双方可用/在途余额、探针付款、已确认的承诺和结算交易、接收地址的 CKB/UDT 净增及通道关闭。
- Basis: [PR 的失败退款与成功付款说明及新增单测](https://github.com/nervosnetwork/fiber/pull/1728/files)、[结算金额计算与承诺输出](https://github.com/nervosnetwork/fiber/blob/46ff437800a0743c804ce1a3a5ad4fbd5d3f9974/crates/fiber-lib/src/fiber/channel.rs#L11357-L11555)。真实升级和链上兑付作为该资金规则的集成推论，交本批人工评审。
- Source: inference
- Testing: required

## 变更依据与上游测试

- 精确条件：出站 TLC 的状态是 `RemoveWaitPrevAck` 时，确认移除原因的判断从 `v2` 改为 `v2 || !for_remote`。因此只有 `!v2 && !for_remote` 新增行为；与资金资产类型无关，CKB 和 UDT 共用这段金额计算。[实现](https://github.com/nervosnetwork/fiber/blob/46ff437800a0743c804ce1a3a5ad4fbd5d3f9974/crates/fiber-lib/src/fiber/channel.rs#L11464-L11541)
- 旧协议的本地承诺在此状态不含该 TLC，远端承诺仍含；V2 两个方向均不含。余额计算必须和承诺内容配套。[TLC 筛选与等待 ACK 转换](https://github.com/nervosnetwork/fiber/blob/46ff437800a0743c804ce1a3a5ad4fbd5d3f9974/crates/fiber-types/src/channel.rs#L785-L861)、[特性位定义](https://github.com/nervosnetwork/fiber/blob/46ff437800a0743c804ce1a3a5ad4fbd5d3f9974/crates/fiber-types/src/channel.rs#L1738-L1780)
- 上游新增两例直接设置 Legacy、余额 800/200、付款金额 300，再经过等待上一轮 ACK 的转换：失败预期 800/200，成功预期 500/500，两者 TLC 集合均为空、余额合计 1000。按旧表达式，两种结果都会把 300 当成仍在途，得到 500/200；这是静态算式推导，未运行旧版对照。
- 上游新增用例没有覆盖 Legacy 远端承诺或 V2 的对照；建议上游将相同状态夹具按协议和承诺方向参数化。它们是本 PR 新增测试的覆盖缺口，不等于已确认产品缺陷，也不宣称整个上游套件缺少这些覆盖。
- PR 作者记录 SQLite feature 下两条聚焦测试通过，以及 fmt/clippy 通过；RocksDB 构建受 Clang 头文件影响。本轮只核对 PR 说明与断言，没有复跑，也没有据此判定本机 RocksDB 环境失败。

## 现有评审与自动化影响

- `H32V2-05` 只有一处映射：`test_cases/fiber/devnet/compatibility/test_full_hash_old_data.py::TestFullHashOldData::test_real_old_stores_start_and_settle_original_legacy_channel`。它从 v0.9.1 真实旧库升级双方、核对原通道身份、继续付款并强关结算，可复用升级与链上核对步骤；当前目标持单由 B 强关后才释放原像，没有覆盖本批 A 本地承诺上的并发移除，也没有 UDT 对应路径。原评审预期不因本 PR 改写。
- `settle_invoice/test_settle_invoice.py::test_cancel_invoice` 检查接收侧在途余额清零且本地余额不变，未验证发送方退款、付款 Failed 或发票 Cancelled；`test_settle_invoice_edge_cases.py::test_settle_after_invoice_cancelled_post_receive` 检查取消后拒绝结算。它们未组合真实旧库升级、并发移除和链上到账，且前者用固定 sleep 代替有界状态等待；可借用公开 RPC 步骤，不直接新增映射冒充覆盖。
- `shutdown_channel/test_force.py::test_in_tx` 发付款后立即强关，当前只断言 Inflight 并保留 todo，没有最终资金判据；`test_force_restart.py` 的关闭请求未带 `force=true`，实际是合作关闭。这些名称不足以证明覆盖本 PR。
- `contract_upgrade_support.py` 的资金断言可参考，但其中部分 CKB 检查用容差；本批须按真实交易费用核对独立账本，不能让容差吞掉一笔小额付款。关闭状态等待受 `FIBER_ASSERT_ONCHAIN_TLC_QUERY` 门控，声称通道最终关闭时须显式开启或提供等价直接断言。
- 本批映射为 2/2；`PR1728-01` 映射到 `test_legacy_settlement_balances.py` 的两个 `test_fulfilled_*` 方法，`PR1728-02` 映射到两个 `test_cancelled_*` 方法，分别覆盖 CKB、UDT。映射和勾选均不代表测试通过；实际执行结果另列。

## 验证设计

- **独立账本**：以开通后的实际出资与基线余额为起点，测试自己记录每个 hash、金额、方向和预定结算/取消操作。令 S_AB、S_BA 为得到成功终态且原像核对正确的付款总额（含探针），则 A 的本金为 A0−S_AB+S_BA，B 为 B0+S_AB−S_BA；取消金额贡献为零。预先指定的成功/失败集合必须逐笔吻合，不把意外失败从成功集合删除后继续判通过。
- **链上观察**：由 A 公开 RPC 强关；查询 funding outpoint 的实际消费、承诺 cell 和全部后续结算输出，逐地址核对资金。CKB 用实际输入输出算矿工费，分别核对收款与费用分配并验证总和；UDT 精确核对 token 数量，CKB 预留及费用另算。没有遗留承诺/TLC cell，才算本金兑付完成。
- **入口与新结果**：真实 RPC `new_invoice/send_payment/get_invoice/get_payment/settle_invoice/cancel_invoice/list_channels/shutdown_channel`，以及生产 CLI 升级、启停和公开 CKB 交易/出块 RPC。等待最新查询结果，不用日志中的内部状态代替付款或资金判定。
- **负例有效性**：取消目标必须已被接收，收款方从未获得原像；取消 RPC 成功、Cancelled 和付款 Failed 均须出现，区别于发送前失败或自然持单超时。同轮反向成功付款和后续探针证明该通道仍能工作。
- **覆盖声明**：本批证明真实节点的升级和资金链路兼容性；无稳定公开入口锁定 ACK 窗口，不要求“base 必失败”作为这两条兼容回归的验收，不将通过解释为精确修复分支覆盖。确定性旧失败/新通过应在上游两条状态单测中验证。

## 设计阶段核验与环境

- 测试仓库 HEAD：`004e5132bbc00e0b1c0c150fd1a6c06b96395a87`；已有修改保留。本地 `fiber/` 为另一个有修改的 `p2p-tap-full-payment-hash` checkout，HEAD `4319344528ded25e9c024a66f6783736387d21a9`，不含 PR head 对象；本次直接通过 GitHub 读取固定 head 的原始源码，没有切换、更新该 checkout。
- `download/fiber/current/fnn --version` → `fnn Fiber v0.10.0-rc1 (7aa1d5d 2026-11-09)`，退出码 0；`download/fiber/0.9.1/fnn --version` → `fnn Fiber v0.9.1 (9a561b3 2026-09-10)`，退出码 0。默认框架路径确实指向 current；版本字符串与合并提交对应，但不代替执行时的构建身份记录。
- 默认 devnet 使用 `source/fiber/data.2026.0929.tar.gz`。`source/contract/fiber/README.md` 记录源码提交 `701d8c8a...`，与链快照里实际部署代码的对应关系本轮未验证；没有运行链节点，不给出已验证合约版本结论。实施时先查询真实 live code/data hash，固定兼容 Legacy 的合约与节点构建身份，再运行用例。
- 结构检查：`python3 /Users/guopenglin/.codex/skills/ai-test-agent-skill/scripts/check_test_design.py --root /Users/guopenglin/PycharmProjects/gp5/fiber-py-integration-test --review reviews/pr1728-legacy-settlement-balances.md` → `cases: 2`，退出码 0；报告 SPEC-01 的集成推论需人工评审，并列出两条明确未分析分支。结构检查不证明行为通过。
- 全仓映射：`python3 scripts/check_test_map.py` → `orphan mappings: PMP-01, PMP-02, PMP-03, PMP-04, PMP-05, PR1677-09, SETTLE-01`，`duplicate review IDs: none`，退出码 1。这七个孤儿在本次编辑前已存在；本批两行尚无映射，符合评审阶段。扫描期间另一个任务增加 PR1705 行，因此不把全仓用例数变化全部归因于本 PR。
- 新文档空白检查通过，新增两条评审行；本轮不将文档检查计作节点行为通过。
- 独立 B 复核：只读子代理 `/root/pr1728_design_review`，以 `fork_turns=none` 的新会话读取原始 PR diff、固定 head 源码和待审文档；未继承主代理对话，不声称主机隔离认证。结论无阻断项；唯一 P2 指出现有取消测试只验证接收侧，已按其真实断言收窄上面的覆盖描述，不改变两条用例。复核另确认目标版本 `network.rs:4806–4841` 支持取消 Received 且未保存原像的发票；实施时须把每方手续费分摊写成独立判据，并为启动/恢复和结算设置有界等待。后续实现已逐方核对手续费并实测完成。

## 首例实现与复现入口

`PR1728-01` 使用 `test_cases/fiber/devnet/compatibility/test_legacy_settlement_balances.py` 的两个方法：`test_fulfilled_legacy_ckb_after_upgrade` 和 `test_fulfilled_legacy_udt_after_upgrade`。两者均按“旧通道付款 → 停机备份与 CLI 迁移 → 三轮持单结算/反向付款 → 探针 → A 强关与链上资金核对”展开，共用实际重复的步骤。每个方法独立创建 Fiber 状态，使用 `tmp-pr1728` 及 27214/27215、27228/27227、27229/27230 端口；端口或目录已有占用时失败，避免覆盖别的测试环境。现有 compatibility CI job 按目录收集，无需修改 CI。

链上核对逐笔验证原 funding cell 的消费、A 的已签承诺 hash、57 字节 Legacy 布局、实际 commitment-lock code dep、两方解锁方向、无残留 TLC、交易费用和钱包净增。CKB 的承诺手续费从最后一方取得的剩余容量中扣除；UDT 的首笔结算释放多余 CKB 预留、末笔释放按脚本字节数独立计算的剩余占用容量，代币数量另按付款账本精确核对。每方结算费来自该交易输入减输出，额外输入必须属于对应收款方；另限制单笔费小于 10,000 Shannon，远小于本批任何付款金额。

环境变量 `FIBER_ASSERT_LEGACY_SETTLEMENT` 默认 `false`，compatibility CI 显式设置为 `false`；默认只查询一次通道快照核对无 pending TLC，不等待节点把状态同步为 `Closed`。仅设为 `true`（忽略大小写）时等待两端 `Closed` 且清除 `WAITING_ONCHAIN_SETTLEMENT`。链上到账、真实手续费、代币数量和无遗留在途资金的断言始终执行。目标提交的 `CHECK_CHANNELS_SHUTDOWN_INTERVAL` 固定为 [300 秒](https://github.com/nervosnetwork/fiber/blob/46ff437800a0743c804ce1a3a5ad4fbd5d3f9974/crates/fiber-lib/src/fiber/network.rs#L192)，最终关闭状态的等待上限为 660 秒。整个最终通道状态轮询均受开关控制，默认路径跳过两次关闭状态扫描。

原生复现命令（仓库根目录）：

```bash
venv/bin/python -u -m pytest test_cases/fiber/devnet/compatibility/test_legacy_settlement_balances.py -v -s -x -o log_cli=false -o log_level=INFO
```

需要额外核对双方最终关闭状态及结算等待标记时：

```bash
FIBER_ASSERT_LEGACY_SETTLEMENT=true venv/bin/python -m pytest test_cases/fiber/devnet/compatibility/test_legacy_settlement_balances.py -v -s
```

运行时查询到实际 commitment-lock 代码 cell 为 `0x157446f77b9468f63ae741dc66e6e987b470eb2e5f66fdde62d7c90f96042202:0`，代码 data hash 为 `0x91c057ff3bf85e43ccd4baad81b9d675d1e982b85d4343e1741ac08b99e85c93`；后续结算交易必须引用该 code dep。本次没有变更链上合约。

## 取消退款实现与复现入口

首例规范确认后，`PR1728-02` 在同一文件新增 `test_cancelled_legacy_ckb_after_upgrade`、`test_cancelled_legacy_udt_after_upgrade`，复用原有升级、账本和逐方链上兑付检查。每轮四笔持单全部到达 Received / Inflight 后，并发发起四次 `cancel_invoice` 和两笔反向正常付款。原像只保留在测试端，取消请求仅传 hash；每笔都要求发票 Cancelled、付款 Failed、错误类型 InvoiceCancelled，且付款方未取得原像。取消金额不进入成功账本，只有两笔反向成功付款影响本轮净余额。每轮操作限定 30 秒，排除将自然超时当作主动取消的结果。

`list_channels.pending_tlcs` 也展示已确认移除的失败记录，列表非空不等于仍占资金。每轮仅允许本轮已逐笔核验取消的 hash 保留为公开 RPC 的 RemoveAckConfirmed 状态，同时两端可用余额必须精确吻合账本、offered/received 在途余额均为零。最后一笔正常探针后仍要求列表为空；强关时再次核对无在途金额、实际结算 witness 无 TLC、每方链上资金到账；双方最终关闭状态由开关控制。成功付款方法使用空的取消集合，保持首例原有的空列表要求。

新增两项的聚焦命令（仓库根目录）：

```bash
venv/bin/python -u -m pytest test_cases/fiber/devnet/compatibility/test_legacy_settlement_balances.py -k cancelled -v -s -x -o log_cli=false -o log_level=INFO
```

回归直接使用上述 pytest 命令，测试不创建 evidence 目录、不复制节点日志或配置，也不使用 signal 闹钟；RPC、状态收敛和链上消费仍使用有界等待。此前成功付款与取消退款各两个资产变体均已通过。本次默认路径使用独立端口 28214/28215、28228/28227、28229/28230 和 `tmp-pr1728-fast-check` 运行真实节点；pytest 收集后仅覆盖测试类的端口与目录配置，四个测试方法和资金断言直接执行当前源码。原生结果为 `4 passed in 259.62s (0:04:19)`，退出码 0；成功付款 CKB/UDT 测试主体分别 44.67/48.00 秒，取消退款分别 42.44/46.55 秒。被测版本仍为 v0.9.1 (9a561b3) → v0.10.0-rc1 (7aa1d5d)。开启最终状态检查的完整路径沿用此前通过结果，本轮未重跑。
