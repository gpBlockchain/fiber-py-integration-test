"""Regression test for list_payments filtering by effective payment status."""

import hashlib

from framework.basic_fiber import FiberTest


class TestListPayments(FiberTest):

    def test_effective_status_filter_pagination_and_settlement(self):
        """
        1. 建立通道，准备按哈希顺序交错的三笔持单与两笔普通付款。
        2. 逐笔付款，确认持单处于 Inflight、普通付款处于 Success。
        3. 检查各状态筛选及无过滤列表与实际付款状态一致。
        4. 用 limit 和 after 遍历 Inflight，检查跨状态分页无遗漏或重复。
        5. 用 Success 的哈希作游标，检查排他分页仍返回其后的 Inflight。
        6. 结算一笔持单，检查它从 Inflight 列表移入 Success 列表。
        """
        # 1. 建立通道；自行计算哈希，使两种状态在存储键顺序中交错。
        self.open_channel(self.fiber1, self.fiber2, 500 * 100000000, 0)
        planned_payments = []
        for _ in range(5):
            preimage = self.generate_random_preimage()
            payment_hash = (
                "0x"
                + hashlib.sha256(bytes.fromhex(preimage.removeprefix("0x"))).hexdigest()
            )
            planned_payments.append((payment_hash, preimage))
        planned_payments.sort()
        assert len({payment_hash for payment_hash, _ in planned_payments}) == 5

        hold_hashes = [planned_payments[i][0] for i in (0, 2, 4)]
        success_hashes = [planned_payments[i][0] for i in (1, 3)]
        expected_statuses = {
            payment_hash: "Inflight" if i % 2 == 0 else "Success"
            for i, (payment_hash, _) in enumerate(planned_payments)
        }

        # 2. 持单只提供哈希，普通发票只提供原像；逐笔等待实际状态。
        for i, (payment_hash, preimage) in enumerate(planned_payments):
            invoice_params = {
                "amount": hex(1 * 100000000),
                "currency": "Fibd",
                "hash_algorithm": "sha256",
                "final_expiry_delta": hex(9_600_000),
                "expiry": hex(3600),
            }
            if i % 2 == 0:
                invoice_params["payment_hash"] = payment_hash
            else:
                invoice_params["payment_preimage"] = preimage
            invoice = self.fiber2.get_client().new_invoice(invoice_params)
            payment = self.fiber1.get_client().send_payment(
                {"invoice": invoice["invoice_address"]}
            )
            assert payment["payment_hash"] == payment_hash
            if i % 2 == 0:
                self.wait_invoice_state(self.fiber2, payment_hash, "Received", 120, 1)
                self.wait_payment_state(self.fiber1, payment_hash, "Inflight", 120)
            else:
                self.wait_payment_state(self.fiber1, payment_hash, "Success", 120)

        # 3. 独立读取 get_payment，再逐一核对筛选集合、状态和游标。
        for payment_hash, expected_status in expected_statuses.items():
            detail = self.fiber1.get_client().get_payment(
                {"payment_hash": payment_hash}
            )
            assert detail["status"] == expected_status

        inflight = self.fiber1.get_client().list_payments({"status": "Inflight"})
        assert [p["payment_hash"] for p in inflight["payments"]] == hold_hashes
        assert all(p["status"] == "Inflight" for p in inflight["payments"])
        assert inflight["last_cursor"] == hold_hashes[-1]

        created = self.fiber1.get_client().list_payments({"status": "Created"})
        assert created["payments"] == []
        assert created["last_cursor"] is None

        success = self.fiber1.get_client().list_payments({"status": "Success"})
        assert [
            p["payment_hash"] for p in success["payments"]
        ] == success_hashes, f"expected Success hashes {success_hashes}, got {success}"
        assert all(p["status"] == "Success" for p in success["payments"])
        assert success["last_cursor"] == success_hashes[-1]

        all_payments = self.fiber1.get_client().list_payments({})
        assert [p["payment_hash"] for p in all_payments["payments"]] == [
            payment_hash for payment_hash, _ in planned_payments
        ]
        assert all(
            p["status"] == expected_statuses[p["payment_hash"]]
            for p in all_payments["payments"]
        )
        assert all_payments["last_cursor"] == planned_payments[-1][0]

        # 4. 第一页跨过 Success，第二页仍须取到最后一笔 Inflight。
        page1 = self.fiber1.get_client().list_payments(
            {"status": "Inflight", "limit": hex(2)}
        )
        assert [p["payment_hash"] for p in page1["payments"]] == hold_hashes[:2]
        assert all(p["status"] == "Inflight" for p in page1["payments"])
        assert page1["last_cursor"] == hold_hashes[1]

        page2 = self.fiber1.get_client().list_payments(
            {"status": "Inflight", "limit": hex(2), "after": page1["last_cursor"]}
        )
        assert [p["payment_hash"] for p in page2["payments"]] == hold_hashes[2:]
        assert all(p["status"] == "Inflight" for p in page2["payments"])
        assert page2["last_cursor"] == hold_hashes[2]

        page3 = self.fiber1.get_client().list_payments(
            {"status": "Inflight", "limit": hex(2), "after": page2["last_cursor"]}
        )
        assert page3["payments"] == []
        assert page3["last_cursor"] is None
        assert [
            p["payment_hash"] for p in page1["payments"] + page2["payments"]
        ] == hold_hashes

        # 5. 游标指向不匹配的 Success 记录，也必须返回其后的 Inflight。
        after_success = self.fiber1.get_client().list_payments(
            {"status": "Inflight", "limit": hex(2), "after": success_hashes[-1]}
        )
        assert [p["payment_hash"] for p in after_success["payments"]] == [
            hold_hashes[-1]
        ]
        assert after_success["payments"][0]["status"] == "Inflight"
        assert after_success["last_cursor"] == hold_hashes[-1]

        # 6. 结算中间持单并等待终态，再核对全部列表中的状态迁移。
        settled_hash, settled_preimage = planned_payments[2]
        self.fiber2.get_client().settle_invoice(
            {"payment_hash": settled_hash, "payment_preimage": settled_preimage}
        )
        self.wait_invoice_state(self.fiber2, settled_hash, "Paid", 120, 1)
        self.wait_payment_state(self.fiber1, settled_hash, "Success", 120)
        assert (
            self.fiber1.get_client().get_payment({"payment_hash": settled_hash})[
                "status"
            ]
            == "Success"
        )
        expected_statuses[settled_hash] = "Success"

        inflight_after = self.fiber1.get_client().list_payments({"status": "Inflight"})
        assert [p["payment_hash"] for p in inflight_after["payments"]] == [
            hold_hashes[0],
            hold_hashes[2],
        ]
        assert all(p["status"] == "Inflight" for p in inflight_after["payments"])

        success_after = self.fiber1.get_client().list_payments({"status": "Success"})
        assert [p["payment_hash"] for p in success_after["payments"]] == sorted(
            success_hashes + [settled_hash]
        )
        assert all(p["status"] == "Success" for p in success_after["payments"])

        created_after = self.fiber1.get_client().list_payments({"status": "Created"})
        assert created_after["payments"] == []
        assert created_after["last_cursor"] is None

        all_after = self.fiber1.get_client().list_payments({})
        assert [p["payment_hash"] for p in all_after["payments"]] == [
            payment_hash for payment_hash, _ in planned_payments
        ]
        assert all(
            p["status"] == expected_statuses[p["payment_hash"]]
            for p in all_after["payments"]
        )
