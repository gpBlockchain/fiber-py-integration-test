"""PR #1672: forwarding must not settle the router's same-hash local invoice."""

import time

from framework.basic_share_fiber import SharedFiberTest
from framework.util import ckb_hash

CKB = 100000000
AMOUNT = CKB


class TestForwardedLocalInvoice(SharedFiberTest):
    ckb_rpc_port = 8414
    ckb_p2p_port = 8425
    fiber1_rpc_port = 8528
    fiber1_p2p_port = 8527
    fiber2_rpc_port = 8529
    fiber2_p2p_port = 8530
    extra_fiber_rpc_port = 8551
    extra_fiber_p2p_port = 8602

    def setUp(self):
        if getattr(type(self), "_channels_ready", False):
            return

        # A = fiber1, B = fiber2, C = fiber3. Reuse this isolated path.
        self.__class__.fiber3 = self.start_new_fiber(self.generate_account(10000))
        self.__class__.channel_ab = self.open_channel(
            self.fiber1, self.fiber2, 1000 * CKB, 0
        )
        self.__class__.channel_bc = self.open_channel(
            self.fiber2, self.fiber3, 1000 * CKB, 0
        )
        self.wait_graph_channels_sync(self.fiber1, 2, timeout=120)
        self.__class__._channels_ready = True

    def _channel(self, fiber, channel_id):
        channels = fiber.get_client().list_channels({"include_closed": True})[
            "channels"
        ]
        for channel in channels:
            if channel["channel_id"] == channel_id:
                return channel
        raise AssertionError(f"channel {channel_id} not found")

    def _wait_tlc(self, fiber, channel_id, payment_hash, status, timeout=120):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for tlc in self._channel(fiber, channel_id).get("pending_tlcs") or []:
                if tlc["payment_hash"] == payment_hash and tlc["status"] == status:
                    return tlc
            time.sleep(0.5)
        raise AssertionError(
            f"{channel_id}: {payment_hash} did not reach {status}; "
            f"pending={self._channel(fiber, channel_id).get('pending_tlcs')}"
        )

    def _wait_tlc_gone(self, fiber, channel_id, payment_hash, tlc_id, timeout=120):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            pending = self._channel(fiber, channel_id).get("pending_tlcs") or []
            if not any(
                tlc["payment_hash"] == payment_hash and tlc["id"] == tlc_id
                for tlc in pending
            ):
                return
            time.sleep(0.5)
        raise AssertionError(f"{channel_id}: TLC {tlc_id} still pending: {pending}")

    def _forward_route(self):
        pubkeys = [
            fiber.get_pubkey() for fiber in (self.fiber1, self.fiber2, self.fiber3)
        ]
        graph = self.fiber1.get_client().graph_channels({})["channels"]
        hops = []
        for left, right in ((0, 1), (1, 2)):
            match = next(
                channel
                for channel in graph
                if {channel["node1"], channel["node2"]}
                == {pubkeys[left], pubkeys[right]}
            )
            hops.append(
                {
                    "pubkey": pubkeys[right],
                    "channel_outpoint": match["channel_outpoint"],
                }
            )
        return self.fiber1.get_client().build_router(
            {"amount": hex(AMOUNT), "hops_info": hops}
        )["router_hops"]

    def _start_forwarded_hold(self):
        # A=付款方，B=中间转发节点，C=最终收款方；共享通道，但每次使用新的 hash 隔离付款。
        preimage = self.generate_random_preimage()
        payment_hash = ckb_hash(preimage)
        # B 保存同哈希发票及原像：即使 B 知道原像，也应等待 C 的结果，不得自行兑现转发。
        local_invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(AMOUNT),
                "currency": "Fibd",
                "description": "PR1672 router local invoice",
                "payment_preimage": preimage,
                "hash_algorithm": "ckb_hash",
            }
        )
        # C 只拿到 payment_hash，不拿原像；发票先持单，后续由用例显式结算或取消。
        hold_invoice = self.fiber3.get_client().new_invoice(
            {
                "amount": hex(AMOUNT),
                "currency": "Fibd",
                "description": "PR1672 recipient hold invoice",
                "payment_hash": payment_hash,
                "hash_algorithm": "ckb_hash",
            }
        )
        assert local_invoice["invoice"]["data"]["payment_hash"] == payment_hash
        assert hold_invoice["invoice"]["data"]["payment_hash"] == payment_hash

        # 在发送前采样余额；后续只核对本次付款带来的增减，不依赖前一用例的余额。
        before = {
            "b_up": int(
                self._channel(self.fiber2, self.channel_ab)["local_balance"], 16
            ),
            "b_down": int(
                self._channel(self.fiber2, self.channel_bc)["local_balance"], 16
            ),
            "c": int(self._channel(self.fiber3, self.channel_bc)["local_balance"], 16),
        }
        # 固定走 A→B→C；已核对实现会抑制显式路由付款失败后的自动重试。
        payment = self.fiber1.get_client().send_payment_with_router(
            {
                "router": self._forward_route(),
                "invoice": hold_invoice["invoice_address"],
            }
        )
        assert payment["payment_hash"] == payment_hash
        # 先确认 C 持单、B 两侧 TLC 都已承诺；返回的 TLC 包含后续追踪所需的 ID 和金额。
        self.wait_invoice_state(self.fiber3, payment_hash, "Received", timeout=120)
        incoming = self._wait_tlc(
            self.fiber2, self.channel_ab, payment_hash, {"Inbound": "Committed"}
        )
        outgoing = self._wait_tlc(
            self.fiber2, self.channel_bc, payment_hash, {"Outbound": "Committed"}
        )
        # 此时 B 自己的发票仍应 Open，转发不能被误当成本地收款。
        # 注意：这些前置检查不证明 B 已完成一轮维护，故未覆盖 PR1672-01。
        assert (
            self.fiber2.get_client().get_invoice({"payment_hash": payment_hash})[
                "status"
            ]
            == "Open"
        )
        return preimage, payment_hash, incoming, outgoing, before

    def test_forwarded_fulfill_leaves_router_invoice_open(self):
        # TEST-MAP: PR1672-02
        # 场景：C 兑现持单后，付款成功，但中间节点 B 的同哈希发票不应被标为 Paid。
        preimage, payment_hash, incoming, outgoing, before = (
            self._start_forwarded_hold()
        )

        # 只有最终收款方 C 提交正确原像，才触发本次转发的成功回传。
        self.fiber3.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )
        self.wait_payment_state(self.fiber1, payment_hash, "Success", timeout=120)
        self.wait_invoice_state(self.fiber3, payment_hash, "Paid", timeout=120)
        # 用 hash + 原 TLC ID 确认两跳都已收尾，避免把其他付款的完成当成本次成功。
        self._wait_tlc_gone(self.fiber2, self.channel_ab, payment_hash, incoming["id"])
        self._wait_tlc_gone(self.fiber2, self.channel_bc, payment_hash, outgoing["id"])

        # 付款方获得正确原像、C 已 Paid，而 B 本地发票仍须保持 Open。
        paid = self.fiber1.get_client().get_payment({"payment_hash": payment_hash})
        assert paid["payment_preimage"] == preimage
        assert (
            self.fiber2.get_client().get_invoice({"payment_hash": payment_hash})[
                "status"
            ]
            == "Open"
        )
        b_up = self._channel(self.fiber2, self.channel_ab)
        b_down = self._channel(self.fiber2, self.channel_bc)
        c_down = self._channel(self.fiber3, self.channel_bc)
        # B 上游入账按入站 TLC 金额核对；入站与出站金额之差包含路由费。
        assert int(b_up["local_balance"], 16) - before["b_up"] == int(
            incoming["amount"], 16
        )
        # B 下游扣款与 C 到账都应等于出站 TLC 金额，防止只改状态却转错资金。
        assert before["b_down"] - int(b_down["local_balance"], 16) == int(
            outgoing["amount"], 16
        )
        assert int(c_down["local_balance"], 16) - before["c"] == int(
            outgoing["amount"], 16
        )
        # 两跳通道仍可用；状态、原像、TLC 消失及精确余额共同证明 Fulfill 路径。
        assert all(
            channel["state"]["state_name"] == "ChannelReady"
            for channel in (b_up, b_down)
        )

    def test_cancelled_downstream_hold_fails_only_its_forward(self):
        # TEST-MAP: PR1672-03
        # 场景：C 取消持单后应向 A 返回失败，B 不能用本地原像把失败改成成功。
        _, payment_hash, incoming, outgoing, before = self._start_forwarded_hold()

        # 等前置 TLC 已承诺后再取消，验证的是本次已建立转发的失败回传。
        self.fiber3.get_client().cancel_invoice({"payment_hash": payment_hash})
        self.wait_invoice_state(self.fiber3, payment_hash, "Cancelled", timeout=120)
        self.wait_payment_state(self.fiber1, payment_hash, "Failed", timeout=120)
        # 失败 TLC 可作为确认移除记录保留在 pending_tlcs；等待 RemoveAckConfirmed，而非列表清空。
        removed_incoming = self._wait_tlc(
            self.fiber2,
            self.channel_ab,
            payment_hash,
            {"Inbound": "RemoveAckConfirmed"},
        )
        removed_outgoing = self._wait_tlc(
            self.fiber2,
            self.channel_bc,
            payment_hash,
            {"Outbound": "RemoveAckConfirmed"},
        )
        # 必须是取消前记录的两跳 TLC，不能拿另一轮尝试或其他付款的终态替代。
        assert removed_incoming["id"] == incoming["id"]
        assert removed_outgoing["id"] == outgoing["id"]

        # 失败原因应是取消而非超时；付款方未得到原像，B 自己的发票仍为 Open。
        payment = self.fiber1.get_client().get_payment({"payment_hash": payment_hash})
        assert payment.get("payment_preimage") is None, payment
        assert payment["failed_error"] == "InvoiceCancelled", payment
        assert (
            self.fiber2.get_client().get_invoice({"payment_hash": payment_hash})[
                "status"
            ]
            == "Open"
        )
        b_up = self._channel(self.fiber2, self.channel_ab)
        b_down = self._channel(self.fiber2, self.channel_bc)
        c_down = self._channel(self.fiber3, self.channel_bc)
        # 每侧同 hash 只保留本次原 ID，避免额外的同哈希尝试混入结果。
        assert [
            t["id"] for t in b_up["pending_tlcs"] if t["payment_hash"] == payment_hash
        ] == [incoming["id"]]
        assert [
            t["id"] for t in b_down["pending_tlcs"] if t["payment_hash"] == payment_hash
        ] == [outgoing["id"]]
        # 取消不应发生结算转账：B 两侧及 C 的余额均恢复到发送前。
        assert int(b_up["local_balance"], 16) == before["b_up"]
        assert int(b_down["local_balance"], 16) == before["b_down"]
        assert int(c_down["local_balance"], 16) == before["c"]
        # 失败只终止本次付款，两跳通道仍 Ready；结合错误和原 TLC 终态证明 Fail 路径。
        assert all(
            channel["state"]["state_name"] == "ChannelReady"
            for channel in (b_up, b_down)
        )

    def test_direct_local_invoice_still_settles(self):
        # TEST-MAP: PR1672-04
        # 兼容性对照：B 本身就是收款方，使用新的带原像发票，不涉及 B→C 转发。
        preimage = self.generate_random_preimage()
        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(AMOUNT),
                "currency": "Fibd",
                "description": "PR1672 direct local invoice",
                "payment_preimage": preimage,
                "hash_algorithm": "ckb_hash",
            }
        )
        payment_hash = invoice["invoice"]["data"]["payment_hash"]

        # A 按 B 的发票直接付款，转发隔离守卫不应拦截真正的本地收款。
        payment = self.fiber1.get_client().send_payment(
            {"invoice": invoice["invoice_address"]}
        )
        # 核对同一笔付款成功、B 发票 Paid，且付款方拿到正确原像。
        assert payment["payment_hash"] == payment_hash
        self.wait_payment_state(self.fiber1, payment_hash, "Success", timeout=120)
        self.wait_invoice_state(self.fiber2, payment_hash, "Paid", timeout=120)
        assert (
            self.fiber1.get_client().get_payment({"payment_hash": payment_hash})[
                "payment_preimage"
            ]
            == preimage
        )
        # 收款后 A-B 通道仍可用；本例不证明本地收款与同哈希转发并发时的隔离。
        assert (
            self._channel(self.fiber2, self.channel_ab)["state"]["state_name"]
            == "ChannelReady"
        )
