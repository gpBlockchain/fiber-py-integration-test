"""MPP：100 笔混合资产、自付和普通 MPP，故障稳定作用于在途原款。"""

import pytest

from test_cases.fiber.devnet.send_payment.mpp.mpp_fixture import (
    MppFixture,
    CKB,
    AMOUNT,
    RESULT_WAIT,
)
from framework.helper.fiber_ops import (
    get_channel,
    calculate_payment_hash,
    wait_for_result,
    restart_fiber,
)


class TestMppBatchRecovery(MppFixture):
    # TEST-MAP: MPP1705-11
    @pytest.mark.skip(
        reason="慢测：多节点 100 笔混合资产故障回归；待完成慢测验证后启用。"
    )
    def test_hundred_mixed_asset_payments_survive_inflight_faults(self):
        """
        1. 建立十条独立资金 lane，共用一个普通中继 M，覆盖 CKB/UDT、环形自付/普通 MPP。
        2. 每批十笔 hold 付款全部 Received/Committed，第 3/6 批分别断连/重启同一 M。
        3. 使用原 hash/原像结算，逐资产核对每端本金与锁定；正常反向调平后处理下一批。
        4. 100 个唯一付款保持成功，最后健康付款完成，原成功款不重复扣付。
        """
        # 1. 每条 lane 两个出向通道均不足全额；不靠总池余额掩盖未完成付款。
        relay = self.fiber2
        owner = self.fiber1.account_private
        self.faucet(relay.account_private, 100000, owner, 100000 * CKB)
        udt = self.get_account_udt_script(owner)
        lanes = []
        for index in range(10):
            asset = udt if index % 2 else None
            is_self = index % 4 < 2
            sender = self.start_new_fiber(
                self.generate_account(10000, owner, 10000 * CKB)
            )
            forwarder = self.start_new_fiber(
                self.generate_account(10000, owner, 10000 * CKB)
            )
            split = [
                self.open_payment_channel(sender, relay, 1000 * CKB, udt=asset)
                for _ in range(2)
            ]
            edges = split + [
                self.open_payment_channel(relay, forwarder, 3000 * CKB, udt=asset)
            ]
            if is_self:
                edges.append(
                    self.open_payment_channel(forwarder, sender, 3000 * CKB, udt=asset)
                )
            lanes.append(
                dict(
                    sender=sender,
                    forwarder=forwarder,
                    receiver=sender if is_self else forwarder,
                    asset=asset,
                    self_payment=is_self,
                    edges=edges,
                    split=split,
                )
            )
        count = sum(len(lane["edges"]) for lane in lanes)
        for lane in lanes:
            self.wait_graph_channels_sync(lane["sender"], count, timeout=120)
        for f in self.fibers:
            f.get_client().try_count, f.get_client().timeout = 1, 10
        history = []
        peers = [f for lane in lanes for f in (lane["sender"], lane["forwarder"])]

        # 2. 十笔都停在外部可证明的 hold 状态，故障不靠随机 sleep 命中。
        for batch in range(1, 11):
            current = []
            for lane in lanes:
                preimage = self.generate_random_preimage()
                payment_hash = calculate_payment_hash(preimage, "ckb_hash")
                args = dict(
                    amount=hex(AMOUNT),
                    currency="Fibd",
                    payment_hash=payment_hash,
                    hash_algorithm="ckb_hash",
                    allow_mpp=True,
                    expiry=hex(86400),
                    final_expiry_delta=hex(43_200_000),
                )
                if lane["asset"]:
                    args["udt_type_script"] = lane["asset"]
                invoice = lane["receiver"].get_client().new_invoice(args)
                response = (
                    lane["sender"]
                    .get_client()
                    .send_payment(
                        {
                            "invoice": invoice["invoice_address"],
                            "allow_self_payment": lane["self_payment"],
                            "max_parts": hex(2),
                            "max_fee_amount": "0x0",
                            "timeout": hex(1800),
                            "tlc_expiry_limit": hex(172_800_000),
                        }
                    )
                )
                assert response["payment_hash"] == payment_hash
                current.append(dict(lane=lane, hash=payment_hash, preimage=preimage))
            for item in current:
                lane = item["lane"]
                self.payment_hash = item["hash"]
                self.wait_invoice_state(
                    lane["receiver"], self.payment_hash, "Received", timeout=120
                )
                for edge in lane["edges"]:
                    self.settlement.record_committed_tlcs(edge, self.payment_hash)
                assert sum(edge["sent"] for edge in lane["split"]) == AMOUNT
                assert all(len(edge["tlcs"]) == 1 for edge in lane["split"])
                assert lane["edges"][2]["sent"] == AMOUNT
            if batch == 3:
                m_key = relay.get_pubkey()
                for peer in peers:
                    for local, remote_key in (
                        (peer, m_key),
                        (relay, peer.get_pubkey()),
                    ):
                        try:
                            local.get_client().disconnect_peer({"pubkey": remote_key})
                        except Exception as error:
                            # 另一端先断开后，公开 RPC 会返回 is not connected；
                            # 仍已设置该端主动断连，其他错误必须抛出。
                            assert "is not connected" in str(error), error
                wait_for_result(
                    "M 两侧的原对端均断开",
                    lambda: all(
                        m_key
                        not in {
                            sender["pubkey"]
                            for sender in peer.get_client().list_peers()["peers"]
                        }
                        for peer in peers
                    ),
                )
                for peer in peers:
                    relay.connect_peer(peer)
            elif batch == 6:
                restart_fiber(relay, peers=peers)
            if batch in (3, 6):
                for item in current:
                    self.payment_hash = item["hash"]
                    for edge in item["lane"]["edges"]:
                        self.settlement.assert_payment_locked(edge, self.payment_hash)

            # 3. 每批原款结清后核对原余额；公开反向调平为下一批恢复相同容量。
            for item in current:
                item["lane"]["receiver"].get_client().settle_invoice(
                    {"payment_hash": item["hash"], "payment_preimage": item["preimage"]}
                )
            for item in current:
                lane = item["lane"]
                self.payment_hash, self.preimage, self.hash_algorithm = (
                    item["hash"],
                    item["preimage"],
                    "ckb_hash",
                )
                self.assert_payment_success(lane["sender"])
                self.wait_invoice_state(
                    lane["receiver"], self.payment_hash, "Paid", timeout=120
                )
                for edge in lane["edges"]:
                    wait_for_result(
                        "原款本金只交付一次",
                        lambda e=edge: self.settlement.payment_is_settled(
                            e, self.payment_hash
                        ),
                        RESULT_WAIT,
                    )
                if lane["self_payment"]:
                    incident = [
                        e
                        for e in lane["edges"]
                        if lane["sender"] in (e["left"], e["right"])
                    ]
                    before = sum(
                        int(
                            e["before"][0 if e["left"] is lane["sender"] else 1][
                                "local_balance"
                            ],
                            16,
                        )
                        for e in incident
                    )
                    after = sum(
                        int(
                            get_channel(lane["sender"], e["channel_id"])[
                                "local_balance"
                            ],
                            16,
                        )
                        for e in incident
                    )
                    assert after == before, "自付本金按资产守恒"
                for edge in reversed(lane["edges"]):
                    self._restore_edge_liquidity(edge, lane["asset"])
                history.append(item)

        # 4. 100 笔原 hash 全部保持成功，调平后的固定余额也捕捉迟到重放。
        assert len(history) == len({item["hash"] for item in history}) == 100
        for item in history:
            row = (
                item["lane"]["sender"]
                .get_client()
                .get_payment({"payment_hash": item["hash"]})
            )
            assert (
                row["status"] == "Success"
                and row["payment_preimage"] == item["preimage"]
            )
            assert not row.get("failed_error")
        for lane in lanes:
            for edge in lane["edges"]:
                self._assert_original_balances(edge)
        # 第 0 条为 CKB lane；健康探针走指定单通道，避免两条平行通道随机选择。
        self._direct_payment(lanes[0]["edges"][0], CKB, None, reverse=False)

    def _direct_payment(self, edge, amount, asset, reverse):
        sender, receiver = (
            (edge["right"], edge["left"]) if reverse else (edge["left"], edge["right"])
        )
        args = {
            "amount": hex(amount),
            "hops_info": [
                {
                    "pubkey": receiver.get_pubkey(),
                    "channel_outpoint": edge["before"][0]["channel_outpoint"],
                }
            ],
        }
        if asset:
            args["udt_type_script"] = asset
        router = sender.get_client().build_router(args)["router_hops"]
        assert (
            len(router) == 1
            and router[0]["channel_outpoint"] == edge["before"][0]["channel_outpoint"]
        )
        request = {"router": router, "keysend": True}
        if asset:
            request["udt_type_script"] = asset
        response = sender.get_client().send_payment_with_router(request)
        self.wait_payment_state(
            sender, response["payment_hash"], "Success", timeout=120
        )
        wait_for_result(
            "调平或探针 TLC 已释放",
            lambda: all(
                int(get_channel(f, edge["channel_id"])[key], 16) == 0
                for f in (sender, receiver)
                for key in ("offered_tlc_balance", "received_tlc_balance")
            ),
        )
        return response

    def _restore_edge_liquidity(self, edge, asset):
        self._direct_payment(edge, edge["sent"], asset, reverse=True)
        self._assert_original_balances(edge)

    def _assert_original_balances(self, edge):
        rows = self.settlement.assert_channel_open(edge)
        for i, row in enumerate(rows):
            for field in ("local_balance", "remote_balance"):
                assert row[field] == edge["before"][i][field]
            assert (
                int(row["offered_tlc_balance"], 16)
                == int(row["received_tlc_balance"], 16)
                == 0
            )
