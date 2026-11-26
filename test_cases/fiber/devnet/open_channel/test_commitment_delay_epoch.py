import time

import pytest

from framework.basic_fiber import FiberTest
from framework.fiber_rpc import FiberRPCClient

_AUTO_ACCEPT_SKIP_REASON = (
    "[nervosnetwork/fiber#1785 — Channel stays in NegotiatingFunding "
    "after peer auto-accept fails TLC expiry validation]"
    "(https://github.com/nervosnetwork/fiber/issues/1785)"
)


class TestCommitmentDelayEpoch(FiberTest):
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 5}

    def test_epoch(self):
        self.fiber1.get_client().open_channel(
            {
                "pubkey": self.fiber2.get_pubkey(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
                "commitment_delay_epoch": "0x20001000001",
            }
        )
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_pubkey(), "ChannelReady", 120
        )
        self.fiber1.get_client().shutdown_channel(
            {
                "channel_id": self.fiber1.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "force": True,
            }
        )
        tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        self.node.getClient().generate_epochs("0x20001000001", 0)

        tx_hash = self.wait_and_check_tx_pool_fee(1000, False, 1200)
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        first_tx_message = self.get_tx_message(tx_hash)
        tx_hash = self.wait_and_check_tx_pool_fee(1000, False, 1200)
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        second_tx_message = self.get_tx_message(tx_hash)
        print("first tx message:", first_tx_message)
        assert first_tx_message["input_cells"][0]["capacity"] == 109999999543
        assert (
            first_tx_message["input_cells"][0]["capacity"]
            - second_tx_message["input_cells"][0]["capacity"]
            == 100000000000
            or first_tx_message["input_cells"][0]["capacity"]
            - second_tx_message["input_cells"][0]["capacity"]
            == 10000000000
        )

    # TEST-MAP: CDE-01
    @pytest.mark.skip(reason=_AUTO_ACCEPT_SKIP_REASON)
    def test_open_channel_reports_peer_tlc_expiry_rejection(self):
        """
        1. 确认发起方与自动接受方已连接，接收方的 TLC 到期差值仅为 4 小时。
        2. 发起方出资 1000 CKB，申请开通要求至少 16 小时到期差值的公开通道。
        3. 发起方应得到这次请求的失败原因，无论是直接报错还是按临时 ID 查询。
        """
        # 1. 发起方 → 接受方：出资达到自动接受门槛，但接受方的到期差值不足。
        initiator = FiberRPCClient(self.fiber1.get_client().url, try_count=1, timeout=5)
        acceptor = FiberRPCClient(self.fiber2.get_client().url, try_count=1, timeout=5)
        acceptor_info = acceptor.node_info()
        peer_pubkey = acceptor_info["pubkey"]
        funding_amount = 1000 * 100000000
        auto_accept_min = int(
            acceptor_info["open_channel_auto_accept_min_ckb_funding_amount"], 16
        )
        assert peer_pubkey in {
            peer["pubkey"] for peer in initiator.list_peers()["peers"]
        }
        assert 0 < auto_accept_min <= funding_amount
        assert int(acceptor_info["tlc_expiry_delta"], 16) == 14_400_000

        # 2. 开通只请求一次；若 RPC 接受请求，临时 ID 只代表已受理。
        try:
            opening = initiator.open_channel(
                {
                    "pubkey": peer_pubkey,
                    "funding_amount": hex(funding_amount),
                    "public": True,
                    "commitment_delay_epoch": "0x6",
                    "tlc_expiry_delta": hex(57_600_000),
                }
            )
        except Exception as error:
            failure_reason = str(error)
        else:
            channel_id = opening["temporary_channel_id"]
            deadline = time.monotonic() + 120
            record = None
            while (remaining := deadline - time.monotonic()) > 0:
                initiator.timeout = min(5, remaining)
                channels = initiator.list_channels(
                    {"pubkey": peer_pubkey, "only_pending": True}
                )["channels"]
                record = next(
                    (c for c in channels if c["channel_id"] == channel_id), None
                )
                if (
                    record
                    and record["state"]["state_name"] == "Closed"
                    and record["failure_detail"]
                ):
                    assert record["channel_outpoint"] is None
                    failure_reason = record["failure_detail"]
                    break
                time.sleep(min(1, max(0, deadline - time.monotonic())))
            else:
                pytest.fail(
                    f"未收到通道 {channel_id} 的开通失败结果；最后一次查询：{record}"
                )

        # 3. 网络超时、通用错误或没有原因的关闭都不算到期差值被拒。
        assert failure_reason, "发起方未收到开通失败原因"
        reason = failure_reason.lower()
        assert "tlc" in reason and "expir" in reason, failure_reason
