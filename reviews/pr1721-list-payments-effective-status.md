# PR #1721：`list_payments` 按有效付款状态过滤用例评审

范围：按有效付款状态过滤、过滤后的分页、排他游标及结算前后对照；保留 `PR1721-01`–`04`。本批用例及首例实现规范均已确认；四个 ID 已映射到两条原生测试，组合测试复用同一组付款验证 `02`–`04`。

依据：[PR #1721](https://github.com/nervosnetwork/fiber/pull/1721)；2026-11-03 GitHub 快照：base / merge-base 均为 `45ffdac8bd2d56f6e9cd4a01c2b2e4975c3e4397`，head 为 `4c90cf4dff0c1fb27c09b07eb37e63943d4f42ca`，共 2 个提交、3 个改动文件，PR 为 OPEN。替代初次评审的 head `ca1b692f8e9a0c4508fc5cd6c9daeccbd32f31cb`。指定的[评审链接](https://github.com/nervosnetwork/fiber/pull/1721#pullrequestreview-5748277822)是空正文的 APPROVED 记录，未附加测试要求。

测试仓库 HEAD：`6088115aec85f002bfbedd8f3b3d919b6601cec5`。已有未提交内容保留；评审阶段只做静态核对；首例的实际节点版本、链快照身份和运行结果见文末。

## 变更与现有测试影响

- base 在加载 attempts 前按 session 记录的状态过滤，因此记录为 Created、实际为 Inflight 的付款会从 Inflight 列表漏掉，并可能以 Inflight 项出现在 Created 列表。head 在两条 store 查询中按有效状态筛选，分页仍在筛选后应用 `limit`。[完整差异](https://github.com/nervosnetwork/fiber/compare/45ffdac8bd2d56f6e9cd4a01c2b2e4975c3e4397...4c90cf4dff0c1fb27c09b07eb37e63943d4f42ca)
- 最新提交在加载 attempts 前跳过不匹配的持久化终态：Success / Failed 的有效状态保持终态，只有不匹配的终态记录可直接跳过；非终态仍要加载 attempts。该分支减少无关终态记录的读取，不改变匹配集合；它没有取消对候选 session 的扫描，也不构成大历史集的延迟保证。[查询实现](https://github.com/nervosnetwork/fiber/blob/4c90cf4dff0c1fb27c09b07eb37e63943d4f42ca/crates/fiber-lib/src/store/store_impl/mod.rs#L1445-L1494)、[状态计算](https://github.com/nervosnetwork/fiber/blob/4c90cf4dff0c1fb27c09b07eb37e63943d4f42ca/crates/fiber-types/src/payment.rs#L1098-L1116)
- 上游新增 store 用例覆盖两个查询函数、双向非终态分歧、推导出的终态、持久化终态优先、零/小 limit、排他游标、无过滤与不写回；另用不可反序列化的 attempt 哨兵检查“不匹配终态不加载 attempts”。HTTP RPC 用例覆盖 Created / Inflight 的多页结果。本轮只核对这些断言，未重跑上游测试。[store 测试](https://github.com/nervosnetwork/fiber/blob/4c90cf4dff0c1fb27c09b07eb37e63943d4f42ca/crates/fiber-lib/src/store/tests/store.rs)、[HTTP RPC 测试](https://github.com/nervosnetwork/fiber/blob/4c90cf4dff0c1fb27c09b07eb37e63943d4f42ca/crates/fiber-lib/src/fiber/tests/rpc.rs#L35-L133)
- 本仓库已有 `test_cases/fiber/devnet/list_payments/test_list_payment.py::TestListPayments::test_effective_status_filter_pagination_and_settlement`：用 5 个预先排序的 hash 交错安排 Inflight / Success，断言过滤集合、分页、非匹配游标和结算迁移。本轮补齐普通付款的接收端 Paid 等待、最大匹配 hash 的显式空页断言，并映射 `PR1721-02`–`04`；执行结果与映射关系分别记录。
- 旧 `fnn-cli/test_list_payments_rpc.py` 的相关过滤测试主要验证返回项或已完成付款，未明确构造持久化 / 有效状态分歧；原有持单发票测试可复用 setup。`list_payments/` 已加入 `Makefile` 的 `fiber_test_cases` 及 `.github/workflows/fiber.yml` 中现有列表 / 发票测试 job 的显式目录列表。
- 默认 devnet 二进制由 `framework/test_fiber.py` 指向 `download/fiber/channel-v2-nonce/fnn`，路径本身不证明包含本 PR。后续运行须记录实际二进制身份与合约版本，先用修复前后的同一输入验证旧过滤缺陷。

## Spec

### SPEC-01：过滤按有效状态，返回项状态与过滤一致
Condition: 一笔持单付款已由收款方接受，尚未提供原像；其 session 原始记录为 Created、attempt 为 Inflight。
Expected: `list_payments(status=Inflight)` 包含该付款且项状态为 Inflight；`status=Created` 不含它，无其它有效 Created 会话时为空页。
Observable: 付款 hash 的出现 / 缺席、项状态及空页 `last_cursor=None`；`get_payment` 仅作一致性对照。
Basis: [PR 说明](https://github.com/nervosnetwork/fiber/pull/1721)、[按有效状态过滤](https://github.com/nervosnetwork/fiber/blob/4c90cf4dff0c1fb27c09b07eb37e63943d4f42ca/crates/fiber-lib/src/store/store_impl/mod.rs#L1445-L1494)、[持单发票参数](https://github.com/nervosnetwork/fiber/blob/4c90cf4dff0c1fb27c09b07eb37e63943d4f42ca/crates/fiber-json-types/src/invoice.rs#L126-L133)。
Source: explicit
Testing: required

### SPEC-02：过滤后应用 limit，after 是全体会话键的排他游标
Condition: 同一发送方有 3 笔 Inflight 持单和 2 笔 Success 付款；按测试计算的 hash 字节序交错排列。
Expected: `limit` 只计入匹配项；`after` 排除不大于游标的所有 hash，不要求游标本身匹配状态；结果按 hash 升序，无重复和遗漏，非空页游标为末项 hash，空页游标为 None。
Observable: `limit=2` 三页分别为 2 项、1 项、0 项；非匹配 Success 游标之后仍返回应有的 Inflight，最大匹配 hash 之后为空。
Basis: [PR 说明](https://github.com/nervosnetwork/fiber/pull/1721)、[筛选后 take](https://github.com/nervosnetwork/fiber/blob/4c90cf4dff0c1fb27c09b07eb37e63943d4f42ca/crates/fiber-lib/src/store/store_impl/mod.rs#L1467-L1494)、[RPC 游标](https://github.com/nervosnetwork/fiber/blob/4c90cf4dff0c1fb27c09b07eb37e63943d4f42ca/crates/fiber-lib/src/rpc/payment.rs#L316-L346)。
Source: explicit
Testing: required

### SPEC-03：结算迁移与无过滤列表的兼容性对照
Condition: 收款方接受持单后，再用正确原像结算，发票到 Paid，付款到 Success。
Expected: 无过滤列表在结算前后都包含该付款，状态由 Inflight 变为 Success；结算后 Success 列表包含它，Inflight 与 Created 列表均不包含。
Observable: 结算前后的新列表响应，与接收端发票状态及已提交的原像共同对照。
Basis: [发票原像语义](https://github.com/nervosnetwork/fiber/blob/4c90cf4dff0c1fb27c09b07eb37e63943d4f42ca/crates/fiber-json-types/src/invoice.rs#L126-L133)、[既有状态计算](https://github.com/nervosnetwork/fiber/blob/4c90cf4dff0c1fb27c09b07eb37e63943d4f42ca/crates/fiber-types/src/payment.rs#L1098-L1116)、[列表转换](https://github.com/nervosnetwork/fiber/blob/4c90cf4dff0c1fb27c09b07eb37e63943d4f42ca/crates/fiber-lib/src/rpc/payment.rs#L334-L346)。本条为既有行为的兼容性推断，随本批用例确认。
Source: inference
Testing: required

### SPEC-04：查询不回写原始持久化状态
Condition: 对原始记录与有效状态不一致的 session 执行过滤查询。
Expected: `get_persisted_payment_status` 在查询前后仍返回原始记录值。
Observable: 直接读取 store 的原始状态；RPC 列表只返回有效状态，其结果不足以证明写回是否发生。
Basis: [PR 保留 raw 语义且无 write-on-read](https://github.com/nervosnetwork/fiber/pull/1721)、[原始状态读取](https://github.com/nervosnetwork/fiber/blob/4c90cf4dff0c1fb27c09b07eb37e63943d4f42ca/crates/fiber-lib/src/store/store_impl/mod.rs#L1429-L1435)。
Source: explicit
Testing: not_tested — RPC 未暴露原始记录；上游 store 测试含查询后原始状态断言，本仓库本轮仅静态核对。

### SPEC-05：跳过不匹配终态时不读取 attempts
Condition: 原始状态已为 Success 或 Failed，且与请求状态不匹配。
Expected: 两条 store 查询均可在加载 attempts 前跳过该记录；同状态或无过滤查询不按此条件排除它。
Observable: store 查询遇到不可读取的 attempt 哨兵仍正确返回空集合；该读取行为不直接暴露在 RPC 中。
Basis: [终态提前排除分支](https://github.com/nervosnetwork/fiber/blob/4c90cf4dff0c1fb27c09b07eb37e63943d4f42ca/crates/fiber-lib/src/store/store_impl/mod.rs#L1445-L1494)、[上游 sentinel 用例](https://github.com/nervosnetwork/fiber/blob/4c90cf4dff0c1fb27c09b07eb37e63943d4f42ca/crates/fiber-lib/src/store/tests/store.rs)；这是新增实现优化，不是耗时阈值。
Source: observation
Testing: not_tested — 本集成切片只观察列表结果；不加载 attempts 的性质由上游 store 哨兵用例静态覆盖，本轮未执行。

## 测试树

<!-- TEST-TREE-BEGIN -->
- `list_payments` 按有效状态筛选
  - 持单付款与持久化状态不同
    - PR1721-01 [primary] -> SPEC-01：Inflight 包含，Created 排除
  - 混合状态按 hash 交错
    - PR1721-02 [primary] -> SPEC-02：过滤后限额、逐页取尽、空页和末项游标
  - 排他游标
    - PR1721-03 [primary] -> SPEC-02：Success 游标后仍有 Inflight，最大 Inflight 之后为空
  - 结算兼容性
    - PR1721-04 [primary] -> SPEC-03：无过滤与状态列表随结算迁移
  - [not_applicable] RPC 直接验证原始记录未写回 — SPEC-04；原始记录未暴露，核对上游 store 断言，不以 RPC 结果代替证明
  - [not_applicable] RPC 直接验证 attempts 读取被跳过 — SPEC-05；读取次数未暴露，核对上游 store 哨兵用例，不推断延迟
  - [unanalysed] 有效 Created 的稳定构造与反向非终态分歧 — 公开 API 缺少稳定停留点；若扩展本切片，先检查 attempt 插入 / 重试时序；上游 store 和 HTTP RPC 测试已有相应对照
  - [unanalysed] Failed 终态与 after 组合 — 本批复用 Success 作为终态对照；若扩展，先确定失败注入与终态判据，上游 store 已覆盖状态计算
  - [unanalysed] limit 默认 15 / 上界 500、零值及非法 status — 属既有边界，未新增集成用例；扩展时从参数转换及已有测试进入，上游 store 覆盖 limit=0
  - [unanalysed] 大历史集性能、恢复路径及 wasm / CLI 调用方 — 本轮未测量耗时或运行调用方；后续先定义历史规模、attempt 数量及性能目标，再决定性能用例
<!-- TEST-TREE-END -->

## 用例

共同前提：两节点 devnet 通道 Ready，发送方使用隔离的付款历史，余额及通道限额足够；操作完成前保持持单未结算、未取消、未超时。测试自选原像并独立计算 SHA-256，显式使用 `hash_algorithm=sha256`；持单只传 `payment_hash`，普通发票只传 `payment_preimage`，两字段互斥。金额与 expiry 保证五笔付款及查询可完成，所有等待有上限；实际被测版本支持的 expiry 下限在运行前核对。

<!-- TEST-CASES-BEGIN -->
| 用例 | 场景 | 预期结果 | 防止的问题 | 优先级 |
| --- | --- | --- | --- | --- |
| `PR1721-01` | - [x] 双节点通道 Ready；fiber2 用测试自选 preimage（`hash_algorithm=sha256`，`payment_hash`=sha256(preimage)）建持单发票，fiber1 付款并等到 fiber2 发票 `Received`、fiber1 `get_payment` 为 `Inflight` 后，分别调用 `list_payments(status=Inflight)` 与 `list_payments(status=Created)`（limit 足够大） | Inflight 页包含该 `payment_hash` 且返回项 `status=Inflight`、`last_cursor` 为本页最后 hash；Created 页不包含该 hash（无其它有效 Created 会话时 `payments` 为空、`last_cursor=None`）；两次结果与 `get_payment` 的有效状态一致 | 过滤仍用持久化 Created，使在途付款从 Inflight 列表消失并以伪 Created 出现在 Created 列表 | P0 |
| `PR1721-02` | - [x] 同一通道使用 5 个自算且互异的 hash，按字节序设 `h1<h2<h3<h4<h5`；h1/h3/h5 建持单付款至 Received / Inflight，h2/h4 建普通付款至 Paid / Success；以 `status=Inflight, limit=2` 从无 after 开始，用上页游标连续查询 3 页 | 三页 hash 依次为 [h1,h3]、[h5]、[]，项状态均为 Inflight；游标依次为 h3、h5、None，分页并集恰为三笔持单且无重复，夹在其中的 Success 不占 limit | 先限额再过滤导致短页，或跳过终态与游标组合造成漏项 / 重复 | P0 |
| `PR1721-03` | - [x] 同一通道有按自算 hash 字节序排列的 `h1<h2<h3<h4<h5`，h1/h3/h5 为已接收未结算的 Inflight 持单、h2/h4 为 Paid / Success 普通付款；分别以 `status=Inflight, limit=2, after=h4` 和 `after=h5` 查询 | after=h4 恰好返回 [h5]，状态 Inflight、游标 h5；after=h5 返回 []、游标 None；不要求 Success 游标属于过滤结果集 | 把 after 限定在匹配集合、忽略 after 或包含游标自身导致缺项 / 多项 | P1 |
| `PR1721-04` | - [x] 单笔持单付款处于 Inflight 时，调用无 `status` 的 `list_payments`；随后 fiber2 `settle_invoice` 用正确原像结算，等 fiber2 发票为 `Paid`、fiber1 `get_payment` 为 Success，再依次调用无过滤、`status=Inflight`、`status=Success`、`status=Created` 的列表 | 结算前无过滤列表含该 hash 且 `status=Inflight`；结算后无过滤与 Success 列表含该 hash 且 `status=Success`，Inflight 与 Created 列表都不含它 | 修复把终态会话错误排除，或读取缓存导致结算后列表状态不再变化 | P2 |
<!-- TEST-CASES-END -->

## 验证设计

- 独立预期：hash 与排序来自测试自己的原像和 SHA-256；期望集合来自预先指定的持单 / 普通付款分组。收款端 Received 且原像尚未提交建立持单前提，Paid 与正确原像结算建立成功前提。`get_payment` 与列表共用 `init_attempts/calc_payment_status`，因此只能用于等待和一致性对照，其一致性本身不证明状态计算正确。
- 实际入口：真实节点 HTTP JSON-RPC 的 `new_invoice → send_payment → list_payments`，随后 `settle_invoice` 并重新查询；观察付款 hash 集合、项状态、页游标及接收端发票状态。
- 缺陷敏感性：修复前先确认“接收端 Received / 发送端 Inflight，但 Created 过滤返回同一项”的分歧确实出现；未建立此输入时，后续通过不证明原缺陷已覆盖。首例已用相同原像和断言完成旧构建失败 / 目标 head 通过的对照；旧构建为既有故障版本 `eaff617`，并非 PR 的精确 base，完整身份见运行记录。
- 自动化对应：`01` 使用单笔持单；`02`–`04` 复用组合测试。五个原像在测试中固定生成并独立排序；普通付款明确等待 Paid / Success，持单等待 Received / Inflight；分页、非匹配与最大匹配游标、正确原像结算分别有直接断言。
- 证明边界：RPC 集合正确性不证明 raw 未回写、终态被跳过时没有加载 attempts、恢复路径或性能目标。旧报告目录只表示曾有运行数据，本轮未将其计为通过。

## 自动化与验证（2026-11-03）

本批四个用例及首例实现规范均已确认，映射为 4/4：`01` 使用单笔持单测试，`02`–`04` 复用同一组交错付款的组合测试。独立设计复核发现的接收端 Paid 观察已进入用例和断言；本轮未改变已确认的预期。

- 完整测试文件在目标构建 `4c90cf4dff0c1fb27c09b07eb37e63943d4f42ca` 上执行：`2 passed in 77.01s`，退出码 0，无跳过；两个原生测试对应四个 Case ID，不把原生测试数写成四次独立执行。已覆盖有效状态过滤、2/1/0 分页、Success 游标后匹配项、最大匹配 hash 后空页及正确原像结算后的状态迁移。
- 保留首例的修复前对照：既有故障构建 `eaff6171366c109a12a5fe844c247c0b31c58f71` 在 Inflight 集合断言失败，`1 failed in 67.89s`，退出码 1，日志显示 Created 返回同一笔 Inflight 付款。它是已知故障版本，并非 PR 的精确 base；本轮没有将这次负对照扩称为所有分页 / 结算行为都已做旧版验证。
- 实际运行入口为 `.build/pr1721-case01/run_case.py --case all`，显式选择 `download/fiber/list-payments-effective-status/fnn` 后调用原生 pytest。隔离端口为 CKB 18114/18125、Fiber 18228/18227 与 18229/18230；运行完成后这些端口已释放。常规 Makefile / CI 入口仍使用环境自身的 `CURRENT_DEV` 配置。
- FNN 二进制 SHA-256：`584801b9d44e4ff293086529778a5507c825cfb4792733c1c45f26f0369b45c0`；CKB `0.202.0 (d0a6c95)`。合约链快照 `source/fiber/data.2026.0929.tar.gz` 的 SHA-256：`40a6278278b448bf6f2e3f7992e997c5a544345313743d5e19ad7ace5244ff81`。实际配置、日志与完整身份记录保存在本次回执目录。
- 完整批次回执：`reports/pr1721-suite-head/execution.json`，包含实际命令、JUnit 结果、日志哈希和运行期间输入未漂移的检查；旧版首例回执为 `reports/pr1721-case01-old/execution.json`。最终测试源码与通过的回执哈希一致；运行后辅助脚本仅做 Black 格式调整，保留了执行时副本并核对 AST 一致。
- `list_payments/` 已接入 Makefile 和现有 GitHub Actions job；pytest 收集到两条测试，Makefile dry-run、CI YAML 及修改文件的 Black 检查通过。本轮未触发远程 CI、重跑上游 Rust 套件或扩展到未分析分支。
- 全仓映射检查 `python3 scripts/check_test_map.py` 返回 1：仍有 `PMP-01`–`05`、`PR1677-09`、`SETTLE-01` 七个既有孤儿 ID。本批四个 ID 均有且仅有一处映射，复选框一致，未新增孤儿或重复评审 ID。
