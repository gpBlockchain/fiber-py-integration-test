# 对端自动接受失败时，发起方应得到开通失败原因

## 导读

发起方一直看到“协商中”且没有失败原因，是 [Fiber #1785](https://github.com/nervosnetwork/fiber/issues/1785) 报告的缺陷，不是回归通过条件。两条原测试都在强关前等待开通就绪超时。接收方日志已证实，同一个开通请求在自动接受时未通过 TLC 到期差值校验；问题是发起方看不到这个结果，而不是接收方执行校验。

## 需要你决定

- `CDE-01` 已确认只验证开通失败，不等通道就绪，也不执行本端或对端强关。两条旧用例在开通阶段没有差别，合并为一个测试。待上游明确具体失败通知形式；在 #1785 修复前保留 skip。

## 用例

A、B 是在线的 devnet 节点。用 `list_peers` 确认目标对端已连接，用 B 的 `node_info` 确认 1000 CKB 达到其自动接受门槛，且 B 的 TLC 到期差值为 14,400,000 ms。本用例只发一次开通请求，不为准备环境另开通道；合法参数的 `test_epoch` 仍独立保留。

<!-- TEST-CASES-BEGIN -->
| 用例 | 场景 | 预期结果 | 防止的问题 | 优先级 |
| --- | --- | --- | --- | --- |
| `CDE-01` | - [x] A 向在线的 B 开公开 1000 CKB 通道，延迟为 `0x6`、TLC 到期差值为 57,600,000 ms；B 自动接受时使用 14,400,000 ms，校验失败 | A 能识别本次开通已失败及其原因，不再显示为仍待协商。待确认：上游采用哪种 RPC 结果或通知 | 钱包把已拒绝的请求当成仍待接受 | P1 |
<!-- TEST-CASES-END -->

## 测试树

<!-- TEST-TREE-BEGIN -->
- 对端未通过自动接受校验
  - 发起方能区分已失败的请求和仍等待接受的请求：CDE-01 [primary] -> SPEC-01
<!-- TEST-TREE-END -->

## Spec

### SPEC-01：开通被拒绝后，调用方应能看到失败及原因
- Condition: A、B 在线，A 向 B 提交这组开通参数。CI 中 B 自动接受时遇到到期差值校验错误；修复后不预设 A 一定先拿到临时 ID。
- Expected: A 得到关联本次请求的失败结果和有意义的原因，而非持续显示未完成的协商。不预设具体错误字符串、协议消息或终态名称。
- Observable: 若 `open_channel` 直接返回本次开通失败及原因，核对该响应；若先返回临时 ID，按 ID 核对后续失败结果。只看到 `NegotiatingFunding` 且 `failure_detail=null`、记录消失或查询超时，都不能单独证明已正确通知失败。
- Basis: 用户更正了对原 RPC 样例的判断，详见 [Fiber #1785](https://github.com/nervosnetwork/fiber/issues/1785)。[CI](https://github.com/nervosnetwork/fiber-py-integration-test/actions/runs/45609893489/job/139204185812) 的两份接收方日志均记录了 14,400,000 ms 小于要求的 57,600,000 ms，且通道 ID 与对应发起方日志匹配。[原测试](../test_cases/fiber/devnet/open_channel/test_commitment_delay_epoch.py)均在 120 秒就绪等待处超时。
- Source: explicit
- Testing: required

## 验证设计

目标请求只发一次。若 `open_channel` 直接报错，检查到期差值相关的原因；若先返回临时 ID，在发起方查询 `list_channels(only_pending=true)`，按该 ID 查找已关闭、无出资引用且带失败详情的开通记录。最多观察 120 秒，单次查询超时不超过 5 秒及剩余预算。节点准备与前置 RPC 另计。B 的 CI 日志用于核对背景，不代替 A 面向用户的结果；本地提前校验也不证明请求到过 B。断连、泛化超时、查不到记录或协商一直未结束不算通过。勾选只表示已有映射，跳过不是执行通过。
