import pytest

from framework.basic_fiber import FiberTest


class TestErrorScenarios(FiberTest):
    """
    测试 trampoline routing 的各种错误场景
    包括余额不足、通道容量不足、路径不存在等
    """

    def test_insufficient_balance(self):
        """
        测试余额不足的场景
        """
        self.fiber3 = self.start_new_fiber(self.generate_account(10000))

        # 建立通道，但余额较小
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0)
        self.open_channel(self.fiber2, self.fiber3, 1000 * 100000000, 0)

        # 尝试发送超过通道容量的金额
        with pytest.raises(Exception) as exc_info:
            payment = self.fiber1.get_client().send_payment(
                {
                    "target_pubkey": self.fiber3.get_client().node_info()["pubkey"],
                    "currency": "Fibd",
                    "amount": hex(2000 * 100000000),  # 超过通道容量
                    "keysend": True,
                    "trampoline_hops": [
                        self.fiber2.get_client().node_info()["pubkey"],
                    ],
                }
            )
        # 应该因为余额不足而失败
        assert "no path found" in exc_info.value.args[0] or "Failed" in str(
            exc_info.value
        )

    def test_channel_capacity_insufficient(self):
        """
        测试通道容量不足的场景
        """
        self.fiber3 = self.start_new_fiber(self.generate_account(10000))
        self.fiber4 = self.start_new_fiber(self.generate_account(10000))

        # 建立小容量通道
        self.open_channel(self.fiber1, self.fiber2, 50 * 100000000, 0)
        self.open_channel(self.fiber2, self.fiber3, 50 * 100000000, 0)
        self.open_channel(self.fiber3, self.fiber4, 50 * 100000000, 0)

        # 先发送一笔支付消耗部分容量
        payment1 = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": self.fiber4.get_client().node_info()["pubkey"],
                "currency": "Fibd",
                "amount": hex(30 * 100000000),
                "keysend": True,
                "trampoline_hops": [
                    self.fiber2.get_client().node_info()["pubkey"],
                    self.fiber3.get_client().node_info()["pubkey"],
                ],
            }
        )
        self.wait_payment_state(self.fiber1, payment1["payment_hash"], "Success")

        # 尝试发送超过剩余容量的金额
        with pytest.raises(Exception) as exc_info:
            payment2 = self.fiber1.get_client().send_payment(
                {
                    "target_pubkey": self.fiber4.get_client().node_info()["pubkey"],
                    "currency": "Fibd",
                    "amount": hex(30 * 100000000),  # 可能超过剩余容量
                    "keysend": True,
                    "trampoline_hops": [
                        self.fiber2.get_client().node_info()["pubkey"],
                        self.fiber3.get_client().node_info()["pubkey"],
                    ],
                }
            )
        # 可能因为容量不足而失败
        # 注意：如果容量足够，这个测试可能不会失败

    def test_no_path_to_trampoline(self):
        """
        测试无法到达 trampoline 节点的场景
        """
        self.fiber3 = self.start_new_fiber(self.generate_account(10000))
        self.fiber4 = self.start_new_fiber(self.generate_account(10000))

        # 只建立 fiber2 到 fiber3 的通道，fiber1 无法到达 fiber2
        self.open_channel(self.fiber2, self.fiber3, 1000 * 100000000, 0)
        self.open_channel(self.fiber3, self.fiber4, 1000 * 100000000, 0)

        # 尝试通过无法到达的 trampoline 节点路由
        with pytest.raises(Exception) as exc_info:
            payment = self.fiber1.get_client().send_payment(
                {
                    "target_pubkey": self.fiber4.get_client().node_info()["pubkey"],
                    "currency": "Fibd",
                    "amount": hex(1 * 100000000),
                    "keysend": True,
                    "trampoline_hops": [
                        self.fiber2.get_client().node_info()["pubkey"],
                    ],
                }
            )
        expected_error_message = "Insufficient balance"
        assert (
            expected_error_message in exc_info.value.args[0]
            or "no path found" in exc_info.value.args[0]
        ), (
            f"Expected substring '{expected_error_message}' or 'no path found' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

    def test_no_path_from_trampoline_to_target(self):
        """
        测试 trampoline 节点无法到达目标节点的场景
        """
        self.fiber3 = self.start_new_fiber(self.generate_account(10000))
        self.fiber4 = self.start_new_fiber(self.generate_account(10000))

        # 建立 fiber1 到 fiber2 的通道，但 fiber2 无法到达 fiber4
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0)
        # fiber2 和 fiber4 之间没有通道

        # 尝试通过无法到达目标的 trampoline 节点路由
        payment = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": self.fiber4.get_client().node_info()["pubkey"],
                "currency": "Fibd",
                "amount": hex(1 * 100000000),
                "keysend": True,
                "trampoline_hops": [
                    self.fiber2.get_client().node_info()["pubkey"],
                ],
            }
        )
        self.wait_payment_state(self.fiber1, payment["payment_hash"], "Failed")

    def test_max_fee_amount_too_low(self):
        """
        测试 max_fee_amount 设置过低的场景
        """
        self.fiber3 = self.start_new_fiber(self.generate_account(10000))

        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0)
        self.open_channel(self.fiber2, self.fiber3, 1000 * 100000000, 0)

        # 设置过低的 max_fee_amount
        with pytest.raises(Exception) as exc_info:
            payment = self.fiber1.get_client().send_payment(
                {
                    "target_pubkey": self.fiber3.get_client().node_info()["pubkey"],
                    "currency": "Fibd",
                    "amount": hex(1 * 100000000),
                    "keysend": True,
                    "max_fee_amount": hex(1),  # 极低的费用预算
                    "trampoline_hops": [self.fiber2.get_client().node_info()["pubkey"]],
                }
            )
        expected_error_message = "max_fee_amount is too low"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

    def test_duplicate_trampoline_hops(self):
        """
        测试重复的 trampoline hops（应该被拒绝）
        """
        self.fiber3 = self.start_new_fiber(self.generate_account(10000))

        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0)
        self.open_channel(self.fiber2, self.fiber3, 1000 * 100000000, 0)

        # 使用重复的 trampoline hop
        with pytest.raises(Exception) as exc_info:
            payment = self.fiber1.get_client().send_payment(
                {
                    "target_pubkey": self.fiber3.get_client().node_info()["pubkey"],
                    "currency": "Fibd",
                    "amount": hex(1 * 100000000),
                    "keysend": True,
                    "trampoline_hops": [
                        self.fiber2.get_client().node_info()["pubkey"],
                        self.fiber2.get_client().node_info()["pubkey"],  # 重复
                    ],
                }
            )
        # 应该因为重复的 hops 而失败
        assert "duplicate" in exc_info.value.args[0].lower() or "Failed" in str(
            exc_info.value
        )

    def test_target_in_trampoline_hops(self):
        """
        测试目标节点在 trampoline_hops 中（应该被拒绝）
        """
        self.fiber3 = self.start_new_fiber(self.generate_account(10000))

        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0)
        self.open_channel(self.fiber2, self.fiber3, 1000 * 100000000, 0)

        # 目标节点出现在 trampoline_hops 中
        with pytest.raises(Exception) as exc_info:
            payment = self.fiber1.get_client().send_payment(
                {
                    "target_pubkey": self.fiber3.get_client().node_info()["pubkey"],
                    "currency": "Fibd",
                    "amount": hex(1 * 100000000),
                    "keysend": True,
                    "trampoline_hops": [
                        self.fiber2.get_client().node_info()["pubkey"],
                        self.fiber3.get_client().node_info()["pubkey"],  # 目标节点
                    ],
                }
            )
        # 应该因为目标节点在 hops 中而失败
        assert "target_pubkey" in exc_info.value.args[0].lower() or "Failed" in str(
            exc_info.value
        )

    def test_empty_trampoline_hops(self):
        """
        测试空的 trampoline_hops（应该被拒绝）
        """
        self.fiber3 = self.start_new_fiber(self.generate_account(10000))

        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0)
        self.open_channel(self.fiber2, self.fiber3, 1000 * 100000000, 0)

        # 使用空的 trampoline_hops
        with pytest.raises(Exception) as exc_info:
            payment = self.fiber1.get_client().send_payment(
                {
                    "target_pubkey": self.fiber3.get_client().node_info()["pubkey"],
                    "currency": "Fibd",
                    "amount": hex(1 * 100000000),
                    "keysend": True,
                    "trampoline_hops": [],  # 空的 hops
                }
            )
        # 应该因为空的 hops 而失败
        assert "empty" in exc_info.value.args[0].lower() or "Failed" in str(
            exc_info.value
        )

    def test_trampoline_downstream_failure_error_propagates_to_payer(self):
        self.fiber3 = self.start_new_fiber(self.generate_account(10000))

        # A -> T1 有通道，但 T1 -> target 没有路径
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0)

        payment = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": self.fiber3.get_client().node_info()["pubkey"],
                "currency": "Fibd",
                "amount": hex(1 * 100000000),
                "keysend": True,
                "trampoline_hops": [
                    self.fiber2.get_client().node_info()["pubkey"],
                ],
            }
        )

        result = self.wait_payment_finished(self.fiber1, payment["payment_hash"])
        assert result["status"] == "Failed"
        assert result["failed_error"] == "TemporaryNodeFailure"

        payer_tlcs = self.get_pending_tlc(self.fiber1, payment["payment_hash"])
        trampoline_tlcs = self.get_pending_tlc(self.fiber2, payment["payment_hash"])

        def tlcs_empty_or_removed(tlcs):
            return all(
                next(iter(entry["tlc"]["status"].values())) == "RemoveAckConfirmed"
                for entries in tlcs.values()
                for entry in entries
            )

        assert tlcs_empty_or_removed(payer_tlcs)
        assert tlcs_empty_or_removed(trampoline_tlcs)
