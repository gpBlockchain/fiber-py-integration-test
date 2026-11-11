# V2 入站款项：重启后主动强关与真实回款

## 导读

- **目的**：付款方离线、收款方已提交原像但尚未链下结算时，收款方重启后主动强关，仍能取回全部应收款、本金和归属自己的预留容量。
- **背景**：[Fiber #1737](https://github.com/nervosnetwork/fiber/pull/1737) 将待收款原像准备放到 V2 共用强关入口。本用例验证公开主动强关后的真实资金路径，不替代自动到期保护的期限测试；它也可能在修复前版本通过。
- **范围**：仅新建 V2 CKB，真实 release 节点、公开 RPC、正常停启、CKB devnet 和内置 Watchtower。两笔款分别使用 sha256、ckb_hash；不调系统时间、不改数据库或二进制。
- **风险**：只关通道却漏领某笔应收款，重复记账，或把正常重启的结果当成缺失原像补写、通知去重的证明。

分析版本：base / merge-base `fb8a4950d34c08b3144201a89472d541fd4d92b2`，head `12b7a845f0ea5b7a68eaa7ffd8424463df55141a`。既有 V2 可沿升级路径受益；V1/Legacy 不因此变为 V2。本批使用新建 V2，不扩大到其他部署变体。

## 需要你决定

无。用户已确认该行、完成首例运行，并要求提交 PR；本 PR 不增加其他场景。

## 用例

### 公共前提与预算

- A 为收款方，B 为付款方。通过框架 `open_channel` 创建通道，A/B 资金参数为 500/0 CKB，承诺延迟固定一 epoch；正常双向付款后记录 A 本金 P、归属 A 的预留容量 R、钱包及 funding outpoint。
- B→A 两笔持单金额分别为 `200000017`、`200000018` Shannon；分别用 sha256、ckb_hash，核对实际期限 `E1<E2`。两笔均 Received、实际入站金额稳定后正常停 B，B 此后不重启。
- A 各提交一次正确原像并从原目录正常重启。公开强关前核对通道 Ready、funding 未消费，且距离最早保护阈值 `E1−S−I` 至少五分钟；其中 release 一 epoch 对应四小时，`S=160分钟`、`I=60秒`。只有条件满足才调用一次 `shutdown_channel(force=true)`，不再次提交原像，不重试该关闭操作。
- RPC 限十秒、关闭广播限六十秒；从关闭 RPC 发起起，确认与全部领取共用六百六十秒上限。通过 devnet 正常出块和 `generate_epochs` 推进相对锁定期。整例预算二十五分钟，不修改编译常量或合约。
- 使用仓库 `CURRENT_DEV` 和现有 devnet 合约模板；没有 PR 专用版本或端口环境变量。测试目录已由现有 `connect_peer` CI job 和 Makefile 收集。

<!-- TEST-CASES-BEGIN -->
| 用例 | 场景 | 预期结果 | 防止的问题 | 优先级 |
| --- | --- | --- | --- | --- |
| `RCLAIM-03` | - [x] V2 CKB 两笔先后到期、哈希算法不同的持单均 Received 后停付款方；收款方各提交一次原像并正常重启，在最早保护阈值前至少五分钟且 funding 未消费时主动强关，付款方持续离线（预计 10～20 分钟，上限 25 分钟） | 单次关闭后，本金、归属收款方的预留容量及两笔应收款扣实际承担费用后真实到账，各笔只记一次，发票最终 Paid；不依赖等到期或再次提交原像 | 自动保护测试通过，却遗漏其他强关入口下的待收款，或把人工关闭成功误记为自动保护通过 | P1 |
<!-- TEST-CASES-END -->

## 测试树

<!-- TEST-TREE-BEGIN -->
- 已授权入站款在主动强关入口下的领取
  - 保护窗前主动关闭，收款方正常重启后两笔应收款及本金全部到账：RCLAIM-03 [primary] -> SPEC-01, SPEC-02
  - 自动到期保护、恢复超时、合作关闭等待与 UDT：[not_applicable] — 本 PR 只交付已确认的 CKB 主动关闭首例，不扩大其他场景。
- 不生成黑盒内部状态用例
  - 精确 TLC 绑定、队列竞争、删除原像记录后补写和通知去重：[not_applicable] — 不通过内部改库或消息钩子构造；本例只证明公开接口下的真实到账。
<!-- TEST-TREE-END -->

## Spec

### SPEC-01：V2 主动强关也保留已授权待收款
- Condition: 付款方离线；收款方已提交本笔正确原像，但尚未完成链下结算，随后从完整目录正常重启。
- Expected: 在保护阈值之前主动强关，两笔先后到期的应收款仍能领取，无需重新提交原像。
- Observable: 单次公开强关、实际 funding 消费和每笔领取交易。
- Basis: [共用强关入口](https://github.com/nervosnetwork/fiber/blob/12b7a845f0ea5b7a68eaa7ffd8424463df55141a/crates/fiber-lib/src/fiber/channel.rs#L2718-L2792) 与用户确认的 RCLAIM-03。
- Source: explicit
- Testing: required

### SPEC-02：以独立金额和真实消费链证明回款
- Condition: 测试预先固定两笔金额、原像以及收款方本金和归属容量。
- Expected: A 实收为 P+R+两笔应收款−A实际承担的链上费用；金额各记一次，两张发票最终 Paid。
- Observable: funding→commitment→settlement、完整哈希和原像、目的锁、钱包增量及发票状态。
- Basis: 用户已确认的真实回款和 Paid 验收要求；PR 作者说明上游模拟链测试未验证实际钱包到账。
- Source: explicit
- Testing: required

## 验证设计

- **独立预期**：金额来自测试输入，P/R 在目标付款前固定；费用逐笔按链上输入输出计算，排除钱包自有输入找零。每个完整哈希与测试端原像匹配，检查各笔只能领取一次，再对账钱包总增量。
- **入口与观察**：公开建通道、持单、结算、节点正常停启和单次强关；新鲜查询实际交易、钱包、发票，不以 Closed 或 RPC 成功代替到账。
- **证明边界**：仅证明本场景的主动强关回款，不证明自动到期保护及时性、所有关闭触发器、原像缺失修补或通知去重。
- **实现**：[`TestV2ClaimRecovery.test_manual_force_close_claims_survive_restart`](../test_cases/fiber/devnet/connect_peer/test_v2_claim_recovery.py)。保留已通过版本的测试步骤及所需方法，不引入其他尚未发布的测试；可选 RPC timeout/deadline 支持配有无网络的框架测试，默认调用行为保持不变。

## 验证记录

- 原工作区使用本地 `fnn Fiber v0.10.0-rc1 (12b7a84 2026-11-11)`：`1 passed in 172.23s (0:02:52)`，退出 0。
- 两笔应收分别释放 `200000017`、`200000018` Shannon，本金与归属容量释放 `50100000000` Shannon；三笔领取手续费合计 `2710` Shannon，钱包净增 `50499997325` Shannon，两张发票 Paid。未调整系统时间。
- 发布分支基于上游 `v0.10.0` 的 `939e6969eaa1078a97a6d3665a43fcba75709dd7`。保持原有默认二进制路径，本地复跑将忽略目录中的 `download/fiber/current/fnn` 指向上述 1737 二进制；未提交版本路径调整。
- 发布分支的同一 pytest 选择器复跑：`1 passed in 167.10s (0:02:47)`，退出 0；真实钱包净增仍为 `50499997325` Shannon，两张发票 Paid，测试节点和端口正常清理。
- `python -m pytest -q -o addopts= test_cases/framework/test_ckb_rpc_deadline.py test_cases/framework/test_fiber_rpc_timeout.py`：`7 passed in 0.08s`，退出 0。
- `black --check .`：410 个文件通过；设计结构检查 `cases: 1`，退出 0；`git diff --check` 通过。
- `python3 scripts/check_test_map.py`：90 个评审、86 个映射，退出 1，仍是既有的 `PMP-01`～`PMP-05`、`SETTLE-01` 六个孤儿；RCLAIM-03 已映射，无重复评审 ID。不提交本机数据目录、二进制或运行日志。
