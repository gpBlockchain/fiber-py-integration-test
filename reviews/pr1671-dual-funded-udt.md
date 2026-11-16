# PR #1671 双方出资 xUDT 通道测试分析

本次把 OffCKB 工程发现的 UDT 通道出资问题补充到 Fiber 的 Python 开发链测试中，检查找零、默认费率下的手续费、锁脚本依赖，以及开通后的支付和协作关闭。已实现原有 8 条用例及本轮确认的 2 条旧用例完整流程补充，复用现有框架和相关旧测试。本文保留源码分析、历史复现、检查方法和实际运行结果。

测试代码以 `nervosnetwork/fiber-py-integration-test` 的 `v0.10.0` 为基础，提交为 `6088115aec85f002bfbedd8f3b3d919b6601cec5`，即 2026-10-20 拉取时 [PR #110](https://github.com/nervosnetwork/fiber-py-integration-test/pull/110) 的最新提交。

源码依据为 [Fiber PR #1671](https://github.com/nervosnetwork/fiber/pull/1671)：改动前的提交 `355bd63582e5034044de3c50a5fd9ad38968aec6` → 修复提交 `ccc6fea022c5e24f694fdf288456b88262332a69`。已阅读本机 `offckb-py-intergration-test/source/fiber-pr1671` 中两个提交之间的完整差异。分析依据的提交与实际运行的 FNN 版本分别记录，运行环境见下文。

## 三个问题及修复内容

PR 只修改 `crates/fiber-lib/src/ckb/funding/funding_tx.rs`。以下修复目标来自 PR 描述，实现已从源码核对：

- [#1667 接受方找零导致输出位置变化](https://github.com/nervosnetwork/fiber/issues/1667)：旧实现把接受方的 UDT 找零插在已有找零输出前面，导致发起方按位置校验交易时失败。修复后先保留已有的非注资输出及对应数据，再追加本方找零。本批 Python 测试检查通道能否开通、最终输出和代币数量是否正确；协商前后各输出位置和数据是否保持不变，由 PR 自带的 Rust 测试直接检查。
- [#1663 手续费漏算接受方的签名](https://github.com/nervosnetwork/fiber/issues/1663)：旧实现估算手续费时，没有给接受方首个 UDT 输入对应的签名预留完整空间。修复后按已有输入数量找到接受方签名所在的位置，提前留出签名数据（`witness`）的空间。Python 测试保持 Fiber 默认费率和 CKB 最低费率均为 1000，检查带双方签名的交易能否确认；历史排查时曾把费率提高到 2000，本次验收不采用这个设置。
- [#1669 仅使用 UDT 输入时缺少锁脚本依赖](https://github.com/nervosnetwork/fiber/issues/1669)：旧实现仅在补充普通 CKB 输入时，才顺带添加 secp 锁依赖。修复后选择 UDT 输入时就补齐它们所用锁脚本的依赖。本测试中的 secp 依赖指 `secp256k1_blake160` 锁所需的 `dep_group`。

原始分析资料位于本机 `/Users/sunchengzhu/project/offckb-py-intergration-test`：`reviews/fiber/udt-payments.md`、`reviews/fiber/review-feedback.md`、`tests/fiber/test_udt.py`、`tests/fiber/udt_flows.py`，以及 `inputs/fiber-udt-sharing/` 中的三个 issue、PR 描述和代码差异文件。分析前已读取目标分支的 `reviews/review-feedback.md`。本批使用现有 xUDT 合约、测试环境和准备代码；历史 SUDT 结果仅作背景，未作为本批 SUDT 已通过的证明。

历史 issue #1669 有一组重要对照：双方各持有两个 `5000` 单位、`142 CKB` 的 xUDT cell 时，出资 `[10000, 10000]` 的交易实际使用 **4 个 UDT 输入、0 个普通 CKB 输入**；出资 `[5000, 10000]` 时，则使用 **3 个 UDT 输入、1 个普通 CKB 输入**。后者因为补了普通 CKB 输入，整笔交易也得到了 secp 依赖，不能用来验证“仅 UDT 输入时缺依赖”的问题。原报告将费率设为 2000 是为了排除手续费问题，本批仍按默认 1000 验证。

## 测试条件和检查方法

- A 发起开通道，B 接受通道，双方使用不同的标准 secp 锁。每条用例中，双方初始各有 10000 单位的同一种 xUDT。本文 UDT 金额均按最小单位计数，RPC 传 `hex(raw_units)`，不乘 `100000000`；cell 携带的 CKB 数量（`capacity`）另以 Shannon 表示。
- 使用 `FiberTest` 提供的两个 Fiber 节点、CKB 节点和现有 xUDT 合约。CKB 在同一测试类的各方法之间共用，默认准备代码还会给 A 发行其他代币。因此每条用例换一个发行账户，使代币类型脚本中的 `args` 不同，并检查该种代币的初始 cell，避免旧余额或上一条用例的找零影响选币。当前代币白名单的 `args` 为 `0x.*`，允许这些代币。
- 每方一个 cell 时，该 cell 持有 `10000` 单位 xUDT 和 `142 CKB`；每方两个 cell 时，每个 cell 持有 `5000` 单位 xUDT 和 `142 CKB`。双方另有充足的普通 CKB。准备交易已确认、CKB 索引已能查到这些 cell 后，才发起开通。`faucet()` 默认创建的 UDT cell 并非 142 CKB，因此使用现有 CLI 交易工具准备所需数量和容量，并直接核对结果。
- 下文“注资交易”指创建通道资金 cell 的交易。交易“已确认”指 `get_transaction` 返回的 `tx_status.status` 为 `committed`；通道“就绪”指状态为 `ChannelReady`。
- 直接调用 `open_channel`，在 B 的 `list_channels(only_pending=True)` 中找到对应临时 ID 后，再调用 `accept_channel`。本批金额低于默认 xUDT 自动接受阈值，因此由 B 手工接受；核对 B 的接受方身份、代币类型脚本和请求金额。
- 开通成功须同时满足：注资交易已确认；双方查询到相同的通道 ID 和资金 cell 位置（`channel_outpoint`），且都已就绪；A 的本地余额与 B 的远端余额相同，反向也相同，金额分别等于双方出资额；类型脚本的 `code_hash`、`hash_type`、`args` 均一致。从 `channel_outpoint` 解析交易哈希和输出编号，定位实际注资交易。
- 链上代币余额按完整类型脚本和账户锁汇总所有未花费的 cell。检查注资输入时，按每个 `previous_output` 查询原交易，核对真正选中的输入数量、锁和资产类型；已花费的输入也按这个方法查询。把 `outputs` 与 `outputs_data` 配对检查，代币数量按小端序的 128 位无符号整数解码。关闭通道的 CKB 余额比较从资产准备完成、尚未开通时开始，统计双方锁下所有未花费 cell 的 `capacity`；结束后总额应只减少注资和关闭交易的实际手续费，期间不插入其他资金操作。
- 核对实际 CKB 配置和 `tx_pool_info.min_fee_rate`，最低费率须为 1000。交易手续费按输入 CKB 总量减输出总量计算；检查双方各自首个输入所对应的 `witness`，其中须有 65 字节签名。测试保留默认注资费率，不固定历史交易大小和手续费数值。
- 复用已有 `wait_*` 方法。其他等待条件每秒检查一次：开通和支付最多等 120 秒，链上确认最多等 180 秒，超时给出最后状态并失败。这些是测试的等待上限，与产品自身的注资超时设置无关。所有 TLC 处理完成、`pending_tlcs` 为空后，再核对支付后的余额。
- 当前预留为 `DEFAULT_MIN_DEPOSIT_UDT = 181 CKB`，旧版本为 180 CKB；它与普通 xUDT cell 的 142 CKB 最低存储容量不同。运行时核对实际 FNN、合约和预留规则，不照抄旧版本的注资输出容量或交易大小。尤其是仅 UDT 输入场景，须以实际交易确认没有补普通 CKB 输入。

## 用例关系

```text
双方出资 xUDT 通道
├─ 接受方有找零：每方一个 UDT 输入／每方多个 UDT 输入 → PR1671-01、04
├─ 接受方全额出资
│  ├─ 每方一个 cell，需要普通 CKB 输入，按默认费率估费 → PR1671-02
│  ├─ 每方两个 cell，只有 UDT 输入，检查 secp 依赖 → PR1671-03
│  └─ 每方两个 cell，实际混用 UDT 和普通 CKB 输入 → PR1671-05
├─ 零出资接受通道后完成首次支付 → PR1671-06
└─ 开通后使用通道
   ├─ 双向发票支付，核对余额 → PR1671-07
   └─ 支付后协作关闭，核对链上资金返还 → PR1671-08
```

## 用例

| 用例 | 场景 | 预期结果 | 防止的问题 | 优先级 |
| --- | --- | --- | --- | --- |
| `PR1671-01` | - [x] 双方各持一个 10000 单位的 xUDT cell，以默认费率分别出资 5000、3000，由 B 手工接受通道 | 注资交易已确认，双方查询到同一就绪通道，余额为 5000、3000；双方各自锁下的找零为 5000、7000，类型脚本一致，代币总量不变 | 接受方找零改变已有输出的位置，导致开通失败或退错账户 | P0 |
| `PR1671-02` | - [x] 双方各持一个 10000 单位的 xUDT cell，分别出资 5000、10000，费率分别省略或显式传 1000，CKB 最低费率为 1000 | 带双方有效签名的注资交易能以该费率确认，双方查询到同一就绪通道且余额正确；B 没有 UDT 找零，A 链上剩余 5000 | 漏算接受方签名占用的字节，导致必须提高费率才能开通 | P0 |
| `PR1671-03` | - [x] 双方各持两个 5000 单位、142 CKB 的 xUDT cell，以默认费率各投入全部 10000 | 注资交易实际恰用 4 个 UDT 输入、0 个普通 CKB 输入，包含正确且不重复的 secp 依赖；交易确认后双方通道就绪且各有 10000，链上双方该种代币均清零 | UDT cell 的 CKB 容量足够时缺少锁脚本依赖，或测试意外补了普通 CKB 输入而漏掉问题 | P0 |
| `PR1671-04` | - [x] 双方各持两个 5000 单位、142 CKB 的 xUDT cell，以默认费率分别出资 6000、7000 | 注资交易实际各用 2 个 UDT 输入；交易确认后双方通道就绪，余额为 6000、7000，各自锁下找零为 4000、3000，代币总量不变 | 使用多个输入时丢失找零，或只检查钱包有多个 cell，却没检查实际使用的输入 | P1 |
| `PR1671-05` | - [x] 双方各持两个 5000 单位、142 CKB 的 xUDT cell，以默认费率分别出资 5000、10000 | 注资交易实际用 A 的 1 个、B 的 2 个 UDT 输入，并补普通 CKB 输入；secp 依赖正确且不重复，交易确认后双方通道就绪，链上该种代币分别剩余 5000、0 | 修复锁依赖时影响原本需要补普通 CKB 输入的开通过程 | P1 |
| `PR1671-06` | - [x] 双方各持一个 10000 单位的 xUDT cell，A 以默认费率投入 5000，B 以 0 手工接受，再由 A 支付 B 的 100 单位发票 | 注资交易已确认且双方通道就绪；B 原有的 UDT cell 没有被花费，链上余额仍为 10000；支付为 Success、发票为 Paid，通道余额为 4900、100，所有 TLC 已处理完成 | 修复双方出资时影响零出资接受，或用户仍无法完成首次支付 | P0 |
| `PR1671-07` | - [x] 以默认费率建立出资 5000、3000 的 xUDT 通道，先由 A 向 B 支付 100 单位发票，再由 B 向 A 支付 70 单位发票 | 每笔都在付款方查到 Success、收款方查到发票 Paid；通道余额依次为 4900、3100 和 4970、3030，双方记录的本地与远端余额相互对应，所有 TLC 已处理完成 | 通道开通后支付资产或记账出错，或反向付款时查错节点 | P1 |
| `PR1671-08` | - [x] 以默认费率建立出资 5000、3000 的 xUDT 通道，完成 A→B 100、B→A 70 单位发票支付，处理完所有 TLC 后协作关闭 | 关闭交易已确认并花费原通道资金 cell，双方通道为 Closed；汇总各自锁下所有未花费 cell，该种代币分别为 9970、10030，没有重复返还，双方 CKB 总额只减少实际手续费 | 只检查关闭请求返回成功，漏掉交易未确认、找零漏算或资金返还错误 | P1 |

## 旧用例完整流程补充

本轮只补齐迁移旧用例时遗漏的支付、协作关闭和资金返还流程。Fiber PR #1671 的三个直接修复点均在注资交易构造中；支付和关闭属于开通后的既有行为。第二条的多输入开通步骤仍经过该注资逻辑，因此有交集，但新增检查的重点是保留旧用例的完整流程。用户于 2026-10-20 确认以下两条用例，要求在 #115 追加一个独立提交；现有 8 条用例保持原意。

两条均使用默认注资费率 1000、同一种 xUDT，双方另有充足普通 CKB。下表代币金额均为最小单位，不乘 `100000000`。每个 UDT cell 携带 142 CKB。开通须检查交易确认及双方就绪；付款须在实际付款方查到 `Success`、收款方发票为 `Paid`，并等到双方 `pending_tlcs` 为空。关闭须等双方 `Closed`、实际关闭交易确认且花费原通道资金 cell；最终钱包余额汇总双方所有相关 cell，CKB 总额只减少实际开通和关闭手续费。

| 用例 | 场景 | 预期结果 | 防止的问题 | 优先级 |
| --- | --- | --- | --- | --- |
| `UDT-CLOSE-01` | - [x] 双方各持一个含 10000 单位 xUDT 的 cell，A 出资 5000、B 零出资接受；A 向 B 支付 100 后协作关闭 | 支付后通道余额为 4900/100；关闭交易分别返还 4900/100，最终钱包余额为 9900/10100；B 原有 UDT cell 始终未花费，状态和 CKB 手续费检查符合上述要求 | 迁移原零出资用例时，只保留首次支付，遗漏同一通道关闭及返还检查 | P1 |
| `UDT-CLOSE-02` | - [x] A 持一个 10000 单位 xUDT cell，B 持两个各 5000 单位的 cell；A 出资 5000、B 投入全部 10000，B 向 A 支付 1000 后协作关闭 | 注资实际使用 A 的 1 个和 B 的 2 个 UDT 输入，A 找零 5000、B 无 UDT 找零；支付后通道余额为 6000/9000；关闭分别返还 6000/9000，最终钱包余额为 11000/9000，状态和 CKB 手续费检查符合上述要求 | 迁移原多输入用例时，用其他通道的支付或关闭检查替代“接受方多输入全额出资→反向付款→关闭”的完整流程 | P1 |

对应原方法分别为 `test_udt_funding_amount_zero` 和 `test_udt_multi_funding_amount_gt_account`。后者原本是发起方部分出资、接受方把两个 cell 的代币全部投入，并由接受方向发起方付款；本次保留这些条件，将代币金额缩小以复用现有准备代码。普通 CKB 输入按实际容量需要补充，不把本条误作“仅 UDT 输入”的依赖检查。

复用 `test_dual_funded_udt.py` 的资产准备、开通、付款和余额检查，增加两条线性测试。资产准备方法新增可选的接受方 cell 数量，默认行为不变；原 08 的关闭请求、双方 Closed、关闭交易确认、花费资金 cell 和手续费检查提取为本文件共用方法，返还数量和最终钱包余额仍在各测试中直接断言。聚焦验证覆盖新增两条、原 03 的双方各两个 UDT 输入开通、原 08 的双向支付后关闭；另收集整个 accept_channel 目录，检查 CI 能否找到全部测试。实际结果在文末补充。

## 旧代码如何整合

本组测试放在 `test_cases/fiber/devnet/accept_channel/test_dual_funded_udt.py`，使用一个 `FiberTest` 类。现有 8 条覆盖了开通、双向付款、协作关闭和资金返还的分项检查，但并未完整保留下面两种旧流程；本轮已按确认的两条独立用例补齐。

该目录已被 Makefile 和 `.github/workflows/fiber.yml` 的现有任务执行，因此不新增 CI 任务，也不修改触发条件。按 CI 使用的目录检查，本轮 pytest 能识别到 35 项测试，其中包括本文件全部 11 项（原有 9 项及新增 2 项）。现有 `make fiber_test` 及后续运行的 accept_channel 任务都会自动包含这些测试。现有工作流仅对目标为 `main` 的 PR 自动触发；本 PR 提交到 `v0.10.0`，通过 `workflow_dispatch` 指定测试分支手动运行。

| 现有代码 | 本次处理 |
| --- | --- |
| `accept_channel/test_funding_amount.py::test_udt_funding_amount_lt_account` | 原来因缺少 secp 依赖而被 `pytest.mark.skip` 跳过。对应检查合并到 01、07、08；修正 B 付款后在 A 查状态，以及最后余额常量与实际付款量不符的问题，删除被替代的旧方法。 |
| 同文件 `test_udt_multi_funding_amount_gt_account` | 原来同样被跳过。方法名写的是超过余额，实际是 A 部分出资、B 将两次发行的代币全部投入，再由 B 付款给 A 并关闭。03、04、05 检查相关开通条件，07、08 使用另一种出资组合，不能替代这条完整流程；由 UDT-CLOSE-02 保留同一通道的完整流程。 |
| 同文件 `test_udt_funding_amount_zero` | 原来没有跳过。06 检查了零出资接受后的首次支付；08 为双方出资，不能替代零出资通道关闭及返还。由 UDT-CLOSE-01 保留同一通道的完整流程，余额汇总全部相关 cell。 |
| `FiberTest.faucet`、`generate_account`、`get_account_udt_script`，`UdtContract.list_cell` / `balance` | 继续用于准备账户、取得代币类型脚本和汇总链上余额；测试另行核对 cell 的数量、容量和实际使用的输入。 |
| `framework/helper/ckb_cli.py` 中的 `tx_init`、`tx_add_input`、`tx_add_output` 及签名提交方法 | 用于创建指定数量、每个 142 CKB 的 xUDT cell。共用的准备步骤放在本测试文件，不修改框架原有的发行方法。 |
| `FiberTest.open_channel` | 小金额分支没有传入 UDT 类型脚本，还会增加 CKB 预留；其他分支会通过支付分配余额。这些步骤不能验证真实的双方 UDT 出资，因此本批直接调用开通和接受 RPC，保留通用方法原样。 |
| `wait_for_channel_state`、`wait_payment_state`、`wait_invoice_state`、`get_tx_message` | 继续用于等待状态、读取输入输出及计算 CKB 手续费；另行核对完整脚本、通道资金 cell 位置和全部未花费 cell。付款和发票分别在对应节点查询，手续费检查按实际通道交易进行。 |
| OffCKB 的 `prepare_udt`、`open_udt_channel`、`pay_udt_invoice` | 沿用场景参数和检查内容：交易确认、双方通道状态、代币类型、余额。OffCKB 专属 CLI、环境类和将已知缺陷记为 xfail 的处理留在原工程；本批直接要求修复后成功。 |

每个测试方法使用稳定的 `TEST-MAP` ID，中文说明按步骤编号，代码段前的注释使用相同编号。02 用同一套检查方法分别运行省略费率和显式传 1000 两种情况。其他测试按步骤顺序执行，仅提取多个地方都会使用的准备和查询代码。本次没有修改产品源码或构建流程。

## 本次覆盖范围和运行方法

本次尚未加入 SUDT 测试环境、多跳、MPP、强制关闭、`watchtower`、自定义锁、缺少白名单的配置或旧版本对照测试。估费前的签名占位、协商前后输出位置不变，以及找不到依赖时的内部错误分支，由上游 Rust 测试直接检查；本批 Python 测试检查真实交易和节点响应。用例表中的复选框只表示代码中存在对应 `TEST-MAP` 注释。

现有 `prepare.sh` 从 `develop` 构建 `download/fiber/current/fnn`。分析 PR 使用的是固定提交，实际运行还须单独核对二进制来源、是否包含修复及合约是否兼容。本轮复用已有构建，没有构建或修改产品。

在已安装项目依赖的 Python 环境中运行：

```bash
python3 -m pytest test_cases/fiber/devnet/accept_channel/test_dual_funded_udt.py -v -s
python3 scripts/check_test_map.py
```

`check_test_map.py` 只检查用例 ID 和代码注释是否对应，不检查断言是否完整或测试是否通过。原分支已有 `PMP-01` 至 `PMP-05`、`SETTLE-01` 六个注释引用了评审文档中不存在的 ID，本次不修改这些其他测试的问题。

## 实际运行环境

本轮使用独立 Git 工作目录和端口 `28114`、`28228`、`28229`，没有停止主工作目录的节点。环境为 Python `3.13.2`、pytest `7.3.2`、CKB `0.202.0 (d0a6c95)`、ckb-cli `1.15.0 (8c892a5)`。

FNN 使用本机已有的 `v0.10.0-rc1 (8419c00 2026-10-16)` 构建，源码完整提交为 `8419c005e2d0e737c5abf7e0f881aaedbc6fd300`。已核对 Git 提交关系，确认它包含 PR #1671 的修复提交，并检查了干净源码中的三项修复。二进制 SHA256 为 `fc9880b0c60b16e4e208fe92730ed6bd6d3e93e253c74d8dabea6a8855f0e951`，与原构建记录一致。该构建启用了 debug-add-tlc，本批只调用常规 RPC；上述结果只适用于这个实际构建，尚未验证之后的 develop 或正式发布包。

本地运行时通过符号链接复用 CKB、CLI 和 FNN，这些链接、二进制和原始日志不随测试代码提交。CI 仍使用仓库 `make prepare` 准备的 `download/fiber/current/fnn`。

## 首轮验证结果（原 8 条用例）

2026-10-20 使用上述环境和项目现有 Python 虚拟环境运行：

```bash
python -u -m pytest test_cases/fiber/devnet/accept_channel/test_dual_funded_udt.py -v -s -o addopts='' --tb=short --maxfail=1
# 9 passed in 981.45s (0:16:21)，退出码 0

python3 scripts/check_test_map.py
# 退出码 1：仍有原分支的 PMP-01～05、SETTLE-01 六个未对应到评审用例的 ID，无重复评审 ID。
```

8 条用例实际运行了 9 项测试：省略费率与显式传 1000 分别执行。本批没有跳过项或 xfail。`git diff --check`、两个修改文件的 Black 格式检查均通过，pytest 也能正常识别旧文件剩余的 7 项测试；旧文件其他方法没有重新运行节点测试。

实际注资交易已核对：仅 UDT 输入场景为双方各 2 个 UDT 输入，共 4 个输入；混合输入场景为 A 的 1 个、B 的 2 个 UDT 输入，加 1 个普通 CKB 输入，共 4 个输入；多输入找零场景为双方各 2 个 UDT 输入，加普通 CKB 输入，共 6 个输入。锁依赖、签名、链上确认、双方余额及代币总量检查均通过。双向支付和协作关闭也通过了状态、TLC、实际关闭交易、最终 `9970/10030` 代币余额及 CKB 实际手续费的检查。

测试结束后，所用 CKB 和 Fiber RPC 端口已释放。完整日志仅保存在本机 `/private/tmp/pr1671-focused.log`，不提交到仓库。

首轮验证完成后曾调整文字，当时已对比修改前后的可执行代码，确认只改了注释和说明，测试输入、操作和断言均未变化。9 项测试仍可正常识别，Black 格式检查和 `git diff --check` 通过；本次没有重新启动节点运行测试。

## 本轮补漏验证（UDT-CLOSE-01、02）

2026-10-20 在上述同一环境运行。重新核对 FNN、CKB、Python 版本及 FNN 二进制 SHA256，与首轮记录一致。

```bash
python -u -m pytest \
  test_cases/fiber/devnet/accept_channel/test_dual_funded_udt.py::TestDualFundedUdt::test_zero_acceptor_funding_returns_paid_balances_on_close \
  test_cases/fiber/devnet/accept_channel/test_dual_funded_udt.py::TestDualFundedUdt::test_full_multi_cell_acceptor_funding_can_pay_and_close \
  test_cases/fiber/devnet/accept_channel/test_dual_funded_udt.py::TestDualFundedUdt::test_full_multi_cell_funding_needs_no_plain_ckb_input \
  test_cases/fiber/devnet/accept_channel/test_dual_funded_udt.py::TestDualFundedUdt::test_cooperative_close_returns_paid_balances_and_all_change \
  -v -s -o addopts='' --tb=short --maxfail=1
# 4 passed in 472.89s (0:07:52)，退出码 0

python -m pytest test_cases/fiber/devnet/accept_channel --collect-only -q -o addopts=''
# 35 tests collected in 0.16s，其中本文件 11 项；退出码 0

python3 scripts/check_test_map.py
# 58/62 个评审 ID 已映射；本 PR 的 10 个 ID 均已映射，新增两条各有一处映射。
# 退出码 1：仍为原分支已有的 PMP-01～05、SETTLE-01 六个代码 ID 在文档中没有对应用例，本次没有新增缺失或重复评审 ID。
```

聚焦范围为新增两条完整流程，以及共用准备代码的原 03、共用关闭代码的原 08。新两条及这两条回归均通过，未重跑本文件其余 7 项节点测试。零出资流程的实际 UDT 输入数为 1/0；接受方全额出资流程为 1/2，交易另补普通 CKB 输入；原 03 仍为 2/2 个 UDT 输入、0 个普通 CKB 输入。两条新增流程的支付状态、发票状态、关闭交易、逐方返还、最终钱包余额和 CKB 手续费检查均通过。

Black 检查和 `git diff --check` 通过。测试退出后，专用 CKB/Fiber RPC 及 P2P 端口已释放。原始日志保存在本机 `/private/tmp/pr1671-close-focused.log`，不提交到仓库。本轮不修改 CI 配置，不重复触发远端 CI；现有 accept_channel 目录任务会收集这两条新测试。本轮代码、文档和之前的纠正反馈作为一个独立提交追加到 #115，保留原提交。
