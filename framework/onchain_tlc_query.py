"""开关：链上处理完 TLC 后，是否还要等节点查回查询状态（get_payment / list_channels）。

链上消费确认后，付款/TLC 的查询状态要等节点自身的链上扫描把结果写回，实测可以达到数分钟
（这类等待是 compatibility 目录里 CI 耗时的主要来源）。所以这类**只由链上事件触发**的终态
判断默认不跑：

- 关（默认）：跳过 ``wait_payment_state`` / ``wait_tlc_terminal`` 这类等待，也不断言被门控的
  查询终态；测试仍会执行链上资金核对（到账/扣费/本金守恒），资金是否丢失照常验证。
- 开：按原样等待并断言上链后的付款 Success/Failed、发票 Paid、TLC 终态。

只作用于"上链处理完 TLC 才变更"的查询；链下即时可见的状态（Inflight / Received、链下完成的
MPP Success 等）不受影响，始终等待并断言。

打开方式（任选其一，值大小写不敏感）::

    FIBER_ASSERT_ONCHAIN_TLC_QUERY=true python -m pytest ...
    FIBER_ASSERT_ONCHAIN_TLC_QUERY=1    python -m pytest ...
"""

from __future__ import annotations

import os

#: 环境变量名；只有它被显式设为真值时才断言上链后的查询终态。
ONCHAIN_TLC_QUERY_ENV = "FIBER_ASSERT_ONCHAIN_TLC_QUERY"

_TRUE_VALUES = {"1", "true", "yes", "on"}


def onchain_tlc_query_enabled() -> bool:
    """是否等待并断言"上链处理完 TLC"之后的查询状态；默认关闭以保证 CI 速度。

    每次调用都读环境变量，避免在 import 时把结果固化下来。
    """
    return os.environ.get(ONCHAIN_TLC_QUERY_ENV, "").strip().lower() in _TRUE_VALUES
