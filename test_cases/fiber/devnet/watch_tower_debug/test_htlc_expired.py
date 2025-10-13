import time

import pytest

from framework.basic_fiber import FiberTest
from framework.test_fiber import FiberConfigPath


class TestHtlcExpired(FiberTest):
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 5}
    fiber_version = FiberConfigPath.CURRENT_DEV_DEBUG

    def test_ready_status(self):
        """
        处于ready 状态
        过期tlc 会自动发送强制shutdown
        Returns:

        """
        self.start_new_fiber(self.generate_account(1000))
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1)
        self.open_channel(self.fiber2, self.fibers[2], 1000 * 100000000, 1)
        before_balance1 = self.Ckb_cli.wallet_get_capacity(
            self.account1["address"]["testnet"]
        )
        before_balance2 = self.Ckb_cli.wallet_get_capacity(
            self.account2["address"]["testnet"]
        )

        # add tlc node1
        CHANNEL_ID = self.fiber1.get_client().list_channels({})["channels"][0][
            "channel_id"
        ]
        self.fiber1.get_client().add_tlc(
            {
                "channel_id": CHANNEL_ID,
                "amount": hex(300 * 100000000),
                "payment_hash": "0x0000000000000000000000000000000000000000000000000000000000000000",
                "expiry": hex((int(time.time()) + 15) * 1000),
            }
        )
        CHANNEL_ID = (
            self.fibers[2].get_client().list_channels({})["channels"][0]["channel_id"]
        )
        # add tlc node2
        self.fiber2.get_client().add_tlc(
            {
                "channel_id": CHANNEL_ID,
                "amount": hex(300 * 100000000),
                "payment_hash": "0x0000000000000000000000000000000000000000000000000000000000000000",
                "expiry": hex((int(time.time()) + 10) * 1000),
            }
        )
        time.sleep(30)
        msg = self.get_fibers_balance_message()
        print(msg)
        channels = self.fiber1.get_client().list_channels({})
        assert len(channels["channels"]) == 0
        channels = self.fiber2.get_client().list_channels(
            {"peer_id": self.fibers[2].get_peer_id()}
        )
        assert len(channels["channels"]) == 0
        for i in range(6):
            self.node.getClient().generate_epochs("0x2")
            time.sleep(6)
        after_balance1 = self.Ckb_cli.wallet_get_capacity(
            self.account1["address"]["testnet"]
        )
        after_balance2 = self.Ckb_cli.wallet_get_capacity(
            self.account2["address"]["testnet"]
        )
        print("before_balance1:", int(before_balance1))
        print("after_balance1:", int(after_balance1))
        assert (int(after_balance1) - int(before_balance1)) == (1062)
        print("before_balance2:", int(before_balance2))
        print("after_balance2:", int(after_balance2))
        assert (int(after_balance2) - int(before_balance2)) == (1124)

    #    @pytest.mark.skip("skip")
    def test_shutdown_status(self):
        """
        处于shutdown状态
            过期tlc 会自动发送强制shutdown
        Returns:
        """
        self.start_new_fiber(self.generate_account(1000))
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1)
        self.open_channel(self.fiber2, self.fibers[2], 1000 * 100000000, 1)
        before_balance1 = self.Ckb_cli.wallet_get_capacity(
            self.account1["address"]["testnet"]
        )
        before_balance2 = self.Ckb_cli.wallet_get_capacity(
            self.account2["address"]["testnet"]
        )

        # add tlc node1
        CHANNEL_ID1 = self.fiber1.get_client().list_channels({})["channels"][0][
            "channel_id"
        ]
        self.fiber1.get_client().add_tlc(
            {
                "channel_id": CHANNEL_ID1,
                "amount": hex(300 * 100000000),
                "payment_hash": "0x0000000000000000000000000000000000000000000000000000000000000000",
                "expiry": hex((int(time.time()) + 15) * 1000),
            }
        )
        CHANNEL_ID = (
            self.fibers[2].get_client().list_channels({})["channels"][0]["channel_id"]
        )
        # add tlc node2
        self.fiber2.get_client().add_tlc(
            {
                "channel_id": CHANNEL_ID,
                "amount": hex(300 * 100000000),
                "payment_hash": "0x0000000000000000000000000000000000000000000000000000000000000000",
                "expiry": hex((int(time.time()) + 10) * 1000),
            }
        )

        # 因为有待处理的tlc 会导致正常 shutdown_channel 卡主
        self.fiber1.get_client().shutdown_channel(
            {
                "channel_id": CHANNEL_ID1,
                "close_script": self.get_account_script(self.fiber1.account_private),
                "fee_rate": "0x3FC",
            }
        )

        time.sleep(30)
        msg = self.get_fibers_balance_message()
        print(msg)
        channels = self.fiber1.get_client().list_channels({})
        assert len(channels["channels"]) == 0
        channels = self.fiber2.get_client().list_channels(
            {"peer_id": self.fibers[2].get_peer_id()}
        )
        assert len(channels["channels"]) == 0
        for i in range(6):
            self.node.getClient().generate_epochs("0x2")
            time.sleep(6)
        after_balance1 = self.Ckb_cli.wallet_get_capacity(
            self.account1["address"]["testnet"]
        )
        after_balance2 = self.Ckb_cli.wallet_get_capacity(
            self.account2["address"]["testnet"]
        )
        print("before_balance1:", int(before_balance1))
        print("after_balance1:", int(after_balance1))
        assert (int(after_balance1) - int(before_balance1)) == (1062)
        print("before_balance2:", int(before_balance2))
        print("after_balance2:", int(after_balance2))
        assert (int(after_balance2) - int(before_balance2)) == (1124)
