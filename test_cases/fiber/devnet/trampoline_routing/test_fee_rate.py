import pytest

from framework.basic_share_fiber import SharedFiberTest
from framework.test_fiber import Fiber


class TestFeeRate(SharedFiberTest):
    # setup_method_only_once = True
    N = 2
    # 0-》1-》2-》3-》4
    # 0-》5 -》4
    # 0 -》6 -》5 -》4
    # debug = True
    fiber3: Fiber
    fiber4: Fiber

    fiber5: Fiber
    fiber6: Fiber

    def setUp(self):
        if getattr(TestFeeRate, "_channel_inited", False):
            return
        TestFeeRate._channel_inited = True

        # self.__class__.fiber3 = self.start_new_mock_fiber("")
        # self.__class__.fiber4 = self.start_new_mock_fiber("")
        # self.__class__.fiber5 = self.start_new_mock_fiber("")
        # self.__class__.fiber6 = self.start_new_mock_fiber("")

        self.__class__.fiber3 = self.start_new_fiber(self.generate_account(10000))
        self.__class__.fiber4 = self.start_new_fiber(self.generate_account(10000))

        self.__class__.fiber5 = self.start_new_fiber(self.generate_account(10000))
        self.__class__.fiber6 = self.start_new_fiber(self.generate_account(10000))

        for i in range(3):
            self.open_channel(
                self.fibers[i], self.fibers[(i + 1)], 1000 * 100000000, 1000 * 100000000
            )
            self.open_channel(
                self.fibers[i],
                self.fibers[(i + 1)],
                1000 * 100000000,
                1000 * 100000000,
                other_config={
                    "public": False,
                    "one_way": True,
                },
            )
        self.open_channel(self.fiber1, self.fiber5, 1000 * 100000000, 0, 6000, 9000)
        self.open_channel(self.fiber5, self.fiber6, 1000 * 100000000, 0, 5001, 7000)

    def test_dry_run(self):
        """fee 设置多少，就返回多少"""
        payment = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": self.fiber4.get_client().node_info()["pubkey"],
                "currency": "Fibd",
                "amount": hex(1 * 100000000),
                "keysend": True,
                "dry_run": True,
                "trampoline_hops": [
                    self.fiber2.get_client().node_info()["pubkey"],
                ],
            }
        )
        assert payment["fee"] == hex(500000)

        payment = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": self.fiber4.get_client().node_info()["pubkey"],
                "currency": "Fibd",
                "amount": hex(1 * 100000000),
                "keysend": True,
                "dry_run": True,
                "max_fee_amount": hex(400000),
                "trampoline_hops": [
                    self.fiber2.get_client().node_info()["pubkey"],
                ],
            }
        )
        assert payment["fee"] == hex(400000)

    def test_trampoline_1_path_2(self):
        self.send_payment(self.fiber2, self.fiber4, 1 * 100000000)
        before_balance = self.get_fibers_balance()
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
        self.wait_payment_state(self.fiber1, payment["payment_hash"], "Success")
        payment = self.fiber1.get_client().get_payment(
            {"payment_hash": payment["payment_hash"]}
        )
        print("payment", payment)
        after_balance = self.get_fibers_balance()
        result = self.get_channel_balance_change(before_balance, after_balance)
        print(result)
        assert result[:4] == [
            {
                "local_balance": 100500000,
                "offered_tlc_balance": 0,
                "received_tlc_balance": 0,
            },
            {
                "local_balance": -400000,
                "offered_tlc_balance": 0,
                "received_tlc_balance": 0,
            },
            {
                "local_balance": -100000,
                "offered_tlc_balance": 0,
                "received_tlc_balance": 0,
            },
            {
                "local_balance": -100000000,
                "offered_tlc_balance": 0,
                "received_tlc_balance": 0,
            },
        ]

    def test_fee_rate_check_enough(self):
        """
        dry_run: fee 不够时，返回no path found
        加大fee: dry_run 成功，发送send_payment(dry_)
        检查 fee 不够，能否返回正确的数据
        Returns:

        """
        before_balance = self.get_fibers_balance()

        with pytest.raises(Exception) as exc_info:
            dryPayment = self.fiber1.get_client().send_payment(
                {
                    "target_pubkey": self.fiber6.get_client().node_info()["pubkey"],
                    "currency": "Fibd",
                    "amount": hex(1 * 100000000),
                    "keysend": True,
                    "dry_run": True,
                    # "max_fee_amount": hex(5000),
                    # "max_fee_rate": hex(5)
                }
            )
        expected_error_message = "Failed to build route"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

        # 使用 trampoline_hops dry run
        dryPayment = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": self.fiber6.get_client().node_info()["pubkey"],
                "currency": "Fibd",
                "amount": hex(1 * 100000000),
                "keysend": True,
                "dry_run": True,
                # "max_fee_amount": hex(5000),
                # "max_fee_rate": hex(5)
                "trampoline_hops": [
                    self.fiber5.get_client().node_info()["pubkey"],
                ],
            }
        )
        assert dryPayment["fee"] == hex(500000)
        payment = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": self.fiber6.get_client().node_info()["pubkey"],
                "currency": "Fibd",
                "amount": hex(1 * 100000000),
                "keysend": True,
                # "max_fee_amount": hex(5000),
                # "max_fee_rate": hex(5)
                "trampoline_hops": [
                    self.fiber5.get_client().node_info()["pubkey"],
                ],
            }
        )
        self.wait_payment_state(self.fiber1, payment["payment_hash"], "Failed")
        failed_payment = self.fiber1.get_client().get_payment(
            {"payment_hash": payment["payment_hash"]}
        )
        assert failed_payment["failed_error"] == "FeeInsufficient"

        # 使用 trampoline_hops dry run ，预期失败
        dryPayment = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": self.fiber6.get_client().node_info()["pubkey"],
                "currency": "Fibd",
                "amount": hex(1 * 100000000),
                "keysend": True,
                "dry_run": True,
                # "max_fee_amount": hex(5000),
                "max_fee_rate": hex(10),
            }
        )
        print("dryPayment fee: ", int(dryPayment["fee"], 16))
        payment = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": self.fiber6.get_client().node_info()["pubkey"],
                "currency": "Fibd",
                "amount": hex(1 * 100000000),
                "keysend": True,
                "max_fee_amount": dryPayment["fee"],
                "max_fee_rate": hex(10),
            }
        )
        self.wait_payment_state(self.fiber1, payment["payment_hash"], "Success")
        after_balance = self.get_fibers_balance()
        result = self.get_channel_balance_change(before_balance, after_balance)
        print(result)
        assert result == [
            {
                "local_balance": 100500100,
                "offered_tlc_balance": 0,
                "received_tlc_balance": 0,
            },
            {"local_balance": 0, "offered_tlc_balance": 0, "received_tlc_balance": 0},
            {"local_balance": 0, "offered_tlc_balance": 0, "received_tlc_balance": 0},
            {"local_balance": 0, "offered_tlc_balance": 0, "received_tlc_balance": 0},
            {
                "local_balance": -500100,
                "offered_tlc_balance": 0,
                "received_tlc_balance": 0,
            },
            {
                "local_balance": -100000000,
                "offered_tlc_balance": 0,
                "received_tlc_balance": 0,
            },
        ]

        payment = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": self.fiber6.get_client().node_info()["pubkey"],
                "currency": "Fibd",
                "amount": hex(1 * 100000000),
                "keysend": True,
                # "max_fee_amount": hex(5000),
                "max_fee_rate": hex(10),
                "max_fee_amount": dryPayment["fee"],
                "trampoline_hops": [
                    self.fiber5.get_client().node_info()["pubkey"],
                ],
            }
        )
        self.wait_payment_state(self.fiber1, payment["payment_hash"], "Success")
        after2_balance = self.get_fibers_balance()
        result2 = self.get_channel_balance_change(after_balance, after2_balance)
        assert result == [
            {
                "local_balance": 100500100,
                "offered_tlc_balance": 0,
                "received_tlc_balance": 0,
            },
            {"local_balance": 0, "offered_tlc_balance": 0, "received_tlc_balance": 0},
            {"local_balance": 0, "offered_tlc_balance": 0, "received_tlc_balance": 0},
            {"local_balance": 0, "offered_tlc_balance": 0, "received_tlc_balance": 0},
            {
                "local_balance": -500100,
                "offered_tlc_balance": 0,
                "received_tlc_balance": 0,
            },
            {
                "local_balance": -100000000,
                "offered_tlc_balance": 0,
                "received_tlc_balance": 0,
            },
        ]
