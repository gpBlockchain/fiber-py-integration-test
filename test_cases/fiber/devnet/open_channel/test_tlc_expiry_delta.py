import time

import pytest

from framework.basic_fiber import FiberTest


class TestTlcLocktimeExpiryDelta(FiberTest):
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 5}

    @pytest.mark.skip("todo")
    def test_tlc_expiry_delta_none(self):
        """
        tlc_expiry_delta = none
        Returns:
        """
        # self.test_linked_peer()

    def test_tlc_expiry_delta_is_zero_or_bigger(self):
        """
        tlc_expiry_delta = 0
        Returns:
        """
        with pytest.raises(Exception) as exc_info:
            temporary_channel_id = self.fiber1.get_client().open_channel(
                {
                    "peer_id": self.fiber2.get_peer_id(),
                    "funding_amount": hex(200 * 100000000),
                    "public": True,
                    "tlc_expiry_delta": "0x0",
                    # "tlc_fee_proportional_millionths": "0x4B0",
                }
            )
        expected_error_message = "TLC expiry delta is too small, expect larger than"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

        with pytest.raises(Exception) as exc_info:
            self.fiber1.get_client().open_channel(
                {
                    "peer_id": self.fiber2.get_peer_id(),
                    "funding_amount": hex(200 * 100000000),
                    "public": True,
                    "tlc_expiry_delta": "0xffffffff",
                    # "tlc_fee_proportional_millionths": "0x4B0",
                }
            )
        expected_error_message = "expected to be smaller than"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

    @pytest.mark.skip("todo")
    def test_tlc_expiry_delta_is_1(self):
        """
        tlc_expiry_delta = 1
        Returns:
        todo:
            qa: open_channel 的 tlc_expiry_delta 作用是什么呢？ 有点没理解 @by  我要怎么才能测到这个参数
             A 给 B 发送一个 tlc，如果 B 知道原相，那 B 可以取走 tlc 里面的资金，否则过了时间 tlc_expiry_delta 之后，A 可以取回 tlc 里面的资金。
            那a 可以怎么取回tlc的资金
            要在 watchtower 里面做，我们现在似乎没有这个功能
        """

    @pytest.mark.skip("todo")
    def test_tlc_expiry_delta_not_eq_default(self):
        """
        tlc_expiry_delta != default

        Returns:

        """
