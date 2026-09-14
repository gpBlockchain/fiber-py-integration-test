# Fiber Integration Tests — Agent Guide

本仓库使用 Python / pytest 验证 Fiber Network Node（FNN）在 CKB 上的通道、付款、发票、路由、Watchtower 和 CCH 行为。指导原则：**先让人看懂并确认用例，再维护自动化；测试保持简单、直接、易维护。**

## 1. 工作范围与现有状态

- 修改前检查 Git 状态，保留已有暂存、未暂存和未跟踪内容；只处理当前请求涉及的文件。
- 每次只推进一个接口、一组连贯行为或一份评审文档的一个阶段；完成后交接，不自动扩展到其他模块。
- 先用 `rg` 定位，再读最近的 `AGENTS.md`、相关评审文档、反馈、映射测试和必要的源码片段或 diff。避免全仓库内容转储。
- 对新用例、评审、映射维护和 PR 测试影响分析使用 `$ai-test-agent`。没有评审或映射工作流的普通单元测试修改按原有风格处理。

## 2. 两份事实来源与目录约定

| 位置 | 职责 |
| --- | --- |
| `reviews/` | 全仓库集中存放可供人工判断的行为用例，可按模块分子目录 |
| `reviews/review-feedback.md` | 记录人工纠正意见、涉及的用例 ID 和可复用约束 |
| `test_cases/` | 可执行测试；测试附近的 `TEST-MAP` 注释是映射事实来源 |
| `framework/` | 节点生命周期、RPC 客户端、环境与通道/付款辅助方法 |
| `scripts/check_test_map.py` | 检查评审 ID、代码映射、孤儿映射和重复评审 ID |
| `docs/references/` | API、测试模式及背景资料；按需阅读 |

`ai-test-agent` 中的 `suites/<suite>/` 是独立项目的通用布局。本仓库沿用已有的 `test_cases/`，检查器已支持此目录；更新文档或新增映射不触发目录迁移。

- Devnet 测试：`test_cases/fiber/devnet/<category>/test_<feature>.py`。
- CCH 测试：`test_cases/fiber/cch/`；框架测试：`test_cases/framework/`。
- Testnet / Mainnet 测试保留在各自现有目录，只在当前用例确有需要时运行。
- 旧测试不批量补 ID、不搬目录；仅在当前范围接入评审映射。
- 不新增覆盖率台账、审批状态字段或内部 ID 链。映射数量按需计算，不维护第二份清单。

## 3. 用例评审契约

每个独立可观察行为使用一个稳定 ID，如 `RPC-01`；该 ID 同时就是 Test Point ID。使用下面的精确表头与行格式：

```markdown
| 用例 | 场景 | 预期结果 | 防止的问题 | 优先级 |
| --- | --- | --- | --- | --- |
| `RPC-01` | - [ ] 提交有效请求 | 返回结果并产生一次预期副作用 | 正常请求失败或被重复处理 | P0 |
```

- 每行自包含，写清必要前提、操作和可观察结果；同一操作和判断依据能证明的相关字段合并描述。
- 场景列以任务复选框开头。新用例从 `- [ ]` 开始；存在匹配的 `TEST-MAP` 时使用 `- [x]`。复选框只表示映射存在，不表示人工确认或测试通过。
- 优先级：`P0` 为阻断发布的核心行为，`P1` 为重要失败与边界，`P2` 为低影响边缘情况。
- 产品行为不明确时，在预期结果中写 `待确认：<decision>`；别把当前实现或一次失败当成产品契约。
- 修改措辞、预期或优先级时保留 ID；仅为新的独立行为新增 ID。
- 文件路径、实现步骤、证据和运行记录放在表外。表内只用场景复选框显示自动化状态。
- 参数、状态或协议缩写影响理解时，在表前简述接口输入、状态流转和观察方式。
- 创建或实质修改用例行、记录纠正反馈时，按需阅读 skill 的 `references/review-cases.md`。

## 4. 强制人工确认门禁

对每一批新增、删除或实质修改的用例行：

1. 先编辑完整的本批变更行。
2. 展示这些行，然后停止；该轮仅交接评审，不修改这些行对应的自动化。
3. 等待用户对当前行集的明确确认；未指定对象的“继续”不视为确认。
4. 根据纠正意见更新文档，并在 `reviews/review-feedback.md` 记录相关 ID 与纠正内容。预期发生实质变化时，再次展示并等待确认。
5. 确认后，下一阶段才生成或同步对应的测试输入、步骤、断言和映射。

本批未变化且已确认的用例仍可进入自动化阶段。只修改文档不等于代码已对齐；本次只更新指导文档时，不顺带重写评审行或测试。

## 5. TEST-MAP 与自动化同步

在已确认用例对应的测试附近使用 Python 原生注释；下例仅展示注释位置，不是待实现的占位测试：

```python
# TEST-MAP: RPC-02
def test_missing_parameter(...):
    ...
```

- 无匹配注释：未自动化，场景使用 `- [ ]`；有匹配注释：已映射，使用 `- [x]`。
- 注释引用未知用例 ID 属于孤儿映射；同一 ID 出现在多条评审行中属于重复 ID，应在相关范围修正。
- 一个用例有多处映射时，逐一检查对应测试；别因找到第一处就忽略其余输入或断言。
- 增删映射时，同一变更中同步场景复选框。用例预期变更经确认后，同步所有相关测试，而非只替换注释。
- 维护现有映射、实现已确认用例或分析 PR 时，按需阅读 skill 的 `references/automation-maintenance.md`。
- 运行 `python3 scripts/check_test_map.py`；仅在要求全量自动化的范围与检查范围一致时加 `--require-complete`。
- 当前检查器扫描项目级范围，检查 ID 与映射关系，**不校验复选框或断言语义**。这些仍需检查 diff 和对应测试；退出码 0 不代表测试行为通过。

## 6. Fiber 测试框架与代码风格

继承关系：`unittest.TestCase → CkbTest → FiberTest → FiberCchTest`；`SharedFiberTest` 继承 `FiberTest`。

| 基类 | 环境生命周期与适用场景 |
| --- | --- |
| `FiberTest` | CKB 在类级初始化；Fiber 节点在 `setup_method` / `teardown_method` 创建和清理。适合需要独立 Fiber 状态、强关和重启等场景 |
| `SharedFiberTest` | CKB / Fiber 环境在类内共享，`teardown_class` 清理。适合复用拓扑的路由、费率、付款参数测试；留意状态污染 |
| `FiberCchTest` | 在 Fiber 基础上提供 BTC / LND 跨链环境；使用前检查其额外依赖 |

- 常用对象：`self.node`、`self.fiber1`、`self.fiber2`、`self.udtContract`；检查当前基类和配置，不假设整个链状态逐方法重置。
- 复用 `open_channel`、`send_payment`、`send_invoice_payment`、`wait_payment_state`、`wait_invoice_state` 等已有方法，先核对真实签名。
- 额外节点使用 `self.start_new_fiber(self.generate_account(...))`。共享拓扑可在 `setUp()` 中用类级 `_channel_inited` 守卫，节点保存在 `self.__class__.fiberN`；拓扑成功建立后再设置守卫。
- 金额使用 Shannon：`1 CKB = 100000000 Shannon`；RPC 中按接口要求使用 `hex()`。调用 `open_channel` 时传 UDT 使用 `udt=...`，避免与费用位置参数混淆。
- 每个功能或 PR 回归使用一个文件或类，方法命名为 `test_<scenario>`；文件顶部简短注明行为或 PR。
- 测试按准备、操作、等待、断言线性展开。只有逻辑确实复用时才抽取辅助方法，优先复用框架而非新增抽象。
- 本文件专用等待使用有上限的循环、明确间隔和超时失败信息；优先使用匹配语义的现有 `wait_*`。避免无限重试和仅靠长时间 sleep 判断成功。
- 断言 RPC 结果、通道状态、余额、付款结果及副作用；仅当错误文本本身是契约时断言具体措辞。只要求调用失败时可用 `pytest.raises(Exception)`。
- 被测操作的重试、断线恢复或最终失败本身是测试目标时，别让辅助方法的自动重试掩盖问题。
- 空实现、跳过或只有日志的测试不算行为验证；映射注释存在也不改变这一点。

## 7. PR 影响分析与最小验证

1. 明确被测源码仓库、实际基准和目标提交；对已定位的源码 diff 分析，不硬编码 `develop` 或旧源码路径。
2. 将变更落到对应功能目录：通道生命周期、付款/路由、连接、发票、图/gossip、Watchtower 或 CCH。
3. 从相关评审 ID 查找全部映射测试，判断现有预期与断言是否仍适用。需要新增、删除或实质修改用例时，回到人工确认门禁。
4. 先运行最小确定性或聚焦测试，再运行映射检查；聚焦测试通过且范围有必要时，才扩大到一个相关套件。

在仓库根目录使用项目现有 Python 环境执行，例如：

```bash
# 已有单条 devnet 用例（会启动本地节点）
python -m pytest test_cases/fiber/devnet/open_channel/test_funding_amount.py::FundingAmount::test_funding_amount_ckb_is_zero -v -s

# 映射检查，不启动节点
python3 scripts/check_test_map.py

# 仅当范围确有需要时运行 Makefile 已列出的测试目录
make fiber_test
```

- 运行前检查当前 Python 环境、所需节点二进制、端口和数据目录；保留无关运行环境。
- 新测试放入现有分类时检查 pytest 发现规则；新增分类需检查 `Makefile` 的显式目录列表及相关 CI，不假设全目录自动接入。
- 文档专用改动检查内容、链接与 diff，并运行映射检查即可；无需启动节点或跑完整 devnet。
- 映射检查发现既有问题时，区分原有问题与本次回归，不跨范围修改其他评审文档。
- 仅在用例需要时访问实时网络，重试有界；持续不可用时报告残余风险。CI 默认只检查一次，持续等待需用户明确要求。
- 报告实际命令、关键原始输出和退出码；区分未运行、环境失败、断言失败与成功，不把收集成功当成执行成功。

## 8. 按需参考与简短交接

### 外部背景资料

- **合约**：[nervosnetwork/fiber-scripts](https://github.com/nervosnetwork/fiber-scripts)。查阅 Fiber 链上合约，包括 `funding-lock`、`commitment-lock`，以及合约测试和部署资料。
- **节点代码**：[nervosnetwork/fiber](https://github.com/nervosnetwork/fiber)。查阅 FNN 实现、RPC 与 P2P 协议文档，分析被测行为及 PR 影响。
- **P2P 测试**：[gpBlockchain/fiber · p2p-tap](https://github.com/gpBlockchain/fiber/tree/p2p-tap)。本项目指定的 P2P 测试代码参考分支；具体测试扩展与使用方法以该分支代码为准。

分析前确认实际使用的仓库、分支或提交，以及节点二进制和合约版本；区分上游节点实现与 `p2p-tap` 测试分支的差异，不把测试专用行为当作上游协议契约。只读取当前用例所需内容，不因补充资料自动克隆、切换或更新这些仓库。

### 本仓库参考

- [API 与辅助方法](docs/references/api-reference.md)
- [测试模式](docs/references/test-patterns.md)
- [Lightning / Fiber 概念](docs/references/lightning-concepts.md)
- [历史覆盖缺口分析](docs/references/gap-analysis.md)：仅作候选线索，使用前核对当前源码、评审与测试，不在本文件维护容易过期的缺口列表。

交接只报告相关字段，省略无关项，不重复未变化的用例表：

```text
Scope: 本次接口、行为、文档或 PR
Changed cases: ID 与预期变化
Added automation: 按相同原因或判断依据分组，单列例外
Coverage: 已映射/已评审；未映射 ID（映射不等于通过）
Verification: 命令 -> 关键原始输出及退出码
Residual risk: 待确认、人工项、环境限制或无
Next gate: 需要确认的具体行集或下一步操作
```
