# CKB + Fiber 共用测试时间（可选）

仅用于本地 devnet。无需修改 CKB/FNN 二进制；需要为运行系统安装同架构的
`libfaketime` 动态库（macOS `.dylib`，Linux `.so.1`），并先验证它能拦截
当前二进制的时间读取。

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
Ubuntu 使用 `sudo apt-get install libfaketime`。标准安装路径会自动发现，
PyCharm 无需额外设置环境变量。自定义安装路径可设置
`FIBER_TEST_FAKETIME_LIB=/绝对路径/libfaketime.1.dylib`
（Linux 使用 `.so.1`）。`BasicClockFiber` 继承 `SharedFiberTest`，提供
`advance_time_by(seconds=14400, mine_epochs=1)`、
`advance_time_to(unix_time_ms, mine_epochs=1)` 和
`wait_chain_median_time(target_ms)`。不同方法自行创建所需状态，时间只向前推进。
直接调用 `self.advance_time_by()` 会同时推进 4 小时和 CKB 1 个 epoch；
跨越更多 epoch 时显式传入相应的 `mine_epochs`。

`FiberTest`、`SharedFiberTest` 自动把同一时间文件交给前两个 FNN、CKB node、
CKB miner 和 `start_new_fiber()` 创建的 FNN。自行创建的其他进程需在启动前
设置 `cluster_clock.process_env()`。Python 手动产块的区块头时间戳也取自同一
`cluster_clock.now_ms()`。普通测试未启用此机制，保持原行为。

## 验证与边界

1. 先在 macOS/Linux 各跑单节点冒烟：检查 FNN 新发票的时间、CKB 新区块头
   时间以及 RPC 连接；再跑目标 TLC 用例。这里的框架单元测试只检查接线，
   不等于实际动态库与二进制已通过兼容性验证。
2. 时间只向前推进。推进后产足够区块，读取链头和 `get_block_median_time`，
   确认链上时间条件已满足；仅改变进程时间不会自动改变既有链历史。
3. pytest 进程使用真实时间，等待上限用 `time.monotonic()`。默认保留进程的
   单调时钟，避免影响 Tokio/CKB 定时器；因此已安排的长定时器仍按真实时间
   触发。需要验证此类调度器时，单独使用真实短超时测试。
4. `FiberCchTest` 的 LND/Bitcoin 进程未接入此时钟，跨链用例需另行协调。
5. 不要同时调用旧的 `change_time()`；它会改变机器时间，与本机制混用。

## CI

`.github/workflows/fiber.yml` 的 `fiber_test_fake_time` job 使用 Linux/macOS
矩阵安装 `libfaketime` 并运行框架测试及实时进程的 4 小时时间跳跃冒烟。
Linux runner 复用 `prepare` 产出的 CKB/FNN 二进制，执行
`test_cases/fiber/devnet/fake_time/` 全目录；后续使用 `BasicClockFiber` 的
devnet 时间用例直接放在这里，就会由该 job 执行。macOS runner 只执行
动态库冒烟，当前不会运行需要 CKB/FNN 二进制的 devnet 用例。
工作流监听 `main` 和 `v0.10.0` 的 PR。
