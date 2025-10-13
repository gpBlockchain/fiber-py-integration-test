import time

import pytest

from framework.basic_fiber import FiberTest


class TestEnable(FiberTest):
    """
    1. 可用路径中存在禁用路径, 能够避开禁用路径
    2. 可用路径中 全都被禁用, send_payment (dry_run = true) 返回错误 ，send_payment 返回错误
    3.
    """

    # FiberTest.debug = True

    # @pytest.mark.skip("https://github.com/nervosnetwork/fiber/issues/499")
    def test_true(self):
        """
        A-B-C
        0. A->C 不会报错
        1. B.update_channel(id:B-C,enable:False)
        2. A->C 报错
        3. B->C 报错
        4.  B->A 不会报错 , C->B 不会报错
        5. C->A 不会报错
        6. B.update_channel(id:B-C,enable:True)
        7. A->C 不会报错
        8. B->C 不会报错
        Returns:
        """
        self.start_new_fiber(self.generate_account(10000))
        self.open_channel(
            self.fibers[0], self.fibers[1], 1000 * 100000000, 1000 * 100000000
        )
        self.open_channel(
            self.fibers[1], self.fibers[2], 1000 * 100000000, 1000 * 100000000
        )
        self.send_payment(self.fibers[0], self.fibers[2], 1 * 100000000)

        # 1. B.update_channel(id:B-C,enable:False)
        channel = (
            self.fibers[1]
            .get_client()
            .list_channels({"peer_id": self.fibers[2].get_peer_id()})
        )
        channels = self.fibers[1].get_client().graph_channels({})
        assert len(channels["channels"]) == 2
        self.fibers[1].get_client().update_channel(
            {"channel_id": channel["channels"][0]["channel_id"], "enabled": False}
        )
        time.sleep(3)
        channels = self.fibers[1].get_client().graph_channels({})
        assert len(channels["channels"]) == 2

        channel = (
            self.fibers[1]
            .get_client()
            .list_channels({"peer_id": self.fibers[2].get_peer_id()})
        )
        print("fiber1 channel:", channel)
        assert channel["channels"][0]["enabled"] == False
        channel = (
            self.fibers[2]
            .get_client()
            .list_channels({"peer_id": self.fibers[1].get_peer_id()})
        )
        print("fiber2 channel:", channel)
        assert channel["channels"][0]["enabled"] == True

        # 2. A->C 报错
        with pytest.raises(Exception) as exc_info:
            self.send_payment(self.fibers[0], self.fibers[2], 1)
        expected_error_message = "no path found"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )
        # 3. B->C 报错
        with pytest.raises(Exception) as exc_info:
            self.send_payment(self.fibers[1], self.fibers[2], 1)
        expected_error_message = "no path found"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )
        # 4. B->A 不会报错 , C->B 不会报错
        self.send_payment(self.fibers[1], self.fibers[0], 1)
        self.send_payment(self.fibers[2], self.fibers[1], 1)

        # 5. C->A 不会报错
        self.send_payment(self.fibers[2], self.fibers[0], 1)

        # 6. B.update_channel(id:B-C,enable:True)
        channel = (
            self.fibers[1]
            .get_client()
            .list_channels({"peer_id": self.fibers[2].get_peer_id()})
        )
        self.fibers[1].get_client().update_channel(
            {"channel_id": channel["channels"][0]["channel_id"], "enabled": True}
        )
        time.sleep(1)
        channels = self.fibers[1].get_client().graph_channels({})
        print("after true graph_channels:", channels)
        assert len(channels["channels"]) == 2
        channel = (
            self.fibers[1]
            .get_client()
            .list_channels({"peer_id": self.fibers[2].get_peer_id()})
        )
        print("fiber1 channel:", channel)
        assert channel["channels"][0]["enabled"] == True
        channel = (
            self.fibers[2]
            .get_client()
            .list_channels({"peer_id": self.fibers[1].get_peer_id()})
        )
        print("fiber2 channel:", channel)
        assert channel["channels"][0]["enabled"] == True
        # 7. A->C 不会报错
        # 8. B->C 不会报错
        self.send_payment(self.fibers[0], self.fibers[2], 1)
        self.send_payment(self.fibers[1], self.fibers[2], 1)
        self.send_payment(self.fibers[1], self.fibers[0], 1)
        self.send_payment(self.fibers[2], self.fibers[1], 1)
        self.send_payment(self.fibers[2], self.fibers[0], 1)

    # 可用路径中 全都被禁用, send_payment (dry_run = true) 返回错误 ，send_payment 返回错误
    def test_channels_enabled_fee_more(self):
        """
        1. A-B-C
        2. B-C 建立多个channel
        3. 将fee 比较低的channel 都禁用掉
        """
        self.start_new_fiber(self.generate_account(10000))
        self.open_channel(
            self.fibers[0], self.fibers[1], 1000 * 100000000, 1000 * 100000000
        )
        self.open_channel(
            self.fibers[1], self.fibers[2], 1000 * 100000000, 1000 * 100000000
        )

        channel = (
            self.fibers[1]
            .get_client()
            .list_channels({"peer_id": self.fibers[2].get_peer_id()})
        )
        self.fibers[1].get_client().update_channel(
            {"channel_id": channel["channels"][0]["channel_id"], "enabled": False}
        )
        self.open_channel(
            self.fibers[1],
            self.fibers[2],
            1000 * 100000000,
            1000 * 100000000,
            20000,
            20000,
        )
        self.open_channel(
            self.fibers[1],
            self.fibers[2],
            1000 * 100000000,
            1000 * 100000000,
            30000,
            30000,
        )

        self.send_payment(self.fibers[0], self.fibers[2], 1 * 100000000)
