# 强关付款超时：钱退回之后，原通道能否结束等待？

## 导读

付款退款和通道结清是两件事。旧节点在链上已确定付款失败、自己却找不到对应付款记录时，会一遍遍处理同一笔款，通道一直显示“等待链上结算”。新版允许这条通道收尾；它没有改变谁收退款，也没有把所有查不到的付款判为失败。

本仓能用正常节点验证两件用户看得见的事：剩余资金领完后，原通道是否结束等待；旧通道收尾是否会改坏同一付款哈希在另一条通道上的新结果。这是**相邻行为的黑盒回归**，不要求基准版本必失败。直接命中本次修复，还需要证明原通道有那笔待处理的付款、而付款方只缺相应记录。正常重启保留记录，整库恢复同时回退通道和付款；目前没有证实可稳定制造这种组合的公开操作。

依据：[PR #1753](https://github.com/nervosnetwork/fiber/pull/1753)，[实际比较 `21c4dfbd → 37a19a32`](https://github.com/nervosnetwork/fiber/compare/21c4dfbdbd48ca3eac55332c9f1cc3e241b1582e...37a19a3230a105b9cc3a64d16b5c05cfc2c6ec92)。改动在付款方节点，不涉及合约或数据库迁移，不以升级为前提。下列为回归预期；时间预算不代表实际运行耗时。

## 需要你决定

无。两条均纳入本仓回归范围：退款到账和原通道结清分别判断；旧付款已失败、已退款且没有在途付款工作时，支持同一付款方支付另一收款方新开的同哈希发票。

## 用例

**构造退款和未领余额。** 隔离的 CKB devnet，使用正常 release FNN（归因时用上述固定提交）、新建 V2 CKB 通道、各自内置 Watchtower，无代离线节点领取的第三方塔。付款方 A 出资 1,000 CKB 与原收款方 B 开公开通道，先用另一哈希付清 200 CKB，使 B 有明确余额。记录此时双方余额、储备、收款锁、通道 ID 和 funding outpoint。B 再开 100 CKB 持单，原像只在测试端；A 付款，确认 B 发票 `Received`、该哈希两端 TLC 已承诺，记录其金额、方向和实际到期时刻 E。停 B，让 A 主动强关一次。正常推进 devnet，沿原 funding 等到**这笔 TLC 的无原像超时消费已确认、钱退到 A、A 查原付款为 Failed 且无原像**；B 保持离线，从链上确认它的余额仍在原承诺派生的 live cell 中。

**观察和时间。** 查通道用 A 的 `list_channels(include_closed=true)` 精确取原 ID；同时核对原承诺确实消费该 funding。“仍在等待”是找到该行且 `Closed` 带 `WAITING_ONCHAIN_SETTLEMENT`；“结束等待”是找到同一行、仍为 `Closed`、标志已清。每次都重新查 RPC 和已确认链数据。以下为设计预算，不是实测或协议保证：

- 准备约 5 分钟；`W退款` 等实际 E，再给退款上链 30 分钟、付款结果更新 420 秒。原持单最终期限差 3 小时，路由还可加时，总期限上限 48 小时。PSET-01 和 PSET-02 各在独立 devnet 中，先确认原 funding 已消费，再把 CKB 和三个 FNN 共用的业务时钟推进到 E 后，产块并等链上中位时间到 E；此后仍须看到实际退款。两项都不调主机时钟。
- `W收尾` 从恢复 B 起给剩余资金上链 30 分钟，**从最后一笔相关承诺输出已消费起**另给通道状态 660 秒。正式构建的通道巡检/关停巡检分别为 [60/300 秒](https://github.com/nervosnetwork/fiber/blob/37a19a3230a105b9cc3a64d16b5c05cfc2c6ec92/crates/fiber-lib/src/fiber/network.rs#L188-L195)。
- 每次重启最多 120 秒。新发票有效期设 24 小时，新付款业务 timeout 设 1 小时，确认新 TLC 的实际到期仍在整个观察窗之外。发起→Received、提交原像→Success 各限 120 秒，中间故意不结算的 130 秒按真实单调时钟观察，不用时钟跳跃替代节点巡检。构造没有达到、查不到原记录或超时，均报出具体阶段，不记通过。

<!-- TEST-CASES-BEGIN -->
| 用例 | 场景 | 预期结果 | 防止的问题 | 优先级 |
| --- | --- | --- | --- | --- |
| `PSET-01` | - [x] A 强关对 B 的无原像持单；确认 A 已退款、B 的余额仍在链上待领，原目录重启 A，再恢复 B 领完余额（约 5 分钟＋W退款＋W收尾＋2 分钟） | B 的余额仍待领时，A 重启前后原通道均显示等待；双方按原账本到账且最后的承诺输出已消费后，A 仍能查到该通道，状态为 Closed 且不再等待 | 把单方退款误作整条通道结清，或链上已结清而付款方一直显示等待 | P1 |
| `PSET-02` | - [x] A 对 B 的旧付款已失败且退款，B 仍离线有余额待领；A 原目录重启，经另一首跳向 C 支付同哈希持单，保持在途 130 秒后结算，再恢复 B 收尾（约 5 分钟＋W退款＋W收尾，另留 10 分钟） | 同哈希新付款获准，给出原像前保持 Inflight；结算及旧通道收尾后仍为 Success、原像匹配，C 只收一次 100 CKB，B 不收到旧持单的 100 CKB | 旧通道的超时将新付款误报失败，或状态虽成功但漏付、重付 | P1 |
<!-- TEST-CASES-END -->

第二项可独立构造同一退款窗口。另建 A 单方出资 1,000 CKB 的公开直连 A–C，零路由费；两条通道的 funding 不同，B 与 C 不互联。先确认旧目标 TLC 已不可逆退款、A 重启后仍报 Failed，再让 C 用相同哈希、算法和金额开**新发票**，A 仅提交一次。由 C 的 `Received` 和 A–C 两端该笔 TLC 已承诺确认新付款真正开始；以 B 仍离线的 130 秒观察窗重复查在途结果，再向 C 提交预留的正确原像。记录新付款 Success 后才恢复 B；原通道完成后，再查付款状态、原像、C 的 Paid 发票及 A–C 两端余额。

这里保留的是 **B 的未领余额，不是尚未处理的旧付款**。在仍等待结算的原通道上，若旧尝试还持有那笔 TLC，节点会将结果显示为在途并拒绝同哈希重付；新请求获准已排除了这个前提。130 秒只用来观察新款是否稳定，不证明旧失败事件曾重放、编号曾复用或本 PR 的缺记录分支已运行。[通道如何认领付款工作](https://github.com/nervosnetwork/fiber/blob/37a19a3230a105b9cc3a64d16b5c05cfc2c6ec92/crates/fiber-lib/src/fiber/channel.rs#L12160-L12192)；[该状态如何对外显示](https://github.com/nervosnetwork/fiber/blob/37a19a3230a105b9cc3a64d16b5c05cfc2c6ec92/crates/fiber-lib/src/fiber/payment.rs#L640-L657)。

## 测试树

<!-- TEST-TREE-BEGIN -->
- 付款方强关：区分付款结果和原通道结清
  - 本地付款记录仍在（正常节点可构造）
    - A 已退款，B 有待领余额 → 原通道重启前后仍等待；B 领完 → 原通道结束等待：PSET-01 [primary] -> SPEC-02
    - 同一付款方、同一哈希、不同首跳 → 新持单跨过正常维护窗口并结算，旧通道随后结清不改新结果：PSET-02 [primary] -> SPEC-03
  - 本地记录缺失（PR 的直接变化）
    - [not_applicable] 两种记录都缺 — 不符合本仓用例偏好：上游已用固定状态覆盖；没有能保留原 TLC 同时稳定清除两种付款记录的公开构造。
    - [not_applicable] 只缺付款会话、尝试仍在 — 不符合本仓用例偏好：没有选择性删除会话的公开路径；这是上游最应单独补验的分支。
    - [not_applicable] 会话仍在、只缺目标尝试 — 不符合本仓用例偏好：失败重付会清理旧的未成功尝试，但旧 TLC 仍有待处理结果不是重付所保证的前置条件；应在上游独立核对其他尝试未被改写。
  - [not_applicable] 其他失败证据：已确认快照排除该 TLC、或匹配的链上记录含无效原像 — 后者不依赖到期；前者不要求真实超时。两者都不是本轮普通 V2 持单构造的链上退款。
  - [not_applicable] 手工 TLC 本来没有尝试编号，与编号存在但记录消失不同；缺 funding 定位仍要等，缺少匹配的链上证据也不确认失败 — 这些不是正常持单可用来替代的前置状态。
<!-- TEST-TREE-END -->

## Spec

### SPEC-01：付款记录缺失不应让一笔已证实失败的原通道付款阻止收尾
- Condition: 付款方有原通道、原 TLC 及匹配的强关失败证据，能定位 funding，尝试编号存在，但找不到该尝试或付款会话。[失败证据入口](https://github.com/nervosnetwork/fiber/blob/37a19a3230a105b9cc3a64d16b5c05cfc2c6ec92/crates/fiber-lib/src/fiber/onchain_tlc_reconcile.rs#L599-L666)还接受已确认的强关快照明确排除的本地 TLC，以及链上记录中原像校验失败的情况（后者不要求到期）。本轮只用正常链上超时退款，不以“到了时间”代替证据。
- Expected: 确认这笔原通道的失败，其他结算义务完成后通道可收尾。仅会话缺失时不凭空重建付款；仅目标尝试缺失时不改写其他尝试的结果。
- Observable: 上游可分别核对缺失组合、已有记录前后值和通道终态。本仓只看得到 RPC 结果、通道与资金，看不到特定尝试记录恰在处理时缺失。
- Basis: [PR 说明](https://github.com/nervosnetwork/fiber/pull/1753)、[付款方分支](https://github.com/nervosnetwork/fiber/blob/37a19a3230a105b9cc3a64d16b5c05cfc2c6ec92/crates/fiber-lib/src/fiber/network.rs#L2104-L2183)、[上游 Missing 同时省略两种记录](https://github.com/nervosnetwork/fiber/blob/37a19a3230a105b9cc3a64d16b5c05cfc2c6ec92/crates/fiber-lib/src/fiber/tests/onchain_tlc_reconcile.rs#L3995-L4055)。
- Source: explicit
- Testing: not_tested — 没有可重复的正常节点入口；若得到由受支持操作产生、前置状态可核实的旧库，再评估直接回归。不上调相邻用例的覆盖结论。

### SPEC-02：单方退款与整条通道的终态分别以各自链上事实判断
- Condition: 原持单已链上超时；先只有付款方收回款，再由收款方领取原有余额。
- Expected: 仍有承诺输出待领时不提前结束等待；全部结清后，付款方能查到已结束等待的原通道。
- Observable: 原 funding 的逐笔已确认消费、双方净到账，以及付款方带已关闭记录的原通道查询。
- Basis: 本次评审确认将原通道终态与单方退款分开验收；实现参见[关停检查与原承诺剩余输出](https://github.com/nervosnetwork/fiber/blob/37a19a3230a105b9cc3a64d16b5c05cfc2c6ec92/crates/fiber-lib/src/fiber/network.rs#L4624-L4766)，资金预期沿用[本仓既有退款约定](mpp-payment-recovery.md)。
- Source: explicit
- Testing: required

### SPEC-03：旧通道收尾不应覆盖另一首跳的新付款
- Condition: 同一付款方之前的请求已失败，原金额已退、没有在途付款工作；新的单路径持单使用同一哈希、不同首跳。
- Expected: 允许同一付款方支付另一收款方新开的同哈希发票。新付款只随新收款方的结算改变；旧通道尚待收尾或随后结清，不得令新付款回退或二次扣款。
- Observable: 新发票的收款人和 Paid 状态、匹配的原像、新通道双方余额变化及旧通道退款去向；付款方按同一哈希查询。
- Basis: 更换收款方是本次评审明确采纳的回归预期，非 PR 单独声明的保证。实现依据：[允许重付的条件](https://github.com/nervosnetwork/fiber/blob/37a19a3230a105b9cc3a64d16b5c05cfc2c6ec92/crates/fiber-lib/src/fiber/payment.rs#L2626-L2660)、[原首跳检查](https://github.com/nervosnetwork/fiber/blob/37a19a3230a105b9cc3a64d16b5c05cfc2c6ec92/crates/fiber-lib/src/fiber/payment.rs#L2320-L2368)、[相关问题 #1722](https://github.com/nervosnetwork/fiber/issues/1722)。
- Source: explicit
- Testing: required

## 验证设计

- **资金判断不取自付款状态。** 用持单前的两端余额、各自储备和收款锁做账本。由原 funding 逐笔跟踪已确认交易，按输入与输出算实际承担的费用，扣除双方另外投入的手续费输入。没有兑现原像的旧持单净转移应为零；B 最终只得持单前已有的份额。新发票的金额为 100 CKB，新通道两端可用余额分别精确减少、增加 100 CKB，锁定归零；把返回的原像按所选算法重算哈希。系统范围的“没有存活 cell”、发票 Paid 或单方余额增加都不足以单独判定通过。
- **与现有用例的关系。** [原直接退款用例](../test_cases/fiber/devnet/fake_time/test_direct_timeout_refund_after_restart.py)保留 `MPP1705-09` 的不同哈希健康付款和 `PSET-01` 的原通道收尾断言；旧款最终仍为 Failed。[同哈希重付用例](../test_cases/fiber/devnet/fake_time/test_same_hash_payment_after_channel_close.py)独立构造同一退款窗口，先让 C 的新款成功，再让 B 收尾；收尾后立即重查。两条均使用 [BasicClockFiber](../docs/references/cluster-clock.md)，可在时钟专用测试目录直接运行，不需要慢测开关。映射不代表实跑通过。上游的在途、已成功、重试中尝试已有进程内断言；这里不验证事件投递次数、数据库清理或尝试编号复用，也不以改库、定点崩溃或调试节点补齐缺口。
