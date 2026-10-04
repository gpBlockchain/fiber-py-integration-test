# Full payment hash settlement — v1/v2 合并评审

评审范围：两个 PR 共同改变的“通道版本选择 → 链上完整 hash 结算 → 监控核对和旧资产兼容”行为链；已确认设计沿用；本轮统一迁移评审与既有测试映射，不代表全量自动化或整个 PR 可合并。

合并规则：以v2的Spec、树和稳定H32V2 ID为骨架，吸收v1的完整升级矩阵、精确边界和恢复窗口；保留v2新增负例及对照。原v1文档迁移完成后移出reviews，v1/v2合并前的历史快照只保留在本地分析目录，不随本仓库提交，也不作为本评审的入库证据。01～25保留ID，新增26（已上链首次settle前升级）、27（合作关闭）。旧H32映射按实际测试路径迁入H32V2；未实现与部分覆盖均保留，不用迁移状态代替执行通过。合并不是实施授权，历史执行和旧版B结论不自动适用于本版。

## 固定输入

- [fiber PR #1656](https://github.com/nervosnetwork/fiber/pull/1656)：base tip / merge-base `9a561b3e7b786001488b61303a0663d9f4f22259`；head `8b95af3ab2caac570eba3be8e30f9157f95a4869`。
- [fiber-scripts PR #30](https://github.com/nervosnetwork/fiber-scripts/pull/30)：base tip / merge-base `cbf2bc2cb02d2a774468b72cee4ab7cfd690b727`；head `dba3b36e72512e780d89bcd6868ba6fc29c0f892`。
- 测试项目 HEAD：`8cd29c31a99d8097f0876e48a88dd64a430c7a3e`，已有未提交测试、框架、pytest 配置和二进制；不能仅用 HEAD 代表测试输入；本 PR 以提交内容本身作为固定的测试输入声明。
- 原始 PR JSON、review comments、一次 CI 快照、merge-base→head diff 及变化文件原文只保留在本地分析目录，不随本仓库提交。节点分析使用 `git show <head>:<path>` 导出，不切换当前 checkout；合约复用既有 checkout。未用 controlled pr_workflow 状态机：采用显式双仓库原始材料包和原生独立 B 调用，隔离等级按 `isolation_unverified` 报告。

## 变更说明

- 合约：旧版仅 57 字节 args、85 字节 TLC/20 字节 hash；新版保留 Legacy，并新增严格 58 字节/feature=1、97 字节 TLC/32 字节 hash；后续派生 cell 保留版本。
- 节点：新增可选 feature 位、开通时固定版本，持久化到通道/开通/监控记录；普通和外部 funding、容量和费率计算、内置/独立 Watchtower 统一使用版本。
- 核对：只有身份准确的链上消费证据携带错误完整原像时，才从 Unknown 改成失败处理；发起端/中继不等到期。旧前缀键记录不因此获得精确身份可信度。
- 相邻影响：默认自动接受出资99→100 CKB；真实旧库使用 bincode，新增 serde 默认字段并不单独证明旧库可读。合约升级与节点升级须分开测试。
- 相对现存 `136ab285` 节点源码副本，当前 head 的额外变更在网络测试、Bruno 余额/出资断言和 perf 初始化；业务分析仍以当前完整 PR diff 为准。
- PR body 中的历史测试通过数字不是本次执行结果；当前 CI 仅作外部上下文；节点CI快照仅返回93项中的前30项，合约返回1/1，不据此宣称所有检查通过。没有运行 devnet、Rust 或产品测试。
- 未读/未分析：`.superpowers` 和 docs/superpowers 内计划/自评报告不作为产品依据；完整路由/MPP/CCH、性能极限、部署 checksum 可复现构建及迁移具体实现尚未分析。新增文档不宣称覆盖这些分支。

## 输入和观察约定

TLC 为条件付款。Legacy/V1 指链上 witness 布局，不等同节点软件版本；hash 算法为 CKB Blake2b 或 SHA256。args 的第56字节是首次/派生状态，V1 第57字节才是feature。CKB 金额单位 Shannon，1 CKB=100000000 Shannon；UDT 数额读取 output_data，不与capacity混算。

成功以真实交易 committed、指定 outpoint 消费、收款净额、付款/TLC 终态证明；交易构建、日志和 RPC 返回成功都不足。坏原像输入取任意 preimage，计算摘要后仅修改声明 hash 的尾12字节；不求解160位碰撞。对端提供异常输入，本端保持正常实现。若普通 RPC/已有 Python 夹具不支持该输入，记录夹具缺口并待确认，不通过新 Rust 包装测试或改产品实现绕过。

## Spec

### SPEC-01：协商决定布局
Condition: 双方建立新通道，包括普通资金和 external funding。
Expected: 仅双方声明该特性时选 V1；任一方未声明选 Legacy。
Observable: 真实承诺 args 长度和结算条目长度；通道可付款并结算。
Basis: [crates/fiber-lib/src/fiber/network.rs — negotiated_commitment_contract_version / PendingOpenChannel / on_accept_channel](https://github.com/nervosnetwork/fiber/blob/8b95af3ab2caac570eba3be8e30f9157f95a4869/crates/fiber-lib/src/fiber/network.rs)；[crates/fiber-types/src/channel.rs — CommitmentContractVersion / ChannelActorData / ChannelOpenRecord](https://github.com/nervosnetwork/fiber/blob/8b95af3ab2caac570eba3be8e30f9157f95a4869/crates/fiber-types/src/channel.rs)
Source: observation
Testing: required

### SPEC-02：待接受版本固定
Condition: 已接收 OpenChannel，尚未手动 accept，之后会话特性发生改变。
Expected: 候选：接受原请求沿用接收时版本；若断连已清理原请求，单独识别请求失效，不把重新发起当原请求。
Observable: 原 temporary_channel_id 生命周期及原请求形成的承诺布局。
Basis: [crates/fiber-lib/src/fiber/network.rs — negotiated_commitment_contract_version / PendingOpenChannel / on_accept_channel](https://github.com/nervosnetwork/fiber/blob/8b95af3ab2caac570eba3be8e30f9157f95a4869/crates/fiber-lib/src/fiber/network.rs)
Source: observation
Testing: required

### SPEC-03：持久化与旧库兼容
Condition: 新版本通道重启，或真实旧版本数据库交给新节点读取。
Expected: 已存 V1 重启不降级；旧记录保持 Legacy 语义。待确认的是直接读取或迁移策略；两条策略均须保留Legacy并最终可用。拒绝启动是失败证据，不是可替代的成功预期；serde(default) 不等于 bincode 旧记录兼容。
Observable: 原channel_id、outpoint、余额和原签名承诺恢复，旧通道继续付款及结算；启动失败时保留原始旧库。
Basis: [crates/fiber-types/src/channel.rs — CommitmentContractVersion / ChannelActorData / ChannelOpenRecord](https://github.com/nervosnetwork/fiber/blob/8b95af3ab2caac570eba3be8e30f9157f95a4869/crates/fiber-types/src/channel.rs)；[crates/fiber-lib/src/store/store_impl/mod.rs — bincode serialize / deserialize / insert_watch_channel_with_version](https://github.com/nervosnetwork/fiber/blob/8b95af3ab2caac570eba3be8e30f9157f95a4869/crates/fiber-lib/src/store/store_impl/mod.rs)
Source: inference
Testing: required

### SPEC-04：双布局原像验证
Condition: 资金与签名有效，分别按 Legacy 或 V1 构造结算，算法为 CKB Blake2b 或 SHA256。
Expected: V1 验证完整 32 字节，Legacy 保留 20 字节规则；V1的前缀相同尾部不同原像被拒绝，未确认的坏交易不触发节点兑现或立即失败，正常超时仍可用。
Observable: 交易 committed 或验证失败、原 cell 是否仍 live、收款是否发生。
Basis: [contracts/commitment-lock/src/main.rs — resolve_htlc_layout / preimage_matches / auth](https://github.com/nervosnetwork/fiber-scripts/blob/dba3b36e72512e780d89bcd6868ba6fc29c0f892/contracts/commitment-lock/src/main.rs)
Source: observation
Testing: required

### SPEC-05：布局边界拒绝
Condition: 锁 args 长度/feature byte 非法，或合法 args 与 witness 条目布局不匹配，或部分结算的派生输出改变版本。
Expected: 只接受 57 字节 Legacy 或 58 字节且末字节 0x01 的 V1；跨布局、截断 witness 和错误派生输出版本均拒绝，无资金移动。
Observable: CKB 脚本验证结果与原 cell 未花费；不依赖错误文案。
Basis: [contracts/commitment-lock/src/main.rs — resolve_htlc_layout / preimage_matches / auth](https://github.com/nervosnetwork/fiber-scripts/blob/dba3b36e72512e780d89bcd6868ba6fc29c0f892/contracts/commitment-lock/src/main.rs)
Source: observation
Testing: not_tested — 由 fiber-scripts 的 Rust 合约测试覆盖（本仓库是 Python 集成测试，无法为 commitment lock 签名，也不写 Python→cargo 包装）

### SPEC-06：派生 cell 连续结算
Condition: V1/Legacy 含多个待处理 TLC，首次只兑付其中一个，随后继续兑付。
Expected: 派生 cell 保持原版本，剩余 TLC、资产数额和顺序正确；最终余额按实际手续费守恒。
Observable: 父子 outpoint 消费链、args/witness、CKB capacity 或 UDT data、付款与关闭终态。
Basis: [contracts/commitment-lock/src/main.rs — resolve_htlc_layout / preimage_matches / auth](https://github.com/nervosnetwork/fiber-scripts/blob/dba3b36e72512e780d89bcd6868ba6fc29c0f892/contracts/commitment-lock/src/main.rs)；[crates/fiber-lib/src/watchtower/actor.rs — try_settle_commitment_tx / reconcile_settlement_witness / build_settlement_tx](https://github.com/nervosnetwork/fiber/blob/8b95af3ab2caac570eba3be8e30f9157f95a4869/crates/fiber-lib/src/watchtower/actor.rs)
Source: observation
Testing: required

### SPEC-07：超时与撤销保持可用
Condition: 升级合约上 Legacy/V1 发生逐TLC无原像超时、允许条件满足后的最终余额sweep仍含未逐笔解锁TLC，或已撤销承诺惩罚。
Expected: 对应锁定期前拒绝、达到协议条件后可回收；撤销按撤销路径处理而非假装逐 TLC 成功；最终sweep消费最后cell时为剩余TLC形成精确无原像记录，不因没有逐条unlock遗漏收尾。
Observable: 链上接受/拒绝、资金接收者、未被误标成功的付款。
Basis: [contracts/commitment-lock/src/main.rs — resolve_htlc_layout / preimage_matches / auth](https://github.com/nervosnetwork/fiber-scripts/blob/dba3b36e72512e780d89bcd6868ba6fc29c0f892/contracts/commitment-lock/src/main.rs)；[crates/fiber-lib/src/watchtower/actor.rs — try_settle_commitment_tx / reconcile_settlement_witness / build_settlement_tx](https://github.com/nervosnetwork/fiber/blob/8b95af3ab2caac570eba3be8e30f9157f95a4869/crates/fiber-lib/src/watchtower/actor.rs)
Source: inference
Testing: required

### SPEC-08：Watchtower 注册传播
Condition: 内置事件或独立 Watchtower RPC 注册通道并重启监控进程。
Expected: 显式 V1 保留 V1；旧客户端省略版本字段按 Legacy；重启后仍可生成正确首笔和后继结算。
Observable: RPC 入参、重启前后记录对应的真实链上布局及付款结果。
Basis: [crates/fiber-json-types/src/watchtower.rs — CreateWatchChannelParams](https://github.com/nervosnetwork/fiber/blob/8b95af3ab2caac570eba3be8e30f9157f95a4869/crates/fiber-json-types/src/watchtower.rs)；[crates/fiber-lib/src/store/store_impl/mod.rs — bincode serialize / deserialize / insert_watch_channel_with_version](https://github.com/nervosnetwork/fiber/blob/8b95af3ab2caac570eba3be8e30f9157f95a4869/crates/fiber-lib/src/store/store_impl/mod.rs)；[crates/fiber-lib/src/watchtower/actor.rs — try_settle_commitment_tx / reconcile_settlement_witness / build_settlement_tx](https://github.com/nervosnetwork/fiber/blob/8b95af3ab2caac570eba3be8e30f9157f95a4869/crates/fiber-lib/src/watchtower/actor.rs)
Source: observation
Testing: required

### SPEC-09：精确结算身份
Condition: 监控匹配原承诺快照和实际输入 outpoint 的结算，包括revocation_data=None、链上空TLC快照S0但pending S1已有TLC的窗口，以及相同hash前缀对照。
Expected: 仅经原承诺 witness hash 验证的快照、准确 TLC 身份和算法参与结算；无关交易/快照不改变付款，无效原像不进入有效 preimage 库；已匹配的S0即使为空仍可回收余额，不重新误选S1。
Observable: 目标/对照付款状态、链上消费链和可观测 preimage；缺少读取库的观测点须补夹具，日志不算证明。
Basis: [crates/fiber-lib/src/watchtower/actor.rs — try_settle_commitment_tx / reconcile_settlement_witness / build_settlement_tx](https://github.com/nervosnetwork/fiber/blob/8b95af3ab2caac570eba3be8e30f9157f95a4869/crates/fiber-lib/src/watchtower/actor.rs)；[crates/fiber-lib/src/fiber/onchain_tlc_reconcile.rs — resolve_onchain_tlc / collect_onchain_timeout_settled_tlcs](https://github.com/nervosnetwork/fiber/blob/8b95af3ab2caac570eba3be8e30f9157f95a4869/crates/fiber-lib/src/fiber/onchain_tlc_reconcile.rs)
Source: observation
Testing: required

### SPEC-10：精确坏原像不再悬挂
Condition: Legacy 链上消费已形成 Exact 记录，preimage 只匹配前 20 字节，完整 hash 不匹配，TLC尚未到期；要求整个付款最终Failed时明确无重试机会。
Expected: 本次核对即进入失败处理，不再等待 TLC expiry；无重试机会的付款发起端最终失败，中继向上游传播失败，received侧不fulfill；这不代表追回已花链上资金。
Observable: 在到期前的有界核对窗口观察付款/上下游 TLC 终态且无成功原像。
Basis: [crates/fiber-lib/src/fiber/onchain_tlc_reconcile.rs — resolve_onchain_tlc / collect_onchain_timeout_settled_tlcs](https://github.com/nervosnetwork/fiber/blob/8b95af3ab2caac570eba3be8e30f9157f95a4869/crates/fiber-lib/src/fiber/onchain_tlc_reconcile.rs)
Source: observation
Testing: required

### SPEC-11：弱证据不得触发失败
Condition: 只有旧 prefix-keyed 记录、身份/算法不符，或只有本地已知原像而没有精确链上消费证明。
Expected: 不把弱证据等同 Exact 坏原像消费；旧记录只有完整 hash 正确时才可 fulfill，否则保持未知；普通无原像超时保留到期条件。
Observable: 目标及同前缀对照 TLC 不被误 fulfill/fail；到期前后对照。
Basis: [crates/fiber-lib/src/fiber/onchain_tlc_reconcile.rs — resolve_onchain_tlc / collect_onchain_timeout_settled_tlcs](https://github.com/nervosnetwork/fiber/blob/8b95af3ab2caac570eba3be8e30f9157f95a4869/crates/fiber-lib/src/fiber/onchain_tlc_reconcile.rs)
Source: observation
Testing: required

### SPEC-12：核对幂等
Condition: 同一精确坏原像结算已确认且无重试机会，分别在失败通知完成前、处理完成后重启并反复扫描。
Expected: 通知前崩溃后恢复原失败处理；已处理TLC不重复计账、不重新挂起，付款终态及余额稳定，无关TLC不受影响。
Observable: 重复观察前后的 TLC、付款结果、余额和上游处理次数。
Basis: [crates/fiber-lib/src/fiber/onchain_tlc_reconcile.rs — resolve_onchain_tlc / collect_onchain_timeout_settled_tlcs](https://github.com/nervosnetwork/fiber/blob/8b95af3ab2caac570eba3be8e30f9157f95a4869/crates/fiber-lib/src/fiber/onchain_tlc_reconcile.rs)；[crates/fiber-lib/src/watchtower/actor.rs — try_settle_commitment_tx / reconcile_settlement_witness / build_settlement_tx](https://github.com/nervosnetwork/fiber/blob/8b95af3ab2caac570eba3be8e30f9157f95a4869/crates/fiber-lib/src/watchtower/actor.rs)
Source: inference
Testing: required

### SPEC-13：容量与费用匹配版本
Condition: 按协商版本构造承诺/结算，shutdown args 短于或长于承诺 args，资产为 CKB 或 UDT。
Expected: 容量下限使用 max(shutdown args,57/58)；承诺费按真实交易长度，结算 witness 每条 V1 TLC 比 Legacy 多12字节；扣占用后的预留恰好覆盖两倍承诺费通过该校验、少1 Shannon失败；短锁CKB自动预留99/100 CKB，不混同费用校验下限；普通/外部/接收开通与0/1/多TLC结算均验证。
Observable: 独立按实际序列化交易长度与链上输入输出计算费用；开通边界及结算 committed。
Basis: [crates/fiber-lib/src/fiber/channel.rs — settlement_tlc_to_witness / occupied_capacity / build commitment](https://github.com/nervosnetwork/fiber/blob/8b95af3ab2caac570eba3be8e30f9157f95a4869/crates/fiber-lib/src/fiber/channel.rs)；[crates/fiber-lib/src/fiber/fee.rs — checked_calculate_commitment_tx_fee / check_commitment_reserved_fee](https://github.com/nervosnetwork/fiber/blob/8b95af3ab2caac570eba3be8e30f9157f95a4869/crates/fiber-lib/src/fiber/fee.rs)；[crates/fiber-lib/src/watchtower/actor.rs — try_settle_commitment_tx / reconcile_settlement_witness / build_settlement_tx](https://github.com/nervosnetwork/fiber/blob/8b95af3ab2caac570eba3be8e30f9157f95a4869/crates/fiber-lib/src/watchtower/actor.rs)
Source: observation
Testing: required

### SPEC-14：自动接受默认值
Condition: 接收方未配置自动出资或显式配置零，收到满足自动接受阈值的开通请求。
Expected: 默认出资100 CKB；显式零禁用自动接受且允许后续手动接受。
Observable: node_info、真实 funding 出资、pending 到 ChannelReady 的变化。
Basis: [crates/fiber-lib/src/fiber/config.rs — MIN_OCCUPIED_CAPACITY / auto_accept_channel_ckb_funding_amount](https://github.com/nervosnetwork/fiber/blob/8b95af3ab2caac570eba3be8e30f9157f95a4869/crates/fiber-lib/src/fiber/config.rs)
Source: observation
Testing: required

### SPEC-15：仅升级链上合约的兼容性
Condition: 保持节点进程与数据不变，通过原 type-id 更新合约代码 cell；旧承诺或派生 cell 仍存活。
Expected: 升级不改变已协商版本；分别验证新开通后实际settle、原签名承诺升级后广播、已上链首次settle前升级、派生cell期间升级、既有和新通道合作关闭。实际settle执行升级后的live代码；合作关闭仅是相邻回归。
Observable: PID、进程启动时间和持续存活；原outpoint/签名承诺；升级确认、输入锁code_hash/hash_type、直接dep或展开dep-group指向的live代码数据哈希；真实settlement和扣费净余额。
Basis: [contracts/commitment-lock/src/main.rs — resolve_htlc_layout / preimage_matches / auth](https://github.com/nervosnetwork/fiber-scripts/blob/dba3b36e72512e780d89bcd6868ba6fc29c0f892/contracts/commitment-lock/src/main.rs)；[crates/fiber-lib/src/fiber/channel.rs — settlement_tlc_to_witness / occupied_capacity / build commitment](https://github.com/nervosnetwork/fiber/blob/8b95af3ab2caac570eba3be8e30f9157f95a4869/crates/fiber-lib/src/fiber/channel.rs)；项目 review-feedback 中“不重启”“需要测链上交互”的原始要求
Source: inference
Testing: required

## 测试树

<!-- TEST-TREE-BEGIN -->
- 选择与持久化
  - H32V2-01 [primary] -> SPEC-01：双方支持时新通道使用 V1
  - H32V2-02 [primary] -> SPEC-01：混合节点回退 Legacy
  - H32V2-03 [primary] -> SPEC-02：待接受请求固定版本
  - H32V2-04 [primary] -> SPEC-03：V1 原库重启
  - H32V2-05 [primary] -> SPEC-03：真实旧库升级
- 合约验证和资金回收
  - H32V2-06 [primary] -> SPEC-04：有效原像兑付
  - H32V2-07 [primary] -> SPEC-04：前缀有效尾部错误
  - [pending] 布局边界拒绝（SPEC-05） — 由 Rust 合约测试负责：56/59 字节 args、58 字节未知 feature 掩码、跨布局 witness、截断 witness 及派生输出版本篡改都需可签名的合约测试构造；本仓库 Python 无法为 commitment lock 签名，"假 cell"路径既不可运行也无法把拒绝归因到布局校验，故本评审不设该场景。
  - H32V2-09 [primary] -> SPEC-06：连续结算与资产
  - H32V2-10 [primary] -> SPEC-07：无原像超时
  - H32V2-11 [primary] -> SPEC-07：撤销承诺回收
- 监控与核对
  - H32V2-12 [primary] -> SPEC-08：独立监控版本传递
  - H32V2-13 [primary] -> SPEC-09：消费链与精确身份
  - H32V2-24 [primary] -> SPEC-09：首次RAA前正确空快照仍可回收
  - H32V2-25 [primary] -> SPEC-07：最终余额sweep收尾剩余TLC
  - H32V2-14 [primary] -> SPEC-10：发起付款坏原像失败
  - H32V2-15 [primary] -> SPEC-10：中继坏原像失败传播
  - H32V2-16 [primary] -> SPEC-10：接收侧坏原像收尾
  - H32V2-17 [primary] -> SPEC-11：弱证据与超时对照
  - H32V2-18 [primary] -> SPEC-12：重复扫描和重启
- 费用与配置
  - H32V2-19 [primary] -> SPEC-13：版本化容量费用边界
  - H32V2-20 [primary] -> SPEC-14：默认与零自动接受
- 不停机合约升级
  - H32V2-21 [primary] -> SPEC-15：升级后新通道开通并实际强关结算
  - H32V2-22 [primary] -> SPEC-15：升级前签名承诺升级后兑付
  - H32V2-23 [primary] -> SPEC-15：派生cell存活期间升级
  - H32V2-26 [primary] -> SPEC-15：已上链原始承诺首次settle前升级
  - H32V2-27 [primary] -> SPEC-15：升级前后通道合作关闭相邻回归
- 混合版本 MPP 与长链路（本轮新增行，待 B 设计复核）
  - H32V2-28 [primary] -> SPEC-01：混合版本 MPP 正常链下完成
  - H32V2-29 [primary] -> SPEC-10：混合版本 MPP 单片链上结算不提前完成
  - H32V2-30 [primary] -> SPEC-11：多跳混合版本下跨版本失败传播
  - H32V2-31 [primary] -> SPEC-09：同付款分片分属两版时的独立核对
  - H32V2-32 [primary] -> SPEC-10：多跳混合版本下完整原像结算的成功传播
- 观测/实施限制
  - [pending] 待接受特性变化（H32V2-03） — 已自动化可达的一半：断连后原 temporary_channel_id 判为失效（从 only_pending 消失、accept 被拒、不建出通道），同身份对端重连后只能重新发起不同 id 的新请求，并按当时宣告重新协商（只宣告 Legacy 时新通道为 57 字节）。主预期（版本在接收时固定、accept 时复用）仍不可观测：`list_channels` 不暴露协商版本，参考实现断连即清理待接受记录。
  - [pending] watchtower 持久观测点 — 对抗对端已能发出仅前缀有效的链上消费（见测试落地边界），但仍读不到节点持久化的 preimage/settlement store 与 snapshot/revocation_data，13/17/24 只能按链上结果与 TLC/付款终态判定，日志不作为证据。
  - [pending] 旧库升级策略（H32V2-05） — 直接读取还是必须迁移仍待产品决定；真实旧 bincode 库的待开通记录已可独立复核，watch 记录仍只有行为证据。
  - [not_applicable] 首次RAA前的空快照S0（H32V2-24） — 对端可用 `drop_raa` + `submit_commitment_transaction` 广播更早的空承诺，本端回收余额已写入用例。
  - [pending] 混合版本 MPP/长链路 — 28～32 行已补齐最关键的混合版本子集（链下 MPP、单片链上结算、多跳坏原像失败传播、分片级核对、多跳完整原像成功传播）；32 的上游收尾只断言“不再停留在进行中状态（LocalAnnounced/Committed/RemoteRemoved）”，成功路径的确切落点状态名尚未实测，未写死到行里；MPP 路由的极端分支（原子 MPP 部分成功语义、重组、性能极限、CCH）仍未展开；长链路反向（上游 Legacy→下游 V1）仍缺，需另立 ID。
  - [unanalysed] 未知必选P2P特性与所有外部资金中断窗口 — 超出所选布局协商和结算链。
  - [unanalysed] 合约二进制可复现构建 — 尚未验证源码、部署文件、checksum的一致性。
<!-- TEST-TREE-END -->

## 待评审用例

复选框仅表示对应TEST-MAP存在，包含仅覆盖部分矩阵的测试；无映射行保持未勾选（本评审只收 Python 集成测试场景；01～07、09～31 有映射）。迁移优先复用原H32测试；资产覆盖以 xUDT 为准（本仓库没有独立的 UDT 夹具），xUDT 用例计入其覆盖。本轮把 07/08/10/11/13/14/15/16/17/18/19/24/25 的映射补入新文件并同步勾选，迁移缺口与映射变更随本 PR 的测试 diff 一并核对，旧测试历史结果与本轮未执行的 devnet 测试都不自动授予v2执行通过。

<!-- TEST-CASES-BEGIN -->
| 用例 | 场景 | 预期结果 | 防止的问题 | 优先级 |
| --- | --- | --- | --- | --- |
| `H32V2-01` | - [x] 两端支持完整 hash，分别通过普通资金和外部资金开通新通道，保留至少一笔已承诺未完成TLC，强关后才释放原像完成链上付款 | 承诺 args 为58字节且 feature=0x01，TLC 条目97字节；付款与链上结算成功 | 双方构造不一致或仅普通开通路径升级 | P0 |
| `H32V2-02` | - [x] 一端不声明特性，分别由两端发起新通道，保留至少一笔已承诺未完成TLC，强关后才释放原像完成链上付款 | 承诺 args 为57字节、TLC 条目85字节；双方可完成付款与结算 | 新旧节点互联后资金锁死 | P0 |
| `H32V2-03` | - [x] 接收开通时固定V1或Legacy，关闭自动接受；在原请求仍有效时对端重连改变特性，再接受原请求 | 接受原请求沿用接收时固定版本，承诺格式一致；原请求若已被断连清理，应识别失效，不用重新发起的请求替代验证 | accept 时重新协商导致两端承诺不一致 | P1 |
| `H32V2-04` | - [x] V1通道已有已签名承诺与待处理TLC，双方节点及内置Watchtower用各自原库重启后广播原承诺 | 原channel_id、余额及最新签名承诺哈希保持一致；58/97布局不降级，原TLC正确链上结算 | 版本丢失使重启后 witness 被错解 | P0 |
| `H32V2-05` | - [x] 由 PR base 节点创建已用通道、待开通和监控记录，保存一致 checkpoint，再以 head 节点读取副本 | 待确认：支持原库直接读取还是必须迁移；两种策略均须恢复原通道、保留Legacy语义并继续付款及结算，不静默切V1；启动拒绝或解码失败记录为兼容性失败并保护旧库，不当作通过 | 将新结构自序列化测试误当旧库兼容 | P0 |
| `H32V2-06` | - [x] V1 通道含有效原像 TLC；分别使用两种算法和 offered/received 条目，并覆盖双方承诺方向 | 完整 hash 匹配的结算上链，指定收款者到账，付款成功且原像正确 | hash 长度变化导致正常兑付失败或付错人 | P0 |
| `H32V2-07` | - [x] 令承诺 hash 等于已知原像摘要但仅修改末尾字节，其他签名和输入有效；分别提交 Legacy/V1 结算并覆盖两种算法及两种条目方向 | Legacy保留前缀验证行为；V1拒绝消费、原cell仍live且无收款；本端不记录兑现或立即失败收尾，保留正常超时路径 | V1 仍仅检查20字节或只修复一个分支 | P0 |
| `H32V2-09` | - [x] 分别在Legacy/V1及CKB/xUDT通道保留两笔不同金额TLC，先兑付一笔，再兑付剩余一笔和最终余额 | 派生cell保持原版本及剩余TLC；两笔只各付一次，最终无待结算资产，余额扣实际手续费后守恒 | 首次成功但派生布局丢失导致后续资产卡住 | P0 |
| `H32V2-10` | - [x] Legacy/V1通道强关，分别含offered/received无原像TLC，在合约要求的到期条件前后提交回收 | 到期/承诺延迟条件未满足时拒绝，满足后按协议正确退款；付款不因无原像回收变Success | 动态偏移读错expiry或超时被提前放行 | P0 |
| `H32V2-11` | - [x] 升级合约已生效，分别由旧节点广播Legacy已撤销承诺、由新节点广播V1已撤销承诺的撤销证据，持有该承诺的一方在链上执行惩罚 | 有效撤销证据被接受：撤销交易消费承诺cell并按惩罚规则把资金归惩罚方，被撤销承诺不作为普通TLC结算成功 | 布局升级破坏撤销/惩罚分支 | P1 |
| `H32V2-12` | - [x] 分别以显式V1和旧客户端省略版本注册独立监控；保存后重启监控，触发首笔与后继结算 | V1与默认Legacy各自保持58/97和57/85布局，监控完成链上结算且不串通道 | RPC成功但丢弃版本或重启后恢复错误 | P0 |
| `H32V2-13` | - [x] 同通道两笔TLC前20字节相同但完整hash不同，仅一笔在非零解锁索引被结算；沿派生cell继续扫描并重启；另以未消费被监控outpoint的同前缀交易及不匹配快照作负对照 | 只更新精确证据指向的TLC；另一笔跨索引变化、派生扫描与重启仍待处理，直至自身兑现或超时；无关交易/快照不改变状态，无效原像不成为可用原像 | 前缀碰撞或错误快照污染付款状态 | P0 |
| `H32V2-14` | - [x] 付款尚未到期且无重试机会，Legacy对端以仅前缀有效原像完成该付款精确TLC的已确认消费；本端取得证据并核对 | 到期前有界核对窗口内付款Failed、目标TLC移除；不返回成功原像，其他链上项结算后通道收尾；不宣称追回已付链上资金 | 发起端无限保持Inflight | P0 |
| `H32V2-15` | - [x] A经诚实中继B向C付款；下游Legacy对端以仅前缀有效原像确认消费精确TLC，尚未到期、上游可通信且A无重试机会 | B在到期前向原上游TLC传播失败并移除相应入站/出站项，A最终Failed；B不缓存或传播错误原像为成功，不宣称追回已付链上资金 | 中继资金继续被上游锁定 | P0 |
| `H32V2-16` | - [x] 本端Legacy通道已进入链上关闭核对，received TLC仍未收尾且其他结算条件满足；监控持有该TLC身份准确、已确认的仅前缀匹配而完整hash错误原像消费证据 | received TLC按失败消费收尾且不fulfill；其他received TLC不受影响 | 接收侧被遗漏或错误公开原像 | P1 |
| `H32V2-17` | - [x] 分别仅有旧prefix记录且无原像/坏原像/完整hash正确原像、Exact身份或算法不符、仅本地已知原像；另设Exact无原像TLC做到期前后对照 | 弱证据不触发本次新增的立即失败；旧记录完整hash正确的目标成功、同前缀其他目标不被成功或失败处理；Exact无原像offered TLC继续遵守到期条件 | 为消除悬挂而误杀未被精确消费的TLC | P0 |
| `H32V2-18` | - [x] Legacy异常原像花费已确认且付款无重试机会，分别在失败通知完成前和处理完成后重启本端，再重复扫描同一花费；保留无关未结算TLC | 恢复后完成原TLC失败通知和移除、付款最终Failed；已处理目标不重复计账或重新挂起，无关TLC保持自身状态 | 崩溃丢失失败通知、重复计账或误结算其他TLC | P1 |
| `H32V2-19` | - [x] Legacy/V1分别经普通、外部注资、接收开通路径检查预留边界，覆盖短/长shutdown lock和CKB/UDT；充足预留分别强关结算0、1、多笔已承诺TLC | 按真实cell占用与交易长度计算；短锁CKB自动预留Legacy/V1分别99/100 CKB；扣占用后的预留恰好两倍承诺费通过该费用校验、少1 Shannon失败；充足时真实结算确认，V1每TLC witness增加12字节，锁参数等差异另计 | 预留仍按57字节或漏算每TLC新增12字节 | P1 |
| `H32V2-20` | - [x] 接收方使用默认配置、短shutdown lock和充足CKB，分别收到满足条件的V1/Legacy请求，再以显式自动出资0作对照 | 默认自动出资100 CKB，两种版本均ChannelReady且各自保持V1/Legacy；链上支出按实际出资/矿工费核对；0保持待接受，手动接受后可用 | 默认出资不足或0禁用语义被覆盖 | P0 |
| `H32V2-21` | - [x] 原进程持续运行中确认type-id合约升级，以旧旧、新旧、新新组合新开通道，混合版本交换发起方并覆盖普通/外部注资；通道就绪后强关并链上结算 | funding确认且双方ChannelReady；旧旧/新旧为Legacy、新新为V1；承诺与settle均确认，settle实际执行升级后代码且资金到账，节点全程不重启 | 仅开通或合作关闭成功却没有执行新版commitment-lock | P0 |
| `H32V2-22` | - [x] 保留升级前Legacy通道已签名但尚未广播的原承诺，节点不停机升级合约后分别由本端/对端正常强关；覆盖无TLC对照及至少两笔已承诺未完成TLC的有效原像/到期退款、CKB/xUDT，以升级后新建V1作对照 | 原承诺经兼容依赖解析广播确认且未被换成新签承诺；后续settle实际执行新代码，多笔TLC沿派生cell逐次结算且全部资金正确到账；内置Watchtower本端最终Closed并清除结算等待，无需重启 | 原承诺依赖失活、升级后强关失败或链上结束而节点仍等待 | P0 |
| `H32V2-23` | - [x] Legacy至少两笔TLC先在旧合约结算一笔，保留派生cell，节点不停机升级合约，再以有效原像或到期退款处理剩余项；覆盖CKB/xUDT并用升级后新建V1连续结算作对照 | 同一派生outpoint被新代码正确消费，Legacy/V1各自保持布局，剩余收款/退款准确且无重复或遗留应结算cell | 仅原始承诺兼容但中间结算状态不兼容 | P0 |
| `H32V2-24` | - [x] Legacy/V1通道尚无撤销记录，远端仍持有效的空TLC快照S0，pending S1已有TLC；在首次RAA前广播S0并由监控回收 | 延迟条件满足后按原承诺witness hash选中S0，保留有效空TLC清单并实际确认余额结算、资金到账；不误选S1、不等待S1到期、不因空清单跳过提款 | 正确空快照被当成不存在，双方本金卡住 | P0 |
| `H32V2-25` | - [x] Legacy/V1承诺仍含未逐笔解锁TLC，在最终余额sweep所需条件已满足后一次消费最后承诺cell，随后核对付款及上下游状态 | 最后一格cell被消费时，仍未逐笔解锁的TLC必须形成精确无原像消费证据；按实际发生的路径判定（逐笔索引unlock会收缩清单，0xfe/0xff余额sweep保留清单并一次消费），但两笔TLC都要在各自应失败的时间条件满足后收尾，不得悬挂；上游失败传播、通道关闭，且不把sweep标作付款成功 | 最后cell已花费但未逐条unlock的TLC继续悬挂 | P1 |
| `H32V2-26` | - [x] Legacy承诺在旧合约下已上链且首次settle尚未发生，节点持续运行中升级合约再结算；覆盖本端/远端承诺、CKB/xUDT、有效原像及到期退款，并以升级后新建V1作对照 | 首次及后续settle引用升级后的live代码并确认，布局保持原版本、金额正确，无遗留应结算cell且全程不重启 | 只测升级后广播或中间派生状态，遗漏已上链原始承诺升级窗口 | P0 |
| `H32V2-27` | - [x] 节点不停机升级合约后，对升级前Legacy及升级后新建Legacy/V1通道完成待处理付款，再由任一方合作关闭，覆盖CKB/xUDT | 合作关闭确认且funding已花费，双方收款与余额扣费一致、原进程最终Closed；此项为相邻回归，不算执行新版commitment-lock证据 | 遗漏原有通道合作关闭，或把关闭状态当链上到账/合约执行 | P0 |
| `H32V2-28` | - [x] MPP 发票的分片分别经 V1 与 Legacy 通道完成链下付款（同收款方、两种版本通道并存） | 收齐全部分片后收款端才公开原像，付款 Success 且原像正确；两条通道余额各按所属分片金额变动，不出现提前兑现或分片丢失 | 混合版本破坏 MPP 聚合或提前公开原像 | P0 |
| `H32V2-29` | - [x] MPP 分片分别落在 V1 与 Legacy 通道，其中一片已承诺未完成时对端强关该片所在通道并按该通道版本链上结算，其余分片仍锁定 | 未收齐全部分片前不公开原像、不标记成功；已结算分片按自身版本核对（V1 全哈希 / Legacy 20 字节前缀）；其余分片继续按自身版本等待兑现或超时，付款终态与各分片证据一致 | 用单个分片的链上结算提前完成 MPP 或泄露原像 | P0 |
| `H32V2-30` | - [x] 至少三跳链路混用 V1/Legacy（上游 V1→下游 Legacy 及反向）；付款未到期且发起端无重试机会，下游 Legacy 对端以仅前缀有效原像完成该跳精确 TLC 的已确认消费 | 中继按下游通道版本识别精确身份，在到期前跨版本向上游传播失败；各跳通道保持可用、余额不被错误扣减；发起端最终 Failed 且不获得成功原像 | 跨版本长链路中继悬挂或把错误原像当成功传播 | P0 |
| `H32V2-31` | - [x] 同一 payment_hash 的分片分属 V1 与 Legacy 通道；Legacy 片被仅前缀匹配的坏原像链上消费，同付款的另一 V1 片为已承诺未结算 TLC，另有既未兑现也未到期的分片作对照 | Legacy 片按失败处理且不写入可用原像；同一坏原像不能消费 V1 片（完整 32 字节不匹配、原 cell 仍 live、无收款）；未到期且无精确证据的分片继续等待，不被该证据失败或成功处理。不可达组合已排除：同一 hash 既在 V1 被完整原像兑现、又在 Legacy 被前缀原像消费需要 160 位第二原像，不列为预期。待确认：MPP 是否允许部分成功及付款整体终态 | 混合布局下精确身份/弱证据判定串通道，或一片证据误杀/误完成其他分片 | P1 |
| `H32V2-32` | - [x] 至少三跳链路混用 V1/Legacy（上游 V1→下游 Legacy），付款未到期且发起端无重试机会；下游 Legacy 对端以完整 32 字节 hash 正确的有效原像完成该跳精确 TLC 的已确认消费 | 中继按下游通道版本识别精确身份并证明原像完整匹配，随即向上游传播成功；发起端付款 Success 且记录的原像就是该原像，对端到账；各跳通道保持可用、余额只按该笔付款与真实手续费变动；上游 TLC 按成功路径终态收尾、不再停留在进行中状态（LocalAnnounced/Committed/RemoteRemoved），不悬挂、不按失败传播 | 以坏原像失败收尾的实现把完整原像匹配的正常消费一并失败，或跨版本成功无法上传导致资金卡住 | P0 |
<!-- TEST-CASES-END -->

## 合并后的范围约束

- 升级21/22/23/26/27共用硬前提：先建立真实旧部署状态，再在同一type-id上确认代码替换；节点全程原进程运行、不清库、不换二进制。PID之外保留启动时间与连续存活，依赖刷新不得用重启掩盖。允许哪些在线刷新操作仍待决定，不降低不重启要求。
- 实际合约验收必须消费commitment cell，不用funding确认、创建承诺输出或合作关闭替代。21/22/23/26使用CKB与xUDT；21兼顾无TLC/有效原像/到期退款，含TLC时保留已承诺未完成项。升级前仅创建Legacy样本；V1对照仅在新版代码生效后创建，绝不要求旧合约执行V1。
- 本仓库的 UDT 资产统一为 xUDT，xUDT 用例计入资产覆盖；一般费用用例 19 仍覆盖 UDT 容量。
- 03与24产品期望明确，pending指场景可达性/夹具，不是将正确版本或S0余额回收变成可选结果。暂不执行的既有反馈继续有效；普通撤销路径不替代首次RAA前S0。
- 11先核对已有SETTLE-16映射的具体版本/资产范围；只补必要差异，不重复实现或宣称该旧映射自动覆盖H32V2-11。11按人工纠正只测两个场景：旧节点广播Legacy已撤销承诺的旧证据、新节点广播V1已撤销承诺的V1旧证据，均由持有该证据的一方在新版合约下完成链上惩罚；版本只由P2P特性宣告决定，链上合约不参与选择。
- 优先级保留v1核心门槛：10超时与20默认自动接受恢复P0。05只待决定迁移方式，不将拒绝升级当成功。25最终sweep的角色/时序已按建议口径确认：最后一格cell被消费时剩余TLC必须形成精确无原像证据、不得悬挂；最后一格由逐笔索引unlock还是0xfe/0xff余额sweep消费属实现选择，不作为产品预期。

## 测试落地边界

- 优先沿用 SharedFiberTest 和既有 commitment witness 工具；用例自行建通道，强关后不复用已破坏状态。原库升级/重启按需要使用独立生命周期。
- 既有 compatibility 下的通道、持久化、独立监控、派生资产、旧库与真实合约升级测试原位复用；当前映射、独立覆盖判断与实际执行分开记录在迁移报告。既有暂停、夹具删除和待确认决定继续有效；映射迁移不自动补全旧测试缺失的资产、版本和时序矩阵。
- 合约原生 Rust 测试提供设计依据；不向本 Python 仓库添加 Rust 文件或 Python 调 cargo 包装。异常交易可在现有 Python CKB 接口上构造，是否具备完整签名/对端输入能力需在 G1 明确。
- 自动化阶段先最小确定性 witness/断言测试，再选单条已确认的 devnet；运行前固定真实 fnn/合约身份、端口、数据目录与 Python 环境。框架 CURRENT_DEV 默认路径不代表它必然就是本次 head。
- 对抗对端（本轮新增夹具）：`gpBlockchain/fiber` 的 p2p-tap debug RPC 已并入 head `8b95af3`（本地分支 `p2p-tap-full-payment-hash`：合并 `869b4469` + 仅测试用 preimage 钩子 `c857ad39`），产物 `download/fiber/attack-full-payment-hash/fnn`，来源与 SHA-256 见同目录 `provenance.env`；`FIBER_TEST_DISABLE_FULL_HASH_FEATURE=1` 时协商 Legacy，`FIBER_TEST_ALLOW_FULL_HASH_MISMATCH=1` 时该对端的 watchtower 也会构造 V1 全哈希不匹配结算。诚实路径不变（`event_handler` 传 `force=false`），`framework/test_fiber.py` 只新增该二进制入口与按节点注入环境变量。
- 已在本机 devnet 实际执行的用例：11（Legacy 旧证据方向 `test_legacy_revoked_commitment_is_punished_by_head_watchtower` 与 V1 方向 `test_v1_revoked_commitment_is_punished_by_head_watchtower`，本地日志 `report/h32v2-runs/revoked-commitment-11.log`，未随仓库提交，2 passed in 1250.82s）、21（`test_full_hash_channels.py::test_upgrade_then_settle_committed_tlc_with_new_code`，666.16s）、19（`test_commitment_fee_boundary_is_version_aware`、`test_xudt_witness_width_and_settlement_are_versioned`，后者的 CKB witness 宽度断言在内）、28（`test_full_hash_mixed_mpp.py::test_mixed_version_mpp_completes_offchain`，80.79s）。**其余新增/合并的用例仍未执行 devnet**：attack-fnn 批次（07/14/15/16/17/18）、时间跳跃批次（10/25/17 的精确身份等）、03、09/12/13 的 xUDT 与多笔矩阵、22/23/24/26/27 的新增 xUDT 变体、29~31。任何"已映射"都不等于执行通过，未执行项按未验证处理。
- 本轮验证命令：`python3 scripts/check_test_map.py`（已入库评审文件中 31 行用例、31 行有映射；退出码 1 由既有 `PMP-01..05` 与 `SETTLE-01` 孤儿映射导致，两者都引用未随本仓库提交的其他评审文档）与 `venv/bin/python -m pytest test_cases/fiber/devnet/compatibility/ --collect-only -q`（退出 0，49 项）。v2结构检查调用已安装skill中的 `check_test_design.py --root <本仓库> --review reviews/full-payment-hash-settlement-v2.md --json`。无 `--require-complete`，本轮不是全量自动化门禁。

## 历史 B 设计复核与未决事项

此前v2的B结论仅针对合并前25行，不沿用作本次设计通过。合并版已完成一次独立B复核和一次定向修订复核（isolation_unverified）；补回22的多笔连续结算和16的Legacy关闭核对前提，两项均已解决。仅设计复核，不是G2或实施批准。该复核结论只保留在本地分析目录，不随本仓库提交。原始PR和源码证据仍复用固定版本；本轮没有重新查询PR、CI或运行节点。

1. 原 27 行、15条Spec和升级共用矩阵沿用已确认版本；本轮新增的 28～31（混合版本 MPP/长链路）已按 G1 展示并获人工确认，随后进入自动化，设计独立于原B复核，ID与产品预期保持不变，确认不等于测试通过。H32V2-08（非法布局拒绝）已按"只保留 Python 集成测试场景"移出本评审，改由 fiber-scripts Rust 合约测试负责。
2. 旧库采用直接读取还是迁移；支持旧库最终恢复付款/结算的预期不放宽。
3. 03 仍无映射（缺版本观测点，且断连会清理待接受记录）；24 与异常原像输入已用新建对抗对端实现；读 watchtower 持久 preimage/settlement store 的观测点仍缺，重签仍不可行（Python 不能为 commitment lock 重签）。
4. 允许的不重启在线依赖刷新。（25最终sweep的角色/时序已确认，见上节。）

安装的v2检查器未扫描本仓库test_cases，结构诊断不当作映射覆盖；项目检查器继续报告既有PMP孤儿映射。合并版验证与变更 diff 只在本地分析目录保留，不随本仓库提交；原v2报告属于旧快照，不作为合并版新鲜证据。
