# 入站通道 TLC 到期差校验用例

## 导读

- **这个改动要做什么**：接收方接受通道时，校验自己的 TLC 到期差是否足以覆盖发起方提出的 commitment delay。
- **行为变化**：此前自动或手动接受可能建立到期差过短的通道；修复后应拒绝这种入站配置，且自动接受失败不应丢掉待接受请求，用户仍可用合规参数手动接受。适用于包含 [fiber#1776](https://github.com/nervosnetwork/fiber/pull/1776) 的 FNN，PR 已合入 `develop`。
- **最怕出什么问题**：接收方建成不安全的通道，或自动接受失败后把合法开通请求卡死、无法手动恢复。
- **能测到什么**：用两个真实 FNN、CKB dev 链和公开 `open_channel` / `list_channels` / `accept_channel` RPC，检查拒绝、待接受保留及最终 `ChannelReady`。
- **测不到的**：内部 `ChannelAutoAcceptFailed` 事件和内存容器移除时序没有公开查询接口；上游 Rust 单测已有覆盖，不作为 Python 黑盒判据。

依据：PR base `b65cf44d6ad638cffa84641abe36292af12fc35a` → head `bc6ab2e5893904c985bacc61f2e10c5e24b222e3`，合入提交 `d0c7b8d36dd6319a4cea6adf17444de041e7127f`；[PR 说明](https://github.com/nervosnetwork/fiber/pull/1776)、[入站校验源码](https://github.com/nervosnetwork/fiber/blob/d0c7b8d36dd6319a4cea6adf17444de041e7127f/crates/fiber-lib/src/fiber/network.rs) 与 RPC README。

## 需要你决定

- 无。

## 用例

公共约定：A 为发起方，B 为接收方，均使用标准 release FNN 和现有 `FiberTest` 的 CKB dev 环境。6 epoch 在 CKB `EpochNumberWithFraction` RPC 中编码为 `0x10000000006`（长度 1、索引 0、epoch 数 6），不能传无效的 `0x6`。A 以 1,000 CKB、6 epoch commitment delay、16 小时 TLC delta 发起私有双向通道；按 CKB 4 小时/epoch 独立计算，最低 delta 为 `6 * 4 小时 * 2/3 = 16 小时 = 57,600,000 ms`，B 默认 4 小时不足。每条用例独立创建节点/通道，等待待接受记录不超过 15 秒、就绪不超过 120 秒；含对照通道和环境准备预计首条 3～5 分钟、第二条 2～4 分钟，不等待真实 16 小时到期。

<!-- TEST-CASES-BEGIN -->
| 用例 | 场景 | 预期结果 | 防止的问题 | 优先级 |
| --- | --- | --- | --- | --- |
| `F1776-01` | - [x] 确认 B 的自动接受配置有效，先用默认 1 epoch/4 小时开一条自动就绪的对照通道；再发起上述 6 epoch 通道，查询 B 待接受列表并以 16 小时 delta 手动接受同一临时 ID | 对照通道无需手动接受即在双方 `ChannelReady`；目标通道未被自动建立但待接受 ID 保留，手动接受后双方 `ChannelReady`，B 的目标通道 delta 为 16 小时 | 自动接受绕过安全校验，或失败后吞掉待接受请求 | P0 |
| `F1776-02` | - [x] B 关闭自动接受；A 发起上述通道，B 依次用默认 delta、16 小时减 1 ms、恰好 16 小时手动接受同一临时 ID | 前两次 RPC 拒绝且同一 ID 仍可待接受；边界值接受成功，双方最终 `ChannelReady`，B 查询到的 delta 为 16 小时 | 手动接受漏校验、拒绝后破坏待接受状态或边界值误拒 | P0 |
<!-- TEST-CASES-END -->

## 测试树

<!-- TEST-TREE-BEGIN -->
- 入站 TLC delta 必须匹配对端 commitment delay
  - 自动接受使用不足的本地默认值时，拒绝并保留手动恢复路径：F1776-01 [primary] -> SPEC-01
  - 手动接受默认值或显式小于阈值时拒绝，恰好阈值可成功：F1776-02 [primary] -> SPEC-01
  - 内部失败事件与容器时序：[not_applicable] — 无公开稳定观察接口，且产品单测已覆盖
<!-- TEST-TREE-END -->

## Spec

### SPEC-01：接收方只能使用不少于对端 commitment delay 三分之二的 TLC delta 建立入站通道
- Condition: 对端提出合法的 6 epoch commitment delay；接收方默认值不足，或手动提供不足/恰好满足的 delta。
- Expected: 不足时拒绝且保留待接受请求；满足时可接受并完成通道开通。
- Observable: `list_channels(only_pending=true)` 中的临时 ID、`accept_channel` RPC 成败、双方 `list_channels` 的 `ChannelReady` 与接收方 delta。
- Basis: [fiber#1776 的目标与实现](https://github.com/nervosnetwork/fiber/pull/1776)；[open_channel / accept_channel RPC 参数说明](https://github.com/nervosnetwork/fiber/blob/d0c7b8d36dd6319a4cea6adf17444de041e7127f/crates/fiber-lib/src/rpc/README.md)。
- Source: explicit
- Testing: required

## 验证设计

- **独立预期**：用 CKB epoch 编码和每 epoch 4 小时的 release 规则手算 6 epoch 的三分之二为 57,600,000 ms；固定参数的 `阈值 - 1` 与 `阈值` 形成紧邻边界，不调用被测 FNN 的同一计算函数作为期望值。接收方默认 4 小时来自当前产品配置，不依赖 RPC README 中对 `accept_channel` 默认 1 天的过时描述。
- **实际入口与观察**：A 的 `open_channel` 提供合法发起方 delta，避免在发起端提前被拒；B 的自动接受或 `accept_channel` 才是被测路径。首条先从 B 的 `node_info` 核对自动接受出资额大于零、触发门槛不高于 1,000 CKB、本地默认 delta 为 4 小时，再用同配置下无需手动操作即可就绪的对照通道证明自动接受确实工作。每次失败后重新通过公开 RPC 查询同一临时 ID，最终以新的 `ChannelReady` 和 B 的 delta 为证据。成功的边界值接受还排除前两次仅因无效 ID、未连接或资金不足而失败。
- **证明边界**：真实 FNN/CKB JSON-RPC 的通道建立行为；不宣称检查到内部事件、精确内存容器或链上 16 小时等待。第一条不能只在打开后瞬间看到 pending 就判自动接受失败，必须结合有效自动接受对照与随后同一目标 ID 可手动接受并就绪。
