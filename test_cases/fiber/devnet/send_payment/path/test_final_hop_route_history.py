"""PR #1752: final-hop errors must not penalize a healthy upstream channel."""

import time

from framework.basic_fiber import FiberTest
from framework.util import ckb_hash


class TestFinalHopHistory(FiberTest):
    # TEST-MAP: FHP-01
    def test_bad_final_amount_only_penalizes_last_channel(self):
        """
        1. 建立唯一上游 A-B 和两条并行末跳 B-C，确认两条末跳均可路由。
        2. 用金额不足的非 MPP invoice hash 指定 BC1 支付，检查末跳错误且无余额结算。
        3. 用新 hash 自动支付，检查经 A-B、BC2 成功，BC1 余额不变。
        """
        amount = 100000000

        # 1. 建立并检查三条公共通道及两条可用的指定路由。
        fiber3 = self.start_new_fiber(self.generate_account(10000))
        self.open_channel(self.fiber1, self.fiber2, 300 * amount, 0, 0, 0)
        self.open_channel(self.fiber2, fiber3, 300 * amount, 0, 0, 0)
        self.open_channel(self.fiber2, fiber3, 300 * amount, 0, 0, 0)

        deadline = time.time() + 120
        while time.time() < deadline:
            ab = self.fiber1.get_client().list_channels(
                {"pubkey": self.fiber2.get_pubkey()}
            )["channels"]
            bc = self.fiber2.get_client().list_channels(
                {"pubkey": fiber3.get_pubkey()}
            )["channels"]
            if (
                len(ab) == 1
                and len(bc) == 2
                and all(
                    channel["state"]["state_name"] == "ChannelReady"
                    for channel in ab + bc
                )
            ):
                break
            time.sleep(1)
        else:
            assert (
                False
            ), f"expected one ready A-B and two ready B-C channels: {ab}, {bc}"

        self.wait_graph_channels_sync(self.fiber1, 3)
        ab_outpoint = ab[0]["channel_outpoint"]
        bc_outpoints = [channel["channel_outpoint"] for channel in bc]
        assert len(set(bc_outpoints)) == 2
        for bc_outpoint in bc_outpoints:
            route = self.fiber1.get_client().build_router(
                {
                    "amount": hex(amount),
                    "hops_info": [
                        {
                            "pubkey": self.fiber2.get_pubkey(),
                            "channel_outpoint": ab_outpoint,
                        },
                        {
                            "pubkey": fiber3.get_pubkey(),
                            "channel_outpoint": bc_outpoint,
                        },
                    ],
                }
            )["router_hops"]
            assert [hop["channel_outpoint"] for hop in route] == [
                ab_outpoint,
                bc_outpoint,
            ]

        before_ab = int(ab[0]["local_balance"], 16)
        before_bc = {
            channel["channel_outpoint"]: int(channel["local_balance"], 16)
            for channel in bc
        }

        # 2. 固定 BC1 制造收款金额错误，等失败 TLC 清理后检查无结算。
        payment_hash = ckb_hash(self.generate_random_preimage())
        fiber3.get_client().new_invoice(
            {
                "amount": hex(2 * amount),
                "currency": "Fibd",
                "description": "PR-1752 final amount attribution",
                "payment_hash": payment_hash,
                "hash_algorithm": "ckb_hash",
                "allow_mpp": False,
            }
        )
        bad_route = self.fiber1.get_client().build_router(
            {
                "amount": hex(amount),
                "hops_info": [
                    {
                        "pubkey": self.fiber2.get_pubkey(),
                        "channel_outpoint": ab_outpoint,
                    },
                    {
                        "pubkey": fiber3.get_pubkey(),
                        "channel_outpoint": bc_outpoints[0],
                    },
                ],
            }
        )["router_hops"]
        bad_payment = self.fiber1.get_client().send_payment_with_router(
            {
                "payment_hash": payment_hash,
                "invoice": None,
                "keysend": False,
                "router": bad_route,
            }
        )
        assert bad_payment["payment_hash"] == payment_hash
        self.wait_payment_state(self.fiber1, payment_hash, "Failed", timeout=120)
        failed = self.fiber1.get_client().get_payment({"payment_hash": payment_hash})
        assert "FinalIncorrectTlcAmount" in (failed.get("failed_error") or ""), failed
        self.wait_invoice_state(fiber3, payment_hash, "Open")

        deadline = time.time() + 60
        while time.time() < deadline:
            ab = self.fiber1.get_client().list_channels(
                {"pubkey": self.fiber2.get_pubkey()}
            )["channels"]
            bc = self.fiber2.get_client().list_channels(
                {"pubkey": fiber3.get_pubkey()}
            )["channels"]
            if all(
                int(channel["offered_tlc_balance"], 16) == 0
                and int(channel["received_tlc_balance"], 16) == 0
                and all(
                    "RemoveAckConfirmed" in tlc["status"].values()
                    for tlc in channel["pending_tlcs"]
                )
                for channel in ab + bc
            ):
                break
            time.sleep(1)
        else:
            assert False, f"failed payment left unsettled TLCs: {ab}, {bc}"
        assert int(ab[0]["local_balance"], 16) == before_ab
        assert {
            channel["channel_outpoint"]: int(channel["local_balance"], 16)
            for channel in bc
        } == before_bc

        # 3. 新的自动路由支付必须保留 A-B，并改走 BC2。
        good_payment = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": fiber3.get_pubkey(),
                "amount": hex(amount),
                "keysend": True,
                "max_fee_rate": hex(1000000000000000),
            }
        )
        assert good_payment["payment_hash"] != payment_hash
        self.wait_payment_state(
            self.fiber1, good_payment["payment_hash"], "Success", timeout=120
        )

        deadline = time.time() + 60
        while time.time() < deadline:
            ab = self.fiber1.get_client().list_channels(
                {"pubkey": self.fiber2.get_pubkey()}
            )["channels"]
            bc = self.fiber2.get_client().list_channels(
                {"pubkey": fiber3.get_pubkey()}
            )["channels"]
            after_bc = {
                channel["channel_outpoint"]: int(channel["local_balance"], 16)
                for channel in bc
            }
            if (
                int(ab[0]["local_balance"], 16) == before_ab - amount
                and after_bc[bc_outpoints[0]] == before_bc[bc_outpoints[0]]
                and after_bc[bc_outpoints[1]] == before_bc[bc_outpoints[1]] - amount
            ):
                break
            time.sleep(1)
        else:
            assert False, f"successful payment did not settle on BC2: {ab}, {bc}"

        after_bc = {
            channel["channel_outpoint"]: int(channel["local_balance"], 16)
            for channel in bc
        }
        assert int(ab[0]["local_balance"], 16) == before_ab - amount
        assert after_bc[bc_outpoints[0]] == before_bc[bc_outpoints[0]]
        assert after_bc[bc_outpoints[1]] == before_bc[bc_outpoints[1]] - amount

    # TEST-MAP: FHP-02
    def test_bad_final_expiry_only_penalizes_last_channel(self):
        """
        1. 建立唯一上游 A-B 和两条并行末跳 B-C，确认两条末跳均可路由。
        2. 用要求 14 天最终到期的非 MPP invoice hash 指定 BC1 支付，检查末跳错误且无结算。
        3. 用新 hash 自动支付，检查经 A-B、BC2 成功，BC1 余额不变。
        """
        amount = 100000000

        # 1. 建立并检查三条公共通道及两条可用的指定路由。
        fiber3 = self.start_new_fiber(self.generate_account(10000))
        self.open_channel(self.fiber1, self.fiber2, 300 * amount, 0, 0, 0)
        self.open_channel(self.fiber2, fiber3, 300 * amount, 0, 0, 0)
        self.open_channel(self.fiber2, fiber3, 300 * amount, 0, 0, 0)

        deadline = time.time() + 120
        while time.time() < deadline:
            ab = self.fiber1.get_client().list_channels(
                {"pubkey": self.fiber2.get_pubkey()}
            )["channels"]
            bc = self.fiber2.get_client().list_channels(
                {"pubkey": fiber3.get_pubkey()}
            )["channels"]
            if (
                len(ab) == 1
                and len(bc) == 2
                and all(
                    channel["state"]["state_name"] == "ChannelReady"
                    for channel in ab + bc
                )
            ):
                break
            time.sleep(1)
        else:
            assert (
                False
            ), f"expected one ready A-B and two ready B-C channels: {ab}, {bc}"

        self.wait_graph_channels_sync(self.fiber1, 3)
        ab_outpoint = ab[0]["channel_outpoint"]
        bc_outpoints = [channel["channel_outpoint"] for channel in bc]
        assert len(set(bc_outpoints)) == 2
        routes = {}
        for bc_outpoint in bc_outpoints:
            route = self.fiber1.get_client().build_router(
                {
                    "amount": hex(amount),
                    "hops_info": [
                        {
                            "pubkey": self.fiber2.get_pubkey(),
                            "channel_outpoint": ab_outpoint,
                        },
                        {
                            "pubkey": fiber3.get_pubkey(),
                            "channel_outpoint": bc_outpoint,
                        },
                    ],
                }
            )["router_hops"]
            assert [hop["channel_outpoint"] for hop in route] == [
                ab_outpoint,
                bc_outpoint,
            ]
            routes[bc_outpoint] = route

        before_ab = int(ab[0]["local_balance"], 16)
        before_bc = {
            channel["channel_outpoint"]: int(channel["local_balance"], 16)
            for channel in bc
        }

        # 2. 固定 BC1 用较短的默认最终到期支付，等失败 TLC 无未结算余额。
        payment_hash = ckb_hash(self.generate_random_preimage())
        fiber3.get_client().new_invoice(
            {
                "amount": hex(amount),
                "currency": "Fibd",
                "description": "PR-1752 final expiry attribution",
                "payment_hash": payment_hash,
                "hash_algorithm": "ckb_hash",
                "final_expiry_delta": hex(14 * 24 * 60 * 60 * 1000),
                "allow_mpp": False,
            }
        )
        bad_payment = self.fiber1.get_client().send_payment_with_router(
            {
                "payment_hash": payment_hash,
                "invoice": None,
                "keysend": False,
                "router": routes[bc_outpoints[0]],
            }
        )
        assert bad_payment["payment_hash"] == payment_hash
        self.wait_payment_state(self.fiber1, payment_hash, "Failed", timeout=120)
        failed = self.fiber1.get_client().get_payment({"payment_hash": payment_hash})
        assert "FinalIncorrectExpiryDelta" in (failed.get("failed_error") or ""), failed
        self.wait_invoice_state(fiber3, payment_hash, "Open")

        deadline = time.time() + 60
        while time.time() < deadline:
            ab = self.fiber1.get_client().list_channels(
                {"pubkey": self.fiber2.get_pubkey()}
            )["channels"]
            bc = self.fiber2.get_client().list_channels(
                {"pubkey": fiber3.get_pubkey()}
            )["channels"]
            if all(
                int(channel["offered_tlc_balance"], 16) == 0
                and int(channel["received_tlc_balance"], 16) == 0
                and all(
                    "RemoveAckConfirmed" in tlc["status"].values()
                    for tlc in channel["pending_tlcs"]
                )
                for channel in ab + bc
            ):
                break
            time.sleep(1)
        else:
            assert False, f"failed payment left unsettled TLCs: {ab}, {bc}"
        assert int(ab[0]["local_balance"], 16) == before_ab
        assert {
            channel["channel_outpoint"]: int(channel["local_balance"], 16)
            for channel in bc
        } == before_bc

        # 3. 新的自动路由支付必须保留 A-B，并改走 BC2。
        good_payment = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": fiber3.get_pubkey(),
                "amount": hex(amount),
                "keysend": True,
                "max_fee_rate": hex(1000000000000000),
            }
        )
        assert good_payment["payment_hash"] != payment_hash
        self.wait_payment_state(
            self.fiber1, good_payment["payment_hash"], "Success", timeout=120
        )

        deadline = time.time() + 60
        while time.time() < deadline:
            ab = self.fiber1.get_client().list_channels(
                {"pubkey": self.fiber2.get_pubkey()}
            )["channels"]
            bc = self.fiber2.get_client().list_channels(
                {"pubkey": fiber3.get_pubkey()}
            )["channels"]
            after_bc = {
                channel["channel_outpoint"]: int(channel["local_balance"], 16)
                for channel in bc
            }
            if (
                int(ab[0]["local_balance"], 16) == before_ab - amount
                and after_bc[bc_outpoints[0]] == before_bc[bc_outpoints[0]]
                and after_bc[bc_outpoints[1]] == before_bc[bc_outpoints[1]] - amount
            ):
                break
            time.sleep(1)
        else:
            assert False, f"successful payment did not settle on BC2: {ab}, {bc}"

        assert int(ab[0]["local_balance"], 16) == before_ab - amount
        assert after_bc[bc_outpoints[0]] == before_bc[bc_outpoints[0]]
        assert after_bc[bc_outpoints[1]] == before_bc[bc_outpoints[1]] - amount

    # TEST-MAP: FHP-03
    def test_direct_final_errors_remain_terminal(self):
        """
        1. 建立两个互不共享付款历史的直连通道，并记录结算余额。
        2. 第一付款方低于 invoice 金额支付，检查金额错误、终态 Failed 和 invoice Open。
        3. 第二付款方用 48 小时到期上限支付 14 天 invoice，检查到期错误和终态 Failed。
        4. 检查两条直连通道仍就绪、无未结算 TLC 且余额不变。
        """
        amount = 100000000

        # 1. 两个付款方分别与同一收款方建立直连通道。
        fiber3 = self.start_new_fiber(self.generate_account(10000))
        self.open_channel(self.fiber1, self.fiber2, 300 * amount, 0, 0, 0)
        self.open_channel(fiber3, self.fiber2, 300 * amount, 0, 0, 0)
        ab = self.fiber1.get_client().list_channels(
            {"pubkey": self.fiber2.get_pubkey()}
        )["channels"]
        cb = fiber3.get_client().list_channels({"pubkey": self.fiber2.get_pubkey()})[
            "channels"
        ]
        assert len(ab) == len(cb) == 1
        assert ab[0]["state"]["state_name"] == "ChannelReady"
        assert cb[0]["state"]["state_name"] == "ChannelReady"
        before_ab = int(ab[0]["local_balance"], 16)
        before_cb = int(cb[0]["local_balance"], 16)

        # 2. 直连金额不足必须由收款节点拒绝并终止支付。
        amount_hash = ckb_hash(self.generate_random_preimage())
        self.fiber2.get_client().new_invoice(
            {
                "amount": hex(2 * amount),
                "currency": "Fibd",
                "description": "PR-1752 direct final amount",
                "payment_hash": amount_hash,
                "hash_algorithm": "ckb_hash",
                "allow_mpp": False,
            }
        )
        amount_payment = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": self.fiber2.get_pubkey(),
                "amount": hex(amount),
                "payment_hash": amount_hash,
                "keysend": False,
            }
        )
        assert amount_payment["payment_hash"] == amount_hash
        self.wait_payment_state(self.fiber1, amount_hash, "Failed", timeout=120)
        amount_failed = self.fiber1.get_client().get_payment(
            {"payment_hash": amount_hash}
        )
        assert "FinalIncorrectTlcAmount" in (
            amount_failed.get("failed_error") or ""
        ), amount_failed
        self.wait_invoice_state(self.fiber2, amount_hash, "Open")

        # 3. 另一付款方直连支付时，最终到期不足也必须失败且停止。
        expiry_hash = ckb_hash(self.generate_random_preimage())
        self.fiber2.get_client().new_invoice(
            {
                "amount": hex(amount),
                "currency": "Fibd",
                "description": "PR-1752 direct final expiry",
                "payment_hash": expiry_hash,
                "hash_algorithm": "ckb_hash",
                "final_expiry_delta": hex(14 * 24 * 60 * 60 * 1000),
                "allow_mpp": False,
            }
        )
        expiry_payment = fiber3.get_client().send_payment(
            {
                "target_pubkey": self.fiber2.get_pubkey(),
                "amount": hex(amount),
                "payment_hash": expiry_hash,
                "keysend": False,
                "tlc_expiry_limit": hex(48 * 60 * 60 * 1000),
            }
        )
        assert expiry_payment["payment_hash"] == expiry_hash
        self.wait_payment_state(fiber3, expiry_hash, "Failed", timeout=120)
        expiry_failed = fiber3.get_client().get_payment({"payment_hash": expiry_hash})
        assert "FinalIncorrectExpiryDelta" in (
            expiry_failed.get("failed_error") or ""
        ), expiry_failed
        self.wait_invoice_state(self.fiber2, expiry_hash, "Open")

        # 4. 两条直连通道仍就绪，失败未转移余额或留有未结算 TLC。
        deadline = time.time() + 60
        while time.time() < deadline:
            ab = self.fiber1.get_client().list_channels(
                {"pubkey": self.fiber2.get_pubkey()}
            )["channels"]
            cb = fiber3.get_client().list_channels(
                {"pubkey": self.fiber2.get_pubkey()}
            )["channels"]
            if all(
                int(channel["offered_tlc_balance"], 16) == 0
                and int(channel["received_tlc_balance"], 16) == 0
                and all(
                    "RemoveAckConfirmed" in tlc["status"].values()
                    for tlc in channel["pending_tlcs"]
                )
                for channel in ab + cb
            ):
                break
            time.sleep(1)
        else:
            assert False, f"direct failures left unsettled TLCs: {ab}, {cb}"
        assert ab[0]["state"]["state_name"] == "ChannelReady"
        assert cb[0]["state"]["state_name"] == "ChannelReady"
        assert int(ab[0]["local_balance"], 16) == before_ab
        assert int(cb[0]["local_balance"], 16) == before_cb
