"""PR #1774: pending-only channel listing keeps ready channels out."""

import time

from framework.basic_fiber import FiberTest


class TestPR1774PendingFilter(FiberTest):
    start_fiber_config = {"fiber_auto_accept_channel_ckb_funding_amount": 0}

    # TEST-MAP: F1774-01
    def test_only_pending_excludes_ready_when_opening_channel_exists(self):
        """
        1. 手动接受第一条通道，确认它已进入 ChannelReady。
        2. 再打开一条合法金额的通道但不接受，保留 opening 状态。
        3. 检查 only_pending 只返回 opening 通道，不返回 ready 通道。
        4. 检查默认列表仍能返回已就绪的通道。
        """
        # 1. 手动接受第一条通道，并记录其已就绪 ID。
        client = self.fiber1.get_client()
        peer_pubkey = self.fiber2.get_pubkey()
        first_opening = client.open_channel(
            {
                "pubkey": peer_pubkey,
                "funding_amount": hex(1000 * 100000000),
                "public": True,
            }
        )
        first_opening_id = first_opening["temporary_channel_id"]
        for _ in range(15):
            inbound = self.fiber2.get_client().list_channels({"only_pending": True})[
                "channels"
            ]
            if any(channel["channel_id"] == first_opening_id for channel in inbound):
                break
            time.sleep(1)
        else:
            assert False, f"Inbound channel {first_opening_id} was not offered"

        min_funding = int(
            self.fiber2.get_client().node_info()[
                "open_channel_auto_accept_min_ckb_funding_amount"
            ],
            16,
        )
        self.fiber2.get_client().accept_channel(
            {
                "temporary_channel_id": first_opening_id,
                "funding_amount": hex(min_funding),
            }
        )
        ready_id = self.wait_for_channel_state(client, peer_pubkey, "ChannelReady", 120)

        # 2. 用合法金额打开第二条通道，但不手动接受。
        opening = client.open_channel(
            {
                "pubkey": peer_pubkey,
                "funding_amount": hex(200 * 100000000),
                "public": True,
            }
        )
        opening_id = opening["temporary_channel_id"]

        # 3. 等待 opening 通道可见，然后按通道 ID 核对完整 pending 集合。
        for _ in range(15):
            pending = client.list_channels({"only_pending": True})["channels"]
            pending_ids = {channel["channel_id"] for channel in pending}
            if opening_id in pending_ids:
                break
            time.sleep(1)
        else:
            assert False, f"Opening channel {opening_id} did not appear as pending"

        assert pending_ids == {opening_id}, pending
        assert pending[0]["state"]["state_name"] == "NegotiatingFunding"
        assert ready_id not in pending_ids

        # 4. 默认列表仍应包含同一条已就绪通道。
        listed = client.list_channels({})["channels"]
        ready = [channel for channel in listed if channel["channel_id"] == ready_id]
        assert len(ready) == 1, listed
        assert ready[0]["state"]["state_name"] == "ChannelReady"
