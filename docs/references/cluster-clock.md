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

`FiberTest.add_time_and_generate_epoch/block` 在启用 `cluster_clock` 时会
走 `advance_seconds`，未启用时仍走主机 `change_time()`。不要混用这两种机制；
主机调时在 ClusterClock 存活时会被拒绝。

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
4. `FiberCchTest` 的 LND/Bitcoin 进程未接入此时钟，禁止在 CCH 用例上启用
   ClusterClock。跨链 expiry 断言要求 Fiber 与 LND 处于同一真实墙钟。
5. 不要同时调用旧的主机 `change_time()` / `restore_time()`；它们仍供未启用
   虚拟时钟的 TLC timeout、v2 claim recovery 等用例使用，不得删除。
6. 支付状态轮询（如 MPP `wait_until_any_payment_failed`）的 timeout 是真实
   单调时间，与业务墙钟偏移无关，也不按倍率缩放。

## CI

[`fiber.yml`](../../.github/workflows/fiber.yml) 的 `prepare` 在 Ubuntu 22.04 上构建一次兼容
jemalloc 的 `libfaketime`，随 CKB/FNN 放进同一份准备包。各测试分组解包后都可使用
`BasicClockFiber`：需要推进时间的 devnet 用例可以留在原功能目录，仍由原分组运行。
`FIBER_TEST_FAKETIME_LIB` 只指定库的位置；普通用例和 pytest 进程仍使用真实时间。

专用分组 `fiber_test_fake_time` 继续运行时钟框架测试及 `test_cases/fiber/devnet/fake_time/`
全目录（含进程时间跳跃冒烟），不重复收集其他分组的用例。主工作流的 PR 只匹配目标分支 `main`。
