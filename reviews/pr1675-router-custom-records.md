# PR #1675：手动路由付款的 custom_records 用例评审

评审范围：`build_router` 产生的显式路由交给 `send_payment_with_router` 后，用户自定义记录在付款构建、查询、末跳交付及输入校验中的行为。用户已准许按本批行集编写自动化，并要求 `PR1675-04/07` 先测试、记录实际结果；其产品契约决定仍单列。普通 `send_payment`、路由算法、重试/MPP 与接收端公开查询接口不是本批行为。

源码版本：`nervosnetwork/fiber` PR [#1675](https://github.com/nervosnetwork/fiber/pull/1675)，base 与 merge-base 均为 `ae6f7d3440c23025385537c1519d2af607f3b6a0`，head 为 `d54e9b4707fbee755ec9ef7cf77ca30abc3817c3`（已合并）。测试设计起点 `f5969114ce9c4e7283119dcdc70ece9604b84ad7`；本次提交分支基于开放 PR #110 的 `v0.10.0` head `79c3503f09d967d7edff734b623eef9bb6003b90`；本地 `fiber/` 工作树位于另一分支且有未提交改动，本轮仅按上述 Git 对象读取，不切换工作树。

原始材料：`.ai-test-agent/current/pr1675-custom-records/inputs/` 内的 `pr.json`、`issue-1380.json` 和 `product.diff`。需求线索为 [#1380](https://github.com/nervosnetwork/fiber/issues/1380)；旧版及新版源码通过固定提交读取。

## 变更说明

- PR 声明：手动路由付款的 `custom_records` 曾在 `SendPaymentWithRouterCommand → SendPaymentCommand` 转换时丢失，导致末跳收不到、发送端查询为 `None`，超限记录也绕过校验。
- 实际代码：只在中间命令结构体赋值 `custom_records: self.custom_records`；原有 `SendPaymentData::new` 随后执行用户键范围与 Molecule **编码总大小**校验，并把记录放进响应及末跳 onion。新增 Rust 三节点测试覆盖多跳 keysend 交付及明显超限拒绝。见 [改动](https://github.com/nervosnetwork/fiber/commit/d54e9b4707fbee755ec9ef7cf77ca30abc3817c3) 和 `product.diff`。
- 前后可观察行为：修复前 RPC 接收字段，但付款响应/发送端 `get_payment` 为 `null`、末跳无记录；超限请求可能启动付款。修复后保留记录，并在开始付款前拒绝校验失败的输入。
- 直接影响：keysend 与发票付款共用该转换；`dry_run` 也先经过同一转换。间接保持：未传记录的手动路由付款以及普通 `send_payment` 不应因本次改动退化。
- 现有证据边界：新增 Rust 测试直接读取末跳内部记录；本测试仓库公开 RPC 客户端目前仅能从发送端 `get_payment` 看见字段，不能据此单独证明末跳交付。`SendPaymentWithRouterParams` 文档写“值长度合计 ≤2048”，实际实现按编码大小 ≤2048，且普通付款现有测试已按编码大小写；此差异需产品确认。2026-10-08 使用的 FNN 为 `v0.10.0-rc1 (2ba4b25)`，该提交包含 PR head；本仓库公开 RPC 聚焦测试已运行 7 条，仍未验证 C 端末跳记录。

## Spec

### SPEC-01：多跳 keysend 记录贯通
Condition: A、B、C 两跳通道 Ready；A 用 `build_router` 获取 A→B→C 路由，`keysend=true`，提交两个合法用户记录。
Expected: 付款成功；末跳 C 实际收到相同记录。
Observable: A 的付款终态与 C 的内部末跳记录观察点；A 的回显本身不证明 C 收到。
Basis: [issue #1380](https://github.com/nervosnetwork/fiber/issues/1380)、[PR Rust 回归](https://github.com/nervosnetwork/fiber/blob/d54e9b4707fbee755ec9ef7cf77ca30abc3817c3/crates/fiber-lib/src/fiber/tests/payment.rs#L708-L763)、[响应转换](https://github.com/nervosnetwork/fiber/blob/d54e9b4707fbee755ec9ef7cf77ca30abc3817c3/crates/fiber-lib/src/fiber/payment.rs#L634-L666)。
Source: explicit
Testing: required

### SPEC-02：发票付款沿用记录贯通
Condition: C 提供有效未过期且不允许 MPP 的发票，A 用显式 A→B→C 路由与合法记录付款，`keysend=false`。
Expected: 发票付款成功且 C 发票 Paid；末跳 C 收到相同用户记录。
Observable: 付款与发票状态、C 的内部末跳记录观察点。
Basis: [同一 RPC→命令转换](https://github.com/nervosnetwork/fiber/blob/d54e9b4707fbee755ec9ef7cf77ca30abc3817c3/crates/fiber-lib/src/rpc/payment.rs#L281-L312)、[同一 SendPaymentData 构建路径](https://github.com/nervosnetwork/fiber/blob/d54e9b4707fbee755ec9ef7cf77ca30abc3817c3/crates/fiber-lib/src/fiber/payment.rs#L761-L793)；PR 自带测试只跑 keysend，发票预期是候选回归要求。
Source: inference
Testing: required

### SPEC-03：编码上限内记录可用
Condition: 手动路由 keysend，单条记录的 Molecule 编码总大小恰为 2048 字节（按现有测试计算，值长 2012 字节）。
Expected: 不因 `custom_records` 大小被拒，付款成功且发送端记录不丢失。
Observable: RPC 结果、付款终态与发送端 `get_payment`。
Basis: [校验条件为 encoded_size > 2048](https://github.com/nervosnetwork/fiber/blob/d54e9b4707fbee755ec9ef7cf77ca30abc3817c3/crates/fiber-lib/src/fiber/types.rs#L88-L102)、现有 `test_custom_records_encoded_size_under_limit_succeeds` 的 36 字节编码开销注释；精确边界未见现成端到端测试。
Source: inference
Testing: required

### SPEC-04：编码超限在付款前拒绝
Condition: 手动路由 keysend，单条记录值长 2013 字节、Molecule 编码总大小 2049 字节；分别以正常发送和 `dry_run` 输入。
Expected: 待确认：对外上限采用值长度还是 Molecule 编码大小；当前实现两种调用均拒绝，且不建立付款会话或在途 TLC。
Observable: RPC 结果、发送端 `list_payments` 前后差异、通道 TLC/余额；拒绝时随机生成的哈希不返回调用方。
Basis: [大小校验](https://github.com/nervosnetwork/fiber/blob/d54e9b4707fbee755ec9ef7cf77ca30abc3817c3/crates/fiber-lib/src/fiber/types.rs#L88-L102)、[构建发生于启动 PaymentActor 前](https://github.com/nervosnetwork/fiber/blob/d54e9b4707fbee755ec9ef7cf77ca30abc3817c3/crates/fiber-lib/src/fiber/network.rs#L3253-L3271)、[PR Rust 回归](https://github.com/nervosnetwork/fiber/blob/d54e9b4707fbee755ec9ef7cf77ca30abc3817c3/crates/fiber-lib/src/fiber/tests/payment.rs#L764-L781)。
Source: observation
Testing: required

### SPEC-05：用户记录键范围
Condition: 手动路由付款提交用户键 `0x10000`（超出 0～65535），记录值本身很小。
Expected: RPC 拒绝输入，付款未开始，现有通道/付款不受影响。
Observable: RPC 错误、隔离拓扑中 `list_payments` 前后不变及通道 TLC/余额不变。
Basis: [用户键上限](https://github.com/nervosnetwork/fiber/blob/d54e9b4707fbee755ec9ef7cf77ca30abc3817c3/crates/fiber-types/src/payment.rs#L41-L47)、[SendPaymentData::new 的键校验](https://github.com/nervosnetwork/fiber/blob/d54e9b4707fbee755ec9ef7cf77ca30abc3817c3/crates/fiber-lib/src/fiber/payment.rs#L523-L534)；PR 未直接测试。
Source: observation
Testing: required

### SPEC-06：省略记录兼容性
Condition: 手动路由 keysend 不传 `custom_records`。
Expected: 付款照常成功；响应与发送端 `get_payment.custom_records` 为 `null`。
Observable: 付款终态与发送端响应/查询。
Basis: [字段是 Option，命令默认 None](https://github.com/nervosnetwork/fiber/blob/d54e9b4707fbee755ec9ef7cf77ca30abc3817c3/crates/fiber-lib/src/fiber/payment.rs#L724-L759)、本仓库已有 `test_base_send_payment_with_router` 以 `None` 发送但未检查该字段。
Source: observation
Testing: required

### SPEC-07：空记录与省略区分
Condition: 手动路由 keysend 显式传 `custom_records={}`。
Expected: 待确认：显式空对象是否应与省略字段区分；当前实现付款成功，响应与发送端查询保留空对象。
Observable: 付款终态与发送端响应/查询。
Basis: [JSON 字段是 Option map](https://github.com/nervosnetwork/fiber/blob/d54e9b4707fbee755ec9ef7cf77ca30abc3817c3/crates/fiber-json-types/src/payment.rs#L134-L139)、[响应按 Option 转换](https://github.com/nervosnetwork/fiber/blob/d54e9b4707fbee755ec9ef7cf77ca30abc3817c3/crates/fiber-lib/src/rpc/payment.rs#L141-L158)、普通付款现有空字典测试；此处是否需要保证空对象与缺省不同，待确认。
Source: inference
Testing: required

### SPEC-08：合法记录的 dry_run 不发送
Condition: 手动路由 keysend，`dry_run=true`，提交合法记录。
Expected: 模拟结果包含原记录和付款哈希，但不持久化付款会话、不发送 TLC，通道余额不变。
Observable: dry_run 响应、随后按返回哈希 `get_payment` 无该会话、通道余额；`routers` 只在 debug 构建出现，不作为通用断言。
Basis: [dry_run 路径仅建路由，不存会话/发 onion](https://github.com/nervosnetwork/fiber/blob/d54e9b4707fbee755ec9ef7cf77ca30abc3817c3/crates/fiber-lib/src/fiber/payment.rs#L2207-L2225)；记录回显来自同一响应转换。
Source: observation
Testing: required

### SPEC-09：发送端查询回显
Condition: A、B、C 两跳通道 Ready；A 用显式路由 keysend，提交两个合法用户记录。
Expected: 付款成功，A 的发送响应及之后的 `get_payment` 均保留相同键值。
Observable: A 的 RPC 响应、付款终态与查询；此规则不以 A 回显替代 C 末跳交付验证。
Basis: [issue #1380](https://github.com/nervosnetwork/fiber/issues/1380)、[会话响应复制记录](https://github.com/nervosnetwork/fiber/blob/d54e9b4707fbee755ec9ef7cf77ca30abc3817c3/crates/fiber-lib/src/fiber/payment.rs#L634-L666)、[RPC JSON 转换](https://github.com/nervosnetwork/fiber/blob/d54e9b4707fbee755ec9ef7cf77ca30abc3817c3/crates/fiber-lib/src/rpc/payment.rs#L141-L158)。
Source: explicit
Testing: required

## 测试树

<!-- TEST-TREE-BEGIN -->
- 手动路由付款的用户记录
  - 构建有效付款并传递记录
    - 多跳 keysend
      - PR1675-01 [primary] -> SPEC-01：两条记录经中继到末跳，C 实际收到
      - PR1675-09 [primary] -> SPEC-09：同一路径下 A 响应与付款查询保留记录
    - 多跳发票
      - PR1675-02 [primary] -> SPEC-02：非 MPP 发票结算后，C 末跳收到记录
    - 编码尺寸上界
      - PR1675-03 [primary] -> SPEC-03：编码恰好 2048 字节仍被接受
  - 构建阶段拒绝错误记录
    - 尺寸越界
      - PR1675-04 [primary] -> SPEC-04：2013 字节值对应编码 2049，正常发送/dry_run 的大小契约待确认
    - 用户键越界
      - PR1675-05 [primary] -> SPEC-05：`0x10000` 拒绝且无付款副作用
  - 不携带有效载荷时的兼容语义
    - 未提供字段
      - PR1675-06 [primary] -> SPEC-06：付款成功且记录为 null
    - 显式空对象
      - PR1675-07 [primary] -> SPEC-07：付款成功且保留空对象
  - 只检查不发送
    - 合法记录 + dry_run
      - PR1675-08 [primary] -> SPEC-08：回显记录但不创建付款
  - [not_applicable] 普通 `send_payment` — PR 未改该路径；现有 `test_custom_records.py` 可作为对照，不为此 PR 重写。
  - [unanalysed] MPP/Trampoline 对记录的附加处理 — 本切片只审显式路由普通付款；后续从 `SendPaymentData::new` 的 `payment_secret` 合并及 onion 构造入口另行分析。
<!-- TEST-TREE-END -->

## 待评审用例

<!-- TEST-CASES-BEGIN -->
| 用例 | 场景 | 预期结果 | 防止的问题 | 优先级 |
| --- | --- | --- | --- | --- |
| `PR1675-01` | - [ ] A→B→C 通道 Ready；A 构建两跳路由，用 `keysend=true` 与两个合法 `custom_records` 付款 | 付款 Success；C 末跳实际收到相同记录 | 只在 A 端回显记录却未进 onion，或在中继后丢失 | P0 |
| `PR1675-02` | - [ ] C 有有效未过期、非 MPP 发票；A 构建 A→B→C 路由并带合法记录支付该发票 | 付款 Success、发票 Paid；C 末跳收到相同用户记录 | 修复只覆盖 keysend，发票路径继续丢记录 | P1 |
| `PR1675-03` | - [x] A 构建有效路由，用单条用户记录使 Molecule 编码总大小恰为 2048 字节后 keysend | 不因记录大小被拒；付款 Success，A 查询得到原记录 | 将恰好 2048 的编码上界误拒绝，或接受后未保留记录 | P1 |
| `PR1675-04` | - [x] A 用有效路由提交单条值长 2013 字节、编码总大小 2049 字节的记录，分别正常发送与 `dry_run` | 待确认：对外上限是值长度还是编码大小；按当前编码校验两种调用均拒绝，`list_payments` 及通道 TLC/余额不变 | 口径分歧使边界输入被误接受或误拒绝 | P1 |
| `PR1675-05` | - [x] A 用有效路由提交小尺寸记录但用户键为 `0x10000` | RPC 拒绝；`list_payments` 及通道 TLC/余额不变 | 记录转发后把保留给内部用途的键当用户键发送 | P1 |
| `PR1675-06` | - [x] A 用有效路由 keysend，省略 `custom_records` | 付款 Success；响应和 `get_payment.custom_records` 为 `null` | 修复使不带记录的既有调用失效或伪造记录 | P1 |
| `PR1675-07` | - [x] A 用有效路由 keysend，显式传 `custom_records={}` | 待确认：空对象是否应与省略区分；当前实现付款 Success，响应和 `get_payment.custom_records` 均为空对象 | 空对象被错误折叠为未传字段 | P2 |
| `PR1675-08` | - [x] A 用有效路由及合法记录调用 `send_payment_with_router`，设置 `dry_run=true` | 模拟结果保留记录和付款哈希；按哈希查询无付款会话，不发送 TLC，通道余额不变 | dry_run 错误实际发送付款，或丢弃模拟结果中的记录 | P2 |
| `PR1675-09` | - [x] A→B→C 通道 Ready；A 构建两跳路由，用 `keysend=true` 与两个合法 `custom_records` 付款 | 付款 Success；A 的发送响应及 `get_payment` 均与原记录一致 | 构建时保留记录但响应/查询仍显示 `null` | P1 |
<!-- TEST-CASES-END -->

## 自动化落点与观察边界

- `PR1675-03`～`PR1675-09` 的公开 RPC 断言放在 `test_cases/fiber/devnet/send_payment_with_router/test_router_custom_records.py`，同一 `SharedFiberTest` 类复用 A→B→C 环境；映射只代表测试存在。
- 该文件按 `send_payment_with_router` 接口归档；Makefile 与 CI 显式收集此文件，不连带运行目录内其余历史测试。
- `PR1675-01` 的 C 端末跳记录已有产品 Rust 内部回归，但本仓库没有对应公开 RPC 观察点；`PR1675-02` 的非 MPP 发票末跳记录尚无同等证明。两行保持未映射。
- `PR1675-04/07` 的自动化按当前实现表征行为；运行记录放在表外，单次测试通过不把待确认项改写为产品契约。
- 本轮实际输出：`PR1675-04` 的正常调用与 `dry_run` 均报 `custom_records encoded size 2049 exceeds limit 2048 bytes`；`PR1675-07` 的响应及随后查询均为 `{}`。完整命令与退出码见 `reports/pr1675-router-custom-records/coverage.md`。

## 本轮需要确认

- 接收方没有公开的 `get_payment_custom_records` RPC；`PR1675-01/02` 的“C 实际收到”需沿用产品 Rust 内部断言、增加可观测测试钩子，或明确仍未覆盖。`PR1675-09` 单独验证 A 端回显，不能替代前两行。
- `PR1675-04` 的输入用于区分 **Molecule 编码大小**与 RPC 注释中的“值长度合计”，请决定对外契约及文档；`PR1675-03` 的精确 2048 正边界在两种口径下都应被接受。
- `PR1675-02` 的非 MPP 发票末跳行为仍缺观察点；`PR1675-07` 已按当前实现完成表征测试，但空对象与省略字段是否构成对外契约仍待确认。MPP/Trampoline 未分析，不把本轮当作整个付款模块的验收。

## 独立设计复核

采用无继承对话的只读 B 复核原始 PR/issue/diff、产品 Git 对象及本评审；对宿主隐藏状态的隔离不作额外保证。设计阶段 B 的六项意见均已采纳：拆分 A 回显与 C 交付；标明 C 端公开 RPC 观测缺口；发票限定非 MPP；用 2013 字节值暴露大小口径分歧；移除 release 构建不保证的 `routers` 断言；拒绝请求改用前后 `list_payments`/TLC/余额观察。用户随后准许本批自动化；B 复核与运行通过均不替代上节保留的产品契约决定。
