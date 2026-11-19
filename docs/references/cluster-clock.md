# CKB + Fiber 共用测试时间（可选）

仅用于本地 devnet。无需修改 CKB/FNN 二进制；需要为运行系统安装同架构的
`libfaketime` 动态库（macOS `.dylib`，Linux `.so.1`），并先验证它能拦截
当前二进制的时间读取。

## 业务时间与调度真实时间

这套机制只推进 **业务墙钟**（发票、订单、TLC expiry、区块头 `timestamp`、
`get_block_median_time`）。实现是共享的整秒偏移文件（`+N` 秒），不是倍率
加速（不支持 `x2` / `@绝对时间` / 回退）。

pytest 等待、RPC/端口超时、支付轮询、进程启动确认仍使用 **调度真实时间**
（`framework.cluster_clock.scheduling_now()`，即 `time.monotonic()`）。
`FAKETIME_DONT_FAKE_MONOTONIC=1`，因此 Tokio/CKB 已安排的定时器也不会被
偏移加速。不要把网络超时或 `time.sleep` 按倍率缩放。

读取业务墙钟用 `cluster_clock.now_ms()` / `now_s()`，不要用 pytest 进程的
`time.time()` 去构造需要与 FNN/CKB 一致的时间戳。

## 在时间相关用例中使用

```python
from framework.basic_clock_fiber import BasicClockFiber


class TestTlcWithClusterClock(BasicClockFiber):
    def advance_past_expiry(self, payment_hash):
        # 调用前先创建支付；此方法只推进该 TLC 的时间。
        pending = self.get_pending_tlc(self.fiber2, payment_hash)
        tlc_expiry_ms = int(pending["Inbound"][0]["tlc"]["expiry"], 16)
        self.advance_time_to(tlc_expiry_ms + 1000, mine_epochs=1)
        self.wait_chain_median_time(tlc_expiry_ms)
        # 用例随后以真实时间的有界轮询观察链和 FNN 状态。
```

本地先安装 `libfaketime`：macOS 使用 `brew install libfaketime`，
Ubuntu 使用 `sudo apt-get install libfaketime`（CI Linux 会构建与 jemalloc
兼容的 pinned 版本）。标准安装路径会自动发现，自定义路径可设置
`FIBER_TEST_FAKETIME_LIB`。`BasicClockFiber` 继承 `SharedFiberTest`，提供
`advance_time_by(seconds=14400, mine_epochs=1)`、
`advance_time_to(unix_time_ms, mine_epochs=1)` 和
`wait_chain_median_time(target_ms)`（后者的 timeout 是真实单调时间）。
时间只向前推进。直接调用 `self.advance_time_by()` 会同时推进 4 小时和
CKB 1 个 epoch；跨越更多 epoch 时显式传入相应的 `mine_epochs`。

原有会调时的用例在测试类上设置 `enable_cluster_clock = True`，通过
`FiberTest.advance_business_time` / `advance_business_to_ms` /
`add_time_and_generate_epoch/block` 推进业务墙钟。主机
`change_time()` / `restore_time()` 已关闭（调用立即失败），避免遗漏迁移
时静默改宿主时钟。未调时的普通用例保持默认 `enable_cluster_clock = False`，
不注入 `FAKETIME` / `LD_PRELOAD`。

调试时可在子类中设置 `debug = True`。首次运行会保留 CKB/FNN 进程和
`tmp/clock-fiber/cluster-clock/` 中的时钟文件；再次运行连接原进程并读取上次的时间偏移，
不会从零开始。若调试进程已退出但链数据仍在，重启也会通过同一时间戳文件延续该偏移。
CKB `restart()` 保留 `node.virtual_clock`，FNN `start()` 从已有 `extra_env`
继承时钟变量。若端口上已有旧版调试进程却没有对应的持久时钟记录，
先停止这组旧进程，再运行一次以建立记录；新集群使用独立的
`tmp/clock-fiber/` 数据目录，框架不会把新时钟误接到旧进程。

`FiberTest`、`SharedFiberTest` 自动把同一时间文件交给前两个 FNN、CKB node、
CKB miner 和 `start_new_fiber()` 创建的 FNN。自行创建的其他进程需在启动前
调用 `cluster_clock.apply_to_env(...)` 或 `merge_env()`。Python 手动产块的
区块头时间戳也取自同一 `cluster_clock.now_ms()`。普通测试未启用此机制，
`Fiber.start` / `CkbNode.start` 不会注入 `FAKETIME` 或 `LD_PRELOAD`。

## 验证与边界

1. 本地可在 Linux 或 macOS 做单节点冒烟：检查 FNN 新发票的时间、CKB 新区块头
   时间以及 RPC 连接；再跑目标 TLC 用例。框架单元测试只检查接线，
   不等于实际动态库与二进制已通过兼容性验证。CI 的 `fiber_test_fake_time`
   仅在 Linux 上运行（不跑 macOS）。
2. 时间只向前推进。推进后产足够区块，读取链头和 `get_block_median_time`，
   确认链上时间条件已满足；仅改变进程时间不会自动改变既有链历史。
3. pytest 进程使用真实调度时间。需要验证 Tokio/CKB 长定时器时，单独使用
   真实短超时测试，不要指望偏移会加速这些定时器。
4. CCH 默认仍关闭 ClusterClock，官方 CCH CI 继续使用官方 LND 二进制。
   需要跳墙钟的 CCH 用例设置 `enable_cluster_clock = True`，并使用测试仓库
   内的 **测试专用** file-offset-clock LND（`tools/build_lnd_file_clock.sh`，
   产物在 `download/lnd-file-clock/`）。官方 LND 是静态链接，`LD_PRELOAD`
   libfaketime 无效。bitcoind 通过 `setmocktime` 接入（冻结、重启丢失，
   需在启动后重新 apply）。部分失败会抛 `ClockPropagationError`，不会静默
   降级。Fiber CCH 调度器用 tokio `send_after`（真实单调时间，最长 1h），
   跳墙钟不会立刻唤醒已在跑的 tick；验证过期常需重启 Fiber，让
   `update_if_expired` 按新的业务墙钟生效。这是产品限制，不在 Fiber 产品
   仓库里改。
5. 不要调用已关闭的主机 `change_time()` / `restore_time()`。业务时间比较
   用 `business_now_s()` / `business_now_ms()`；pytest 等待仍用
   `time.monotonic()` / `scheduling_now()`。
6. 支付状态轮询（如 MPP `wait_until_any_payment_failed`）的 timeout 是真实
   单调时间，与业务墙钟偏移无关，也不按倍率缩放。

## CI

`prepare` 会运行 `tools/install_libfaketime.sh`，把与 jemalloc 兼容的
pinned `libfaketime` 打进 `download/libfaketime/` 产物。
`fiber_test_fake_time` job 只在 `ubuntu-22.04` 上运行框架测试及实时进程的
4 小时时间跳跃冒烟，复用该产物和 CKB/FNN 二进制，执行
`test_cases/fiber/devnet/fake_time/` 全目录。macOS runner 不在此矩阵中。
工作流监听 `main` 和 `v0.10.0` 的 PR。需要跳墙钟的 CCH 用例还依赖
`tools/build_lnd_file_clock.sh` 产出的测试专用 LND。
