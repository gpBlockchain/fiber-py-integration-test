"""Inbound TLC delta must cover the peer's commitment delay (Fiber PR #1776)."""

import time

import pytest

from framework.basic_fiber import FiberTest


class TestTlcExpiryDelta(FiberTest):
    # TEST-MAP: F1776-01
    def test_high_commitment_delay_auto_accept_stays_pending_and_recovers(self):
        """
        1. 核对接收方自动接受配置，并发起默认 delay 的对照通道。
        2. 确认对照通道无需手动接受就在双方就绪。
        3. 发起 6 epoch 通道，确认接收方未自动接受但保留待接受 ID。
        4. 手动提供 16 小时 delta，确认同一请求在双方就绪且接收方采用该值。
        """
        # 1. 核对 B 的公开配置，并由 A 发起普通私有双向通道。
        sender = self.fiber1.get_client()
        receiver = self.fiber2.get_client()
        sender_pubkey = self.fiber1.get_pubkey()
        receiver_pubkey = self.fiber2.get_pubkey()
        funding_amount = 1000 * 100000000
        receiver_info = receiver.node_info()
        auto_funding = int(receiver_info["auto_accept_channel_ckb_funding_amount"], 16)
        auto_threshold = int(
            receiver_info["open_channel_auto_accept_min_ckb_funding_amount"], 16
        )
        assert auto_funding > 0
        assert auto_threshold <= funding_amount
        assert int(receiver_info["tlc_expiry_delta"], 16) == 4 * 60 * 60 * 1000

        sender.open_channel(
            {
                "pubkey": receiver_pubkey,
                "funding_amount": hex(funding_amount),
                "public": False,
            }
        )

        # 2. 不调用 accept_channel，确认自动接受对照在 A、B 双方就绪。
        control_id = self.wait_for_channel_state(
            sender, receiver_pubkey, "ChannelReady", 120
        )
        self.wait_for_channel_state(
            receiver, sender_pubkey, "ChannelReady", 120, channel_id=control_id
        )

        # 3. A 用合法的本地 delta 发起第二条通道；B 的默认 4 小时不足 6 epoch 门槛。
        required_delta = 16 * 60 * 60 * 1000
        opening = sender.open_channel(
            {
                "pubkey": receiver_pubkey,
                "funding_amount": hex(funding_amount),
                "public": False,
                "commitment_delay_epoch": "0x10000000006",
                "tlc_expiry_delta": hex(required_delta),
            }
        )
        pending_id = opening["temporary_channel_id"]
        for _ in range(15):
            pending = receiver.list_channels(
                {"pubkey": sender_pubkey, "only_pending": True}
            )["channels"]
            if any(channel["channel_id"] == pending_id for channel in pending):
                break
            time.sleep(1)
        else:
            assert False, f"Inbound channel {pending_id} was not left pending"

        # 4. B 对同一待接受 ID 手动设置阈值，确认最终通道及本地 delta。
        accepted = receiver.accept_channel(
            {
                "temporary_channel_id": pending_id,
                "funding_amount": hex(auto_funding),
                "tlc_expiry_delta": hex(required_delta),
            }
        )
        channel_id = accepted["channel_id"]
        self.wait_for_channel_state(
            sender, receiver_pubkey, "ChannelReady", 120, channel_id=channel_id
        )
        self.wait_for_channel_state(
            receiver, sender_pubkey, "ChannelReady", 120, channel_id=channel_id
        )
        channels = receiver.list_channels({"pubkey": sender_pubkey})["channels"]
        recovered = [
            channel for channel in channels if channel["channel_id"] == channel_id
        ]
        assert len(recovered) == 1, channels
        assert int(recovered[0]["tlc_expiry_delta"], 16) == required_delta


class TestManualTlcExpiryDelta(FiberTest):
    start_fiber_config = {"fiber_auto_accept_channel_ckb_funding_amount": 0}

    # TEST-MAP: F1776-02
    def test_inbound_accept_rejects_short_delta_then_accepts_boundary(self):
        """
        1. 关闭自动接受，发起 6 epoch 通道并确认接收方待接受。
        2. 使用默认 delta 手动接受，确认拒绝且同一请求仍待接受。
        3. 使用 16 小时减 1 ms 手动接受，确认拒绝且请求仍待接受。
        4. 使用恰好 16 小时的 delta 接受，确认双方就绪及接收方参数。
        """
        # 1. B 关闭自动接受；A 提供合法的本地 delta 发起私有双向通道。
        sender = self.fiber1.get_client()
        receiver = self.fiber2.get_client()
        sender_pubkey = self.fiber1.get_pubkey()
        receiver_pubkey = self.fiber2.get_pubkey()
        receiver_info = receiver.node_info()
        assert int(receiver_info["auto_accept_channel_ckb_funding_amount"], 16) == 0
        assert int(receiver_info["tlc_expiry_delta"], 16) == 4 * 60 * 60 * 1000

        required_delta = 16 * 60 * 60 * 1000
        opening = sender.open_channel(
            {
                "pubkey": receiver_pubkey,
                "funding_amount": hex(1000 * 100000000),
                "public": False,
                "commitment_delay_epoch": "0x10000000006",
                "tlc_expiry_delta": hex(required_delta),
            }
        )
        pending_id = opening["temporary_channel_id"]
        for _ in range(15):
            pending = receiver.list_channels(
                {"pubkey": sender_pubkey, "only_pending": True}
            )["channels"]
            if any(channel["channel_id"] == pending_id for channel in pending):
                break
            time.sleep(1)
        else:
            assert False, f"Inbound channel {pending_id} was not offered"

        accept_params = {
            "temporary_channel_id": pending_id,
            "funding_amount": hex(200 * 100000000),
        }

        # 2. 默认 4 小时不足 6 epoch 门槛；拒绝后请求仍可查询。
        with pytest.raises(Exception):
            receiver.accept_channel(accept_params)
        pending = receiver.list_channels(
            {"pubkey": sender_pubkey, "only_pending": True}
        )["channels"]
        assert any(channel["channel_id"] == pending_id for channel in pending)

        # 3. 显式低于 16 小时 1 ms 仍须拒绝，不能消耗待接受请求。
        with pytest.raises(Exception):
            receiver.accept_channel(
                {**accept_params, "tlc_expiry_delta": hex(required_delta - 1)}
            )
        pending = receiver.list_channels(
            {"pubkey": sender_pubkey, "only_pending": True}
        )["channels"]
        assert any(channel["channel_id"] == pending_id for channel in pending)

        # 4. 只把 delta 调整到边界值，确认同一请求可在双方开通。
        accepted = receiver.accept_channel(
            {**accept_params, "tlc_expiry_delta": hex(required_delta)}
        )
        channel_id = accepted["channel_id"]
        self.wait_for_channel_state(
            sender, receiver_pubkey, "ChannelReady", 120, channel_id=channel_id
        )
        self.wait_for_channel_state(
            receiver, sender_pubkey, "ChannelReady", 120, channel_id=channel_id
        )
        channels = receiver.list_channels({"pubkey": sender_pubkey})["channels"]
        recovered = [
            channel for channel in channels if channel["channel_id"] == channel_id
        ]
        assert len(recovered) == 1, channels
        assert int(recovered[0]["tlc_expiry_delta"], 16) == required_delta
