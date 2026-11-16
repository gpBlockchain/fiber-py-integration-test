# PR #1703：store_sync_writes 掉电耐久开关用例评审

评审范围：新增 `fiber.store_sync_writes`（yml）/ `--fiber-store-sync-writes`（CLI）/ `FIBER_STORE_SYNC_WRITES`（环境变量）开关的外部可见行为：开关是否真实生效、默认是否保持关闭、开启后节点功能是否正常。开关的掉电耐久语义本身（断电不丢最后几笔写）单机构造不出，不计入用例表。

源码版本：[PR #1703](https://github.com/nervosnetwork/fiber/pull/1703)；diff 取自 2026-11-10 GitHub API 快照（`gh pr diff 1703`）。测试仓库分支 `sunchengzhu/v0.10.0-rc2-regression`（自 `v0.10.0` 拉出，承载 rc2 全部待测 PR 的用例）；初次评审阶段未修改测试。

## 导读

- **这个改动要做什么**：给节点加一个「每次写库都等落盘」的开关。默认关闭（维持现状）；托管他人资金的节点建议打开，牺牲一些写性能换断电不丢最新通道状态。
- **行为变化**：改动前 → 写库只到操作系统缓存就返回，断电可能丢最后几笔写；改动后 → 打开开关的节点每次写都等真正落盘（RocksDB 的 sync 写、SQLite 的 `PRAGMA synchronous=FULL`），且启动日志会打印开关状态。关闭或默认时行为与旧版完全一致。
- **最怕出什么问题**：开关配了却没生效（写了但没等落盘）；默认被意外打开（所有用户白白损失写性能）；开启后正常功能（开通道、付款、重启恢复）被破坏。
- **能测到什么**：开关的生效可从启动日志 `Store sync writes: true/false` 直接观察（main.rs 新增）；开启/默认两种配置下走一遍开通道、付款、重启，验证功能无回归。yml 与环境变量两个入口都可经框架现有机制驱动（`start_fiber_config`、`start_new_fiber` 的 `env`）。
- **测不到的**：断电耐久本身 —— `kill -9` 不会丢操作系统页缓存，同一台机器上开/关的可观察行为没有差别，只有真实断电或内核崩溃才区分，黑盒构造不出；SQLite 后端的 PRAGMA 路径 —— 框架默认 RocksDB 后端，CCH 环境也不走 fnn 的 SQLite 存储。

依据：[PR #1703 描述与 diff](https://github.com/nervosnetwork/fiber/pull/1703)（配置项、默认值、启动日志、RocksDB `WriteOptions::set_sync` 均来自 diff 直接观察）。

## 需要你决定

- ~~日志断言口径与回归深度~~：已确认 —— 可以用日志断言，不需要压测，本工程是黑盒功能测试（2026-11-10，记录于 review-feedback.md）。

## 用例

公共约定：devnet；配置入口用类属性 `start_fiber_config = {"fiber_store_sync_writes": True}`（同 `test_standalone_watchtower_rpc_url.py` 的模式），环境变量入口用 `start_new_fiber(account, env={...})`。开关生效的判定：节点 `node.log` 中的 `Store sync writes: <bool>` 行（启动时打印一次）。功能回归的判定：开通道 Ready、付款 Success、重启后通道状态与余额与重启前一致。

<!-- TEST-CASES-BEGIN -->
| 用例 | 场景 | 预期结果 | 防止的问题 | 优先级 |
| --- | --- | --- | --- | --- |
| `PR1703-01` | - [x] yml 配置 `fiber_store_sync_writes: true` 启动 fiber1/fiber2，开通道并完成一笔付款，随后重启 fiber1 | 启动日志含 `Store sync writes: true`；通道 Ready、付款 Success；重启后通道保持 Ready 且余额与重启前一致 | 开关配了不生效，或开启后写路径破坏正常功能 | P1 |
| `PR1703-02` | - [x] 不配置该开关（默认）启动 fiber1/fiber2，完成一笔付款 | 启动日志含 `Store sync writes: false`；付款 Success | 默认值被意外改成开启，所有用户无感损失写性能 | P2 |
| `PR1703-03` | - [x] 以环境变量 `FIBER_STORE_SYNC_WRITES=true` 启动一个新节点并完成一笔付款 | 启动日志含 `Store sync writes: true`；付款 Success | 环境变量入口失效（部署脚本常用 env 注入配置） | P2 |
<!-- TEST-CASES-END -->

## 测试树

<!-- TEST-TREE-BEGIN -->
- store_sync_writes 开关
  - yml 配置开启
    - PR1703-01 [primary] -> SPEC-01：日志声明开启；开通道、付款、重启恢复均正常
  - 默认（未配置）
    - PR1703-02 [primary] -> SPEC-02：日志声明关闭；功能正常
  - 环境变量开启
    - PR1703-03 [primary] -> SPEC-03：日志声明开启；功能正常
  - [not_applicable] 断电不丢最后几笔写 — kill -9 不丢 OS 页缓存，开/关在单机上无可观察差别，只有真实断电才区分
  - [not_applicable] SQLite 后端的 synchronous=FULL 路径 — 框架全部使用 RocksDB 后端，无入口
  - [not_applicable] 开启后的写性能损耗 — 属性能维度，非行为契约；如需量化另立压测任务
<!-- TEST-TREE-END -->

## Spec

### SPEC-01：yml 配置被接受并声明开启，既有功能仍正常（回归测试）
- Condition: 节点配置 `fiber_store_sync_writes: true` 启动。
- Expected: 节点接受该配置并在启动日志声明开启；开通道、付款、重启恢复等既有功能不受影响。配置是否真正传入存储写路径（写等待落盘）超出黑盒可观察范围，由 PR 自带单元测试覆盖。
- Observable: `node.log` 含 `Store sync writes: true`；通道 Ready、付款 Success；重启后通道 Ready 且余额与重启前一致。
- Basis: [PR #1703 diff](https://github.com/nervosnetwork/fiber/pull/1703)（`main.rs` 的 `info!("Store sync writes: {}")`；`set_sync_writes` 在迁移前生效；RocksDB 写路径带 sync）。
- Source: explicit
- Testing: required

### SPEC-02：默认保持关闭
- Condition: 不配置该开关启动节点。
- Expected: 开关默认 false，写行为与旧版一致。
- Observable: `node.log` 含 `Store sync writes: false`；付款 Success。
- Basis: [PR #1703 diff](https://github.com/nervosnetwork/fiber/pull/1703)（`DEFAULT_STORE_SYNC_WRITES = false`）。
- Source: explicit
- Testing: required

### SPEC-03：环境变量入口同样被接受并声明开启
- Condition: 以环境变量 `FIBER_STORE_SYNC_WRITES=true` 启动节点。
- Expected: 与 yml 配置开启等效地被接受，并在启动日志声明开启。
- Observable: `node.log` 含 `Store sync writes: true`；付款 Success。
- Basis: [PR #1703 diff](https://github.com/nervosnetwork/fiber/pull/1703)（clap `env` 属性 `FIBER_STORE_SYNC_WRITES`）。
- Source: explicit
- Testing: required

## 验证设计

- 独立预期：开关语义与默认值来自 PR 描述与 diff，不来自被测二进制的行为；三条用例共用「启动日志声明 + 功能回归」oracle。
- 实际入口与观察：公开 RPC（`open_channel`、`send_payment`、`get_invoice`/`list_channels`）与运维操作（配置启动、环境变量启动、重启）；日志断言针对启动时一次性打印的开关状态行。
- 证明边界：真实构建的 devnet 节点，RocksDB 真实写路径；「写是否真等落盘」无法从外部区分，本切片只证明开关被接受并声明、功能无回归，耐久声明依赖 PR 自带单元测试（配置解析、后端传播、迁移覆盖）。

## 本轮状态

- 结构检查：`check_test_design.py --review reviews/pr1703-store-sync-writes.md` 退出码 0（3 条用例）。
- 框架改动：`source/fiber/dev_config_3.yml.j2` 新增 `store_sync_writes` 透传键（`is defined` 守卫，不影响既有配置）。
- 自动化进展：`PR1703-01/02/03` 均已实现于 `test_cases/fiber/config/test_store_sync_writes.py` 并映射。执行证据：`3 passed in 89.32s`（rc2）；`PR1703-01` 已核实生成的 config.yml 含 `store_sync_writes: True`、节点日志含 `Store sync writes: true`。
