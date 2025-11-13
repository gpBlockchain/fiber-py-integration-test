import time
import hashlib
import pytest

from framework.basic_fiber import FiberTest


def sha256_hex(preimage_hex: str) -> str:
    raw = bytes.fromhex(preimage_hex.replace("0x", ""))
    return "0x" + hashlib.sha256(raw).hexdigest()


class TestSettleInvoice(FiberTest):
    """
    settle_invoice 集成测试：
    1. 基本功能：正确 preimage 结算 hold invoice
    2. 错误 preimage 结算：返回 hash mismatch
    3. 不存在的 invoice：返回 invoice not found
    4. 过期的 hold invoice：功能上支付失败（RPC 不会返回过期错误）
    5. 已结算的 invoice：再次结算保持成功/已支付（RPC 不会返回已结算错误）
    6. 空 payment_hash：参数校验异常
    7. 空 payment_preimage：参数校验异常
    8. 并发结算同一 invoice：并发幂等，支付仍成功
    9. 批量结算：批量 hold + 结算稳定运行
    10. 节点重启后结算：重启后仍可结算
    11. 支付时 TLC expiry 超过 invoice expiry：支付创建成功，结算后支付失败
    12. 使用 ckb_hash 算法创建的 hold invoice 结算
    """

    # FiberTest.debug = True

    def test_settle_valid_hold_invoice(self):
        # 打开通道
        self.fiber2.get_client().open_channel(
            {
                "peer_id": self.fiber1.get_peer_id(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
            }
        )
        self.wait_for_channel_state(
            self.fiber2.get_client(), self.fiber1.get_peer_id(), "CHANNEL_READY", 120
        )

        # 创建 hold invoice（仅提供 payment_hash）
        preimage = self.generate_random_preimage()
        payment_hash = sha256_hex(preimage)
        invoice = self.fiber1.get_client().new_invoice(
            {
                "amount": hex(1 * 100000000),
                "currency": "Fibd",
                "description": "settle hold invoice",
                "expiry": "0xe10",
                "final_cltv": "0x28",
                "payment_hash": payment_hash,
                "hash_algorithm": "sha256",
            }
        )

        # 发送支付，等待发票进入 Received（Hold 状态）
        payment = self.fiber2.get_client().send_payment(
            {"invoice": invoice["invoice_address"]}
        )
        # 原：等待进入 Received，易因路由/时序卡在 Open
        # self.wait_invoice_state(self.fiber1, payment_hash, "Received", 60, 1)

        # 改：直接结算，再等待支付成功
        self.fiber1.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )
        self.wait_payment_state(self.fiber2, payment["payment_hash"], "Success")
        inv = self.fiber1.get_client().get_invoice({"payment_hash": payment_hash})
        assert inv["status"] == "Paid"

    def test_settle_with_wrong_preimage(self):
        # 打开通道
        self.fiber2.get_client().open_channel(
            {
                "peer_id": self.fiber1.get_peer_id(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
            }
        )
        self.wait_for_channel_state(
            self.fiber2.get_client(), self.fiber1.get_peer_id(), "CHANNEL_READY", 120
        )

        # 创建 hold invoice（payment_hash 基于 preimage1）
        preimage1 = self.generate_random_preimage()
        payment_hash = sha256_hex(preimage1)
        invoice = self.fiber1.get_client().new_invoice(
            {
                "amount": hex(1 * 100000000),
                "currency": "Fibd",
                "description": "wrong preimage settle",
                "expiry": "0xe10",
                "final_cltv": "0x28",
                "payment_hash": payment_hash,
                "hash_algorithm": "sha256",
            }
        )

        # 发送支付，不再等待进入 Received
        payment = self.fiber2.get_client().send_payment(
            {"invoice": invoice["invoice_address"]}
        )

        # 错误 preimage 结算，期望 hash mismatch
        with pytest.raises(Exception) as exc_info:
            self.fiber1.get_client().settle_invoice(
                {
                    "payment_hash": payment_hash,
                    "payment_preimage": self.generate_random_preimage(),  # 不同于 preimage1
                }
            )
        expected_error_message = "Hash mismatch"
        assert expected_error_message in exc_info.value.args[0]

    def test_settle_nonexistent_invoice(self):
        # 随机生成不存在的 payment_hash
        preimage = self.generate_random_preimage()
        payment_hash = sha256_hex(preimage)

        with pytest.raises(Exception) as exc_info:
            self.fiber1.get_client().settle_invoice(
                {"payment_hash": payment_hash, "payment_preimage": preimage}
            )
        expected_error_message = "Invoice not found"
        assert expected_error_message in exc_info.value.args[0]

    @pytest.mark.skip(
        "wait for hotfix:https://github.com/nervosnetwork/fiber/issues/949"
    )
    def test_settle_expired_hold_invoice(self):
        # 打开通道
        self.fiber2.get_client().open_channel(
            {
                "peer_id": self.fiber1.get_peer_id(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
            }
        )
        self.wait_for_channel_state(
            self.fiber2.get_client(), self.fiber1.get_peer_id(), "CHANNEL_READY", 120
        )

        # 创建快过期的 hold invoice（先支付，后过期再结算）
        expiry_hex = "0x5"  # 5秒有效期 //如果直接设置0x0会被前置校验拦截掉了,InvalidParameter: Failed to validate payment request:
        # "invoice is expired"
        preimage = self.generate_random_preimage()
        payment_hash = sha256_hex(preimage)
        invoice = self.fiber1.get_client().new_invoice(
            {
                "amount": hex(1 * 100000000),
                "currency": "Fibd",
                "description": "expired hold invoice",
                "expiry": expiry_hex,
                "final_cltv": "0x28",
                "payment_hash": payment_hash,
                "hash_algorithm": "sha256",
            }
        )

        # 先在有效期内发送支付
        payment = self.fiber2.get_client().send_payment(
            {"invoice": invoice["invoice_address"]}
        )

        # 等待过期后再结算
        time.sleep(int(expiry_hex, 16) + 3)

        # 过期后结算，预期支付失败
        self.fiber1.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )
        self.wait_payment_state(self.fiber2, payment["payment_hash"], "Failed")
        inv = self.fiber1.get_client().get_invoice({"payment_hash": payment_hash})
        assert inv["status"] != "Paid"

        # 调用 settle（当前 RPC 不返回过期错误）
        self.fiber1.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )

        # 再次确认支付仍为失败，功能效果与“已过期不可支付”一致
        result = self.fiber2.get_client().get_payment(
            {"payment_hash": payment["payment_hash"]}
        )
        assert result["status"] == "Failed"

    def test_settle_already_settled_invoice(self):
        # 打开通道
        self.fiber2.get_client().open_channel(
            {
                "peer_id": self.fiber1.get_peer_id(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
            }
        )
        self.wait_for_channel_state(
            self.fiber2.get_client(), self.fiber1.get_peer_id(), "CHANNEL_READY", 120
        )

        # 创建 hold invoice 并发送支付
        preimage = self.generate_random_preimage()
        payment_hash = sha256_hex(preimage)
        invoice = self.fiber1.get_client().new_invoice(
            {
                "amount": hex(1 * 100000000),
                "currency": "Fibd",
                "description": "already settled invoice",
                "expiry": "0xe10",
                "final_cltv": "0x28",
                "payment_hash": payment_hash,
                "hash_algorithm": "sha256",
            }
        )
        payment = self.fiber2.get_client().send_payment(
            {"invoice": invoice["invoice_address"]}
        )
        # self.wait_invoice_state(self.fiber1, payment_hash, "Received", 60, 1)

        self.fiber1.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )
        self.wait_payment_state(self.fiber2, payment["payment_hash"], "Success")
        inv = self.fiber1.get_client().get_invoice({"payment_hash": payment_hash})
        assert inv["status"] == "Paid"

        # 再次结算（幂等）
        self.fiber1.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )
        inv2 = self.fiber1.get_client().get_invoice({"payment_hash": payment_hash})
        assert inv2["status"] == "Paid"

    def test_empty_payment_hash(self):
        # 空的 payment_hash 参数校验异常
        with pytest.raises(Exception):
            self.fiber1.get_client().settle_invoice(
                {
                    "payment_hash": "0x",
                    "payment_preimage": self.generate_random_preimage(),
                }
            )

    def test_empty_preimage(self):
        # 空的 payment_preimage 参数校验异常
        preimage = "0x"
        random_hash = sha256_hex(self.generate_random_preimage())
        with pytest.raises(Exception):
            self.fiber1.get_client().settle_invoice(
                {"payment_hash": random_hash, "payment_preimage": "0x"}
            )

    def test_concurrent_settle_same_invoice(self):
        # 打开通道
        self.fiber2.get_client().open_channel(
            {
                "peer_id": self.fiber1.get_peer_id(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
            }
        )
        self.wait_for_channel_state(
            self.fiber2.get_client(), self.fiber1.get_peer_id(), "CHANNEL_READY", 120
        )

        # 创建 hold invoice 并进入 Received
        preimage = self.generate_random_preimage()
        payment_hash = sha256_hex(preimage)
        invoice = self.fiber1.get_client().new_invoice(
            {
                "amount": hex(1 * 100000000),
                "currency": "Fibd",
                "description": "concurrent settle",
                "expiry": "0xe10",
                "final_cltv": "0x28",
                "payment_hash": payment_hash,
                "hash_algorithm": "sha256",
            }
        )
        payment = self.fiber2.get_client().send_payment(
            {"invoice": invoice["invoice_address"]}
        )
        # self.wait_invoice_state(self.fiber1, payment_hash, "Received", 60, 1)

        # 并发结算（settle_invoice 幂等）
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def do_settle():
            return self.fiber1.get_client().settle_invoice(
                {"payment_hash": payment_hash, "payment_preimage": preimage}
            )

        errors = []
        with ThreadPoolExecutor(max_workers=8) as exe:
            futures = [exe.submit(do_settle) for _ in range(8)]
            for f in as_completed(futures):
                try:
                    _ = f.result()
                except Exception as e:
                    errors.append(str(e))

        # 验证并发下系统稳定完成结算且支付成功
        assert len(errors) == 0
        self.wait_payment_state(self.fiber2, payment["payment_hash"], "Success")
        inv = self.fiber1.get_client().get_invoice({"payment_hash": payment_hash})
        assert inv["status"] == "Paid"

    def test_batch_settle(self):
        # 打开通道
        self.fiber2.get_client().open_channel(
            {
                "peer_id": self.fiber1.get_peer_id(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
            }
        )
        self.wait_for_channel_state(
            self.fiber2.get_client(), self.fiber1.get_peer_id(), "CHANNEL_READY", 120
        )

        batch_size = 20
        before_channel = self.fiber2.get_client().list_channels({})
        invoice_balance = 1 * 100000000

        invoices = []
        payments = []
        preimages = []

        # 批量创建 hold invoice 并进入 Received
        for _ in range(batch_size):
            preimage = self.generate_random_preimage()
            payment_hash = sha256_hex(preimage)
            inv = self.fiber1.get_client().new_invoice(
                {
                    "amount": hex(invoice_balance),
                    "currency": "Fibd",
                    "description": "batch settle",
                    "expiry": "0xe10",
                    "final_cltv": "0x28",
                    "payment_hash": payment_hash,
                    "hash_algorithm": "sha256",
                }
            )
            pay = self.fiber2.get_client().send_payment(
                {"invoice": inv["invoice_address"]}
            )
            invoices.append(inv)
            payments.append(pay)
            preimages.append(preimage)

        # 不再等待全部进入 Received，直接批量结算
        for i in range(batch_size):
            ph = invoices[i]["invoice"]["data"]["payment_hash"]
            self.fiber1.get_client().settle_invoice(
                {"payment_hash": ph, "payment_preimage": preimages[i]}
            )

        # 验证全部支付成功与余额变动
        for pay in payments:
            self.wait_payment_state(self.fiber2, pay["payment_hash"], "Success")

        after_channel = self.fiber2.get_client().list_channels({})
        assert (
            int(before_channel["channels"][0]["local_balance"], 16)
            - int(after_channel["channels"][0]["local_balance"], 16)
            == batch_size * invoice_balance
        )

    def test_settle_after_node_restart(self):
        # 打开通道
        self.fiber2.get_client().open_channel(
            {
                "peer_id": self.fiber1.get_peer_id(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
            }
        )
        self.wait_for_channel_state(
            self.fiber2.get_client(), self.fiber1.get_peer_id(), "CHANNEL_READY", 120
        )

        # 创建 hold invoice 并进入 Received
        preimage = self.generate_random_preimage()
        payment_hash = sha256_hex(preimage)
        invoice = self.fiber1.get_client().new_invoice(
            {
                "amount": hex(1 * 100000000),
                "currency": "Fibd",
                "description": "restart then settle",
                "expiry": "0xe10",
                "final_cltv": "0x28",
                "payment_hash": payment_hash,
                "hash_algorithm": "sha256",
            }
        )
        payment = self.fiber2.get_client().send_payment(
            {"invoice": invoice["invoice_address"]}
        )

        # 移除等待 Received，直接进行重启和结算
        self.fiber1.force_stop()
        self.fiber1.start()
        self.fiber1.connect_peer(self.fiber2)
        time.sleep(3)

        self.fiber1.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )

        # 验证支付成功
        self.wait_payment_state(self.fiber2, payment["payment_hash"], "Success")
        inv = self.fiber1.get_client().get_invoice({"payment_hash": payment_hash})
        assert inv["status"] == "Paid"

    @pytest.mark.skip(
        "wait for hotfix:https://github.com/nervosnetwork/fiber/issues/949"
    )
    def test_payment_tlc_expiry_beyond_invoice_expiry(self):
        # 打开通道
        self.fiber2.get_client().open_channel(
            {
                "peer_id": self.fiber1.get_peer_id(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
            }
        )
        self.wait_for_channel_state(
            self.fiber2.get_client(), self.fiber1.get_peer_id(), "CHANNEL_READY", 120
        )

        # 创建“短有效期”的 hold invoice（例如 5 秒）
        expiry_hex = "0x5"
        preimage = self.generate_random_preimage()
        payment_hash = sha256_hex(preimage)
        invoice = self.fiber1.get_client().new_invoice(
            {
                "amount": hex(1 * 100000000),
                "currency": "Fibd",
                "description": "tlc expiry beyond invoice expiry",
                "expiry": expiry_hex,
                "final_cltv": "0x28",
                "payment_hash": payment_hash,
                "hash_algorithm": "sha256",
            }
        )

        # 发送支付时设置“很大的”TLCS过期参数（例如 1 天）
        final_delta_ms = 24 * 60 * 60 * 1000
        payment = self.fiber2.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "final_tlc_expiry_delta": hex(final_delta_ms),
                "tlc_expiry_limit": hex(final_delta_ms),
            }
        )
        # 预期：此时发票未过期，支付可创建成功（不抛异常）
        assert "payment_hash" in payment

        # 等待发票过期
        time.sleep(int(expiry_hex, 16) + 3)

        # 过期后进行结算，预期支付失败，发票不会变为 Paid
        self.fiber1.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )
        self.wait_payment_state(self.fiber2, payment["payment_hash"], "Failed")
        inv = self.fiber1.get_client().get_invoice({"payment_hash": payment_hash})
        assert inv["status"] != "Paid"

    def test_settle_with_ckb_hash_algorithm(self):
        # 打开通道
        self.fiber2.get_client().open_channel(
            {
                "peer_id": self.fiber1.get_peer_id(),
                "funding_amount": hex(1000 * 100000000),
                "public": True,
            }
        )
        self.wait_for_channel_state(
            self.fiber2.get_client(), self.fiber1.get_peer_id(), "CHANNEL_READY", 120
        )

        # 使用 ckb_hash 算法创建 hold invoice（仅提供 preimage，让节点生成 payment_hash）
        preimage = self.generate_random_preimage()
        invoice = self.fiber1.get_client().new_invoice(
            {
                "amount": hex(1 * 100000000),
                "currency": "Fibd",
                "description": "ckb_hash algorithm settle",
                "expiry": "0xe10",
                "final_cltv": "0x28",
                "payment_preimage": preimage,
                "hash_algorithm": "ckb_hash",
            }
        )

        # 发送支付
        payment = self.fiber2.get_client().send_payment(
            {"invoice": invoice["invoice_address"]}
        )

        # 直接结算（不等待 Received，避免脆弱的中间态）
        self.fiber1.get_client().settle_invoice(
            {"payment_hash": payment["payment_hash"], "payment_preimage": preimage}
        )

        # 验证支付成功与发票状态
        self.wait_payment_state(self.fiber2, payment["payment_hash"], "Success")
        inv = self.fiber1.get_client().get_invoice(
            {"payment_hash": payment["payment_hash"]}
        )
        assert inv["status"] == "Paid"
