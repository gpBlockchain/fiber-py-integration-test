# Fiber 测试模式参考

本页补充根目录 [AGENTS.md](../../AGENTS.md)，提供与当前 Python 框架对应的写法。用例仍集中在 `reviews/`，自动化仍放在 `test_cases/`；本页不是新的用例清单。

## 1. 从已确认用例进入实现

1. 定位当前评审文档与 `reviews/review-feedback.md`，只处理本次接口或连贯行为。
2. 新增、删除或实质修改用例行时，先展示完整变更行并等待明确人工确认；该轮停止在评审阶段。
3. 确认后按稳定用例 ID 查找全部 `TEST-MAP`，同步测试输入、步骤、断言与注释。
4. 新增或删除映射时同步场景复选框：`- [ ]` 表示无映射，`- [x]` 表示存在映射，均不代表测试通过。

下文 `EXAMPLE-*` ID 仅演示注释位置，不属于评审用例。将示例用于自动化前，替换为已确认的真实 ID，并按真实预期调整断言。文档示例不计入映射覆盖；别直接将示例 ID 加入测试。

## 2. 先选对环境和辅助方法

| 基类 | 生命周期 | 适用场景 |
| --- | --- | --- |
| `FiberTest` | CKB 在类级启动；每个方法初始化并清理 Fiber 节点 | 独立通道状态、余额、强关、重启、配置变化 |
| `SharedFiberTest` | CKB / Fiber / 通道在类内共享，类结束时清理 | 拓扑构造昂贵、方法间能容忍或恢复状态变化的路由和参数测试 |
| `FiberCchTest` | Fiber 环境加 BTC / LND 依赖 | CCH 跨链场景，按该基类和现有 CCH 用例准备环境 |

调试模式可能跳过部分初始化或清理；不要用调试模式的环境复用结果证明隔离性。`FiberTest` 也不是每个方法重新创建 CKB 链。

以 [basic_fiber.py](../../framework/basic_fiber.py) 和 [basic_share_fiber.py](../../framework/basic_share_fiber.py) 的实际实现为准：

| 调用 | 参数和返回值的关键区别 |
| --- | --- |
| `open_channel(f1, f2, bal1, bal2, ..., udt=None)` | 返回新通道 ID；费用参数位于 `udt` 前，传资产使用 `udt=...`。辅助方法会处理存款、自动/手动接收分支，且可能用付款配置对端余额 |
| `wait_for_channel_state(client, pubkey, state, ..., channel_id=None)` | 接收 **RPC client**；匹配 `state.state_name` 或完整 state 字典，返回通道 ID。多通道必须定位目标 ID |
| `wait_for_new_channel_state(client, pubkey, state, existing_channel_ids)` | 排除已有通道，返回达到指定状态的新通道 ID |
| `send_payment(f1, f2, amount, wait=True, udt=None, try_count=5)` | 返回 payment hash，默认等待完成，包含辅助层重试和宽松费用预算；不适合直接验证重试次数或费用上限 |
| `wait_payment_state(fiber, hash, status, timeout=360, interval=1)` | 接收 **Fiber 对象**，成功返回 `None`；遇到与预期不同的付款终态会立即抛错 |
| `wait_invoice_state(fiber, hash, status, timeout=120, interval=1)` | 同样接收 **Fiber 对象**，成功返回 `None` |

付款和发票等待中的 `timeout` 当前是循环次数，`interval` 是每次间隔，并非严格的墙钟秒数。涉及精确时限时，单独使用单调时钟和有界等待，并考虑 RPC 调用耗时。

当前框架用 `"ChannelReady"` 匹配 RPC 通道状态；不要将协议内部的 `CHANNEL_READY` 常量直接传入。付款常见状态为 `Created` / `Inflight` / `Success` / `Failed`；发票为 `Open` / `Received` / `Paid` / `Cancelled` / `Expired`，具体版本以实际 RPC 契约为准。

## 3. 最小开通道与付款示例

下面是完整的类示例。基础环境已提供 `self.node`、两个已连接的 Fiber 节点和 UDT 合约。正常业务路径优先复用框架；若正在验证原始 funding 参数、重试或费用限制，则直接调用相应 RPC。

```python
from framework.basic_fiber import FiberTest


class TestDirectPayment(FiberTest):
    # TEST-MAP: EXAMPLE-01
    def test_direct_payment(self):
        channel_id = self.open_channel(
            self.fiber1, self.fiber2, 1000 * 100000000, 0
        )
        self.wait_for_channel_state(
            self.fiber2.get_client(), self.fiber1.get_pubkey(),
            "ChannelReady", channel_id=channel_id,
        )

        payment_hash = self.send_payment(
            self.fiber1, self.fiber2, 1 * 100000000
        )
        payment = self.fiber1.get_client().get_payment(
            {"payment_hash": payment_hash}
        )
        assert payment["status"] == "Success"
```

CKB 金额单位为 Shannon，`1 CKB = 100000000 Shannon`；直接 RPC 按接口要求传十六进制字符串。`open_channel` 的余额参数经过框架处理，不等于原始 RPC 的 `funding_amount`；边界测试应观察原始请求和实际结果。

## 4. 手动接收：等待可观察状态，不用占位调用

下面的独立环境只有一个待协商通道，所以在最终 ID 生成前可按对端公钥等待；同一对端有多个通道时，保存已有 ID 并使用 `wait_for_new_channel_state`，别选 `channels[0]`。

```python
from framework.basic_fiber import FiberTest


class TestManualAccept(FiberTest):
    start_fiber_config = {"fiber_auto_accept_amount": "0"}

    # TEST-MAP: EXAMPLE-02
    def test_manual_accept(self):
        temporary = self.fiber1.get_client().open_channel({
            "pubkey": self.fiber2.get_pubkey(),
            "funding_amount": hex(200 * 100000000),
            "public": True,
        })
        self.wait_for_channel_state(
            self.fiber2.get_client(), self.fiber1.get_pubkey(),
            "NegotiatingFunding",
        )
        self.fiber2.get_client().accept_channel({
            "temporary_channel_id": temporary["temporary_channel_id"],
            "funding_amount": hex(100 * 100000000),
        })
        channel_id = self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_pubkey(), "ChannelReady"
        )
        self.wait_for_channel_state(
            self.fiber2.get_client(), self.fiber1.get_pubkey(),
            "ChannelReady", channel_id=channel_id,
        )
```

如果目标是“自动接收是否发生”，直接发送开通道 RPC 并断言对端行为；不要用可能执行手动接收的 `open_channel` 辅助方法替代被测操作。

## 5. 共享多跳拓扑：成功后再设置守卫

```python
from framework.basic_share_fiber import SharedFiberTest


class TestSharedRoute(SharedFiberTest):
    def setUp(self):
        cls = self.__class__
        if getattr(cls, "_channel_inited", False):
            return

        cls.fiber3 = self.start_new_fiber(self.generate_account(10000))
        # A -- B -- C；各通道初始资金足以支持下述两个方法。
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0)
        channel_id = self.open_channel(
            self.fiber2, self.fiber3, 1000 * 100000000, 0
        )
        self.wait_for_channel_state(
            self.fiber3.get_client(), self.fiber2.get_pubkey(),
            "ChannelReady", channel_id=channel_id,
        )
        cls._channel_inited = True

    # TEST-MAP: EXAMPLE-03
    def test_keysend(self):
        payment_hash = self.send_payment(
            self.fiber1, self.fiber3, 1 * 100000000
        )
        result = self.fiber1.get_client().get_payment(
            {"payment_hash": payment_hash}
        )
        assert result["status"] == "Success"

    # TEST-MAP: EXAMPLE-04
    def test_invoice_payment(self):
        payment_hash = self.send_invoice_payment(
            self.fiber1, self.fiber3, 1 * 100000000
        )
        result = self.fiber1.get_client().get_payment(
            {"payment_hash": payment_hash}
        )
        assert result["status"] == "Success"
```

- 额外节点存到类上，供后续方法复用；初始化失败时守卫仍为假，部分已创建资源仍需按框架生命周期清理，避免盲目重跑初始化。
- 方法应能单独执行，且不依赖另一方法先付款或先开通道；共享的是拓扑，不是隐含的执行顺序。
- 多跳路由传播可能异步完成。上述正常业务示例允许使用付款辅助方法的重试；gossip 同步、首次发送或重试行为测试应显式等待对应图状态，再用直接 RPC 观察结果。
- 单方法多跳测试也可以用同样的开通道步骤，继承 `FiberTest` 并将第三节点保存在局部变量即可。
- 涉及强关、撤销承诺、严格初始余额或破坏性配置时优先选 `FiberTest`，而非让共享类的后续方法继承破坏状态。

## 6. Hold Invoice：分别验证发票与付款终态

```python
import hashlib

from framework.basic_fiber import FiberTest


class TestHoldInvoice(FiberTest):
    # TEST-MAP: EXAMPLE-05
    def test_settle_hold_invoice(self):
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0)
        preimage = self.generate_random_preimage()
        payment_hash = "0x" + hashlib.sha256(
            bytes.fromhex(preimage.removeprefix("0x"))
        ).hexdigest()
        invoice = self.fiber2.get_client().new_invoice({
            "amount": hex(1 * 100000000),
            "currency": "Fibd",
            "description": "hold invoice example",
            "expiry": "0xe10",
            "final_cltv": "0x28",
            "payment_hash": payment_hash,
            "hash_algorithm": "sha256",
        })
        payment = self.fiber1.get_client().send_payment(
            {"invoice": invoice["invoice_address"]}
        )
        assert payment["payment_hash"] == payment_hash
        self.wait_invoice_state(self.fiber2, payment_hash, "Received", 120, 1)

        self.fiber2.get_client().settle_invoice({
            "payment_hash": payment_hash,
            "payment_preimage": preimage,
        })
        self.wait_payment_state(self.fiber1, payment_hash, "Success", 120, 1)
        self.wait_invoice_state(self.fiber2, payment_hash, "Paid", 120, 1)
```

Hold 阶段先用直接 RPC 发起付款，避免等待 `Success` 的辅助方法阻塞后续 `settle_invoice`。发票只给 hash，settle 时才给 preimage；哈希算法保持一致。此例是单笔结算流程，不证明 MPP、过期、重启恢复或原像公开边界；这些行为需要各自已确认的用例。

## 7. UDT 与余额观察

以下片段放在 `FiberTest` 方法中，使用基础环境已发行的测试 UDT；先确认余额和白名单配置足够。UDT 金额是该资产的基本单位，下列数值沿用测试资产规模，不表示所有 UDT 都采用 CKB 的换算关系。

```python
udt_script = self.get_account_udt_script(self.fiber1.account_private)
channel_id = self.open_channel(
    self.fiber1, self.fiber2, 200 * 100000000, 0, udt=udt_script
)
payment_hash = self.send_payment(
    self.fiber1, self.fiber2, 10 * 100000000, udt=udt_script
)
result = self.fiber1.get_client().get_payment({"payment_hash": payment_hash})
assert result["status"] == "Success"
```

余额断言要先明确口径，并给出可验证的预期，而非只输出差额：

- `get_fibers_balance()` 按当前 `self.fibers` 顺序取快照；前后应使用同一组节点、同一资产和可比时点。
- `get_balance_change(before, after)` 当前只计算 `chain.ckb` / `chain.udt`，方向为 **before − after**：正数是链上减少，负数是链上增加。它不是通道余额变化，也不是总资产守恒断言。
- 付款后的通道余额、在途 TLC 和路由费应按目标 `channel_id`、方向与资产分别检查。稳定余额断言前等待付款及相应 TLC 状态收敛。
- 开关通道的存款、交易费和找零应与付款本身分开解释；实际交易确认和结算时点由具体用例决定。

## 8. 重启与有界等待

下面片段放在 `FiberTest` 方法中。正常 `stop()` / `start()` 已等待 RPC 端口变化，但端口可达不代表通道重建完成；重启时保留数据目录，不调用 `prepare()` / `clean()`。

```python
import time

channel_id = self.open_channel(
    self.fiber1, self.fiber2, 1000 * 100000000, 0
)
self.fiber1.stop()
self.fiber1.start()
self.fiber1.connect_peer(self.fiber2)

peers = []
for _ in range(30):
    peers = self.fiber1.get_client().list_peers()["peers"]
    if any(peer["pubkey"] == self.fiber2.get_pubkey() for peer in peers):
        break
    time.sleep(1)
else:
    assert False, f"Peer did not reconnect within 30 polls: {peers}"

self.wait_for_channel_state(
    self.fiber1.get_client(), self.fiber2.get_pubkey(),
    "ChannelReady", channel_id=channel_id,
)
payment_hash = self.send_payment(self.fiber1, self.fiber2, 1 * 100000000)
result = self.fiber1.get_client().get_payment({"payment_hash": payment_hash})
assert result["status"] == "Success"
```

这验证正常重启后的可用性，不等于进程崩溃、自动重连或在途付款恢复：本例主动调用了 `connect_peer`，并在重启后新建付款。验证旧付款恢复时，应保存原 hash，并对同一笔付款观察；不要重新发送成功后就判定旧付款已恢复。

## 9. 强关、Watchtower 与失败断言

强关结算没有通用的“挖一个 epoch 后取第一笔交易”模板。参考 [强关测试](../../test_cases/fiber/devnet/shutdown_channel/test_force.py)，按已确认用例线性编排：

1. 保存目标通道 ID、资金交易以及用例相关 TLC / 付款标识。
2. 对目标 ID 发起 `shutdown_channel({"channel_id": channel_id, "force": True})`。
3. 定位属于该通道的关闭交易并确认；若使用 `wait_and_check_tx_pool_fee`，注意它当前返回池中第一笔 pending 交易，并非按通道筛选。
4. 根据实际合约、相对时间锁和 TLC 到期条件推进链，再确认对应结算交易；不要写死适用于所有配置的 epoch 数。
5. 按用例观察关闭状态、结算是否完成、付款/发票结果和资金可用性；查已关闭通道时显式使用 `include_closed=True`，多通道同时指定 ID。
6. 对需要恢复验证的场景，再检查重启后的同一状态与副作用，避免仅凭进程存活或一笔交易上链判断完成。

配置通过 `start_fiber_config` 覆盖；键名和类型先核对 [配置模板](../../source/fiber/dev_config_3.yml.j2)，观察实际节点配置，而非仅确认 Python 字典赋值成功。

失败路径用 `pytest.raises(Exception)` 包住被测调用即可表达“调用应失败”；若契约还要求余额、通道数、付款或进程状态不变，继续断言这些可观察结果。只在错误文本本身属于契约时检查具体文案，不列一串互相替代的错误片段。

## 10. 最小验证与 CI

所有命令在仓库根目录、项目现有 Python 环境执行。先确认节点二进制、端口和数据目录，再跑需要节点的测试。

```bash
# 单条现有用例；会启动本地节点
python -m pytest test_cases/fiber/devnet/open_channel/test_funding_amount.py::FundingAmount::test_funding_amount_ckb_is_zero -v -s

# 聚焦文件通过后，再根据变更范围扩大测试
python -m pytest test_cases/fiber/devnet/open_channel/test_funding_amount.py -v -s

# 映射校验；不启动节点
python3 scripts/check_test_map.py

# 仅在范围需要时运行 Makefile 列出的分类，并非全部 devnet
make fiber_test
```

- 文档专用改动：检查 Python 示例语法、辅助方法签名、相对链接和 `git diff --check`，再运行映射检查；不为此启动节点。
- 检查器退出码 0 表示没有它识别的映射错误，不代表复选框、断言语义或实际运行通过。仅在检查范围预期全部自动化时使用 `--require-complete`。
- 新分类检查 [Makefile](../../Makefile) 的显式列表和 [fiber.yml](../../.github/workflows/fiber.yml) 的实际任务，避免记录易过期的并行任务数。CI 默认只检查一次。
- 交接记录实际命令、关键原始输出和退出码；区分静态校验、测试收集与真正的节点执行。未运行的场景、环境限制和人工待确认项写入残余风险。

合约、上游节点代码和 P2P 测试分支入口统一见 [AGENTS.md](../../AGENTS.md)；相关背景只按本次行为读取，区分被测实现与测试分支扩展。
