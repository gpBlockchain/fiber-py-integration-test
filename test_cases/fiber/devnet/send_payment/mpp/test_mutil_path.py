import time

import pytest

from framework.basic_fiber import FiberTest
from framework.config import DEFAULT_MIN_DEPOSIT_UDT, DEFAULT_MIN_DEPOSIT_CKB


class MutilPathTestCase(FiberTest):
    # debug = True

    def test_mutil_to_one(self):
        """
        多-1(a->c)
        - a-1-b-1-c
        - a-2-b-1-c
        - a-1-d-1-b-1-c
        - a-2-d-1-b-1-c
        Returns:
        """
        self.start_new_fiber(
            self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
        )
        self.start_new_fiber(
            self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
        )
        # self.start_new_fiber(
        #     self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
        # )

        self.open_channel(self.fibers[0], self.fibers[1], 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fibers[0], self.fibers[1], 1000 * 100000000, 0, 0, 0)

        self.open_channel(self.fibers[0], self.fibers[3], 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fibers[0], self.fibers[3], 1000 * 100000000, 0, 0, 0)

        self.open_channel(self.fibers[3], self.fibers[2], 2000 * 100000000, 0, 0, 0)
        self.open_channel(self.fibers[1], self.fibers[2], 2000 * 100000000, 0, 0, 0)
        for i in range(10):
            time.sleep(2)
            self.send_invoice_payment(self.fibers[0], self.fibers[2], 4000 * 100000000)
            time.sleep(2)
            self.send_invoice_payment(self.fibers[2], self.fibers[0], 4000 * 100000000)

    # @pytest.mark.skip("This test is not stable, needs to be fixed")
    def test_mutil_to_one_2(self):
        self.start_new_fiber(
            self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
        )
        self.start_new_fiber(
            self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
        )

        self.open_channel(self.fibers[0], self.fibers[1], 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fibers[0], self.fibers[1], 1000 * 100000000, 0, 0, 0)

        self.open_channel(self.fibers[0], self.fibers[3], 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fibers[0], self.fibers[3], 1000 * 100000000, 0, 0, 0)

        self.open_channel(self.fibers[3], self.fibers[2], 2000 * 100000000, 0, 0, 0)
        self.open_channel(self.fibers[1], self.fibers[2], 2000 * 100000000, 0, 0, 0)

        time.sleep(2)
        self.send_invoice_payment(self.fibers[0], self.fibers[2], 2000 * 100000000)
        time.sleep(2)
        self.send_invoice_payment(self.fibers[0], self.fibers[2], 2000 * 100000000)
        time.sleep(2)
        try:
            self.send_invoice_payment(self.fibers[0], self.fibers[2], 2000 * 100000000)
        except Exception as e:
            pass
        self.send_invoice_payment(self.fibers[2], self.fibers[0], 2000 * 100000000)
        self.send_invoice_payment(self.fibers[2], self.fibers[0], 2000 * 100000000)
        self.send_invoice_payment(self.fibers[0], self.fibers[2], 1 * 100000000)

    def test_mutil_to_one_3(self):
        self.start_new_fiber(
            self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
        )
        self.start_new_fiber(
            self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
        )

        self.open_channel(
            self.fibers[0], self.fibers[1], 1000 * 100000000, 0, 1000, 1000
        )
        self.open_channel(
            self.fibers[0], self.fibers[1], 1000 * 100000000, 0, 1000, 1000
        )

        self.open_channel(
            self.fibers[0], self.fibers[3], 1000 * 100000000, 0, 1000, 1000
        )
        self.open_channel(
            self.fibers[0], self.fibers[3], 1000 * 100000000, 0, 1000, 1000
        )

        self.open_channel(
            self.fibers[3], self.fibers[2], 2000 * 100000000, 0, 1000, 1000
        )
        self.open_channel(
            self.fibers[1], self.fibers[2], 2000 * 100000000, 0, 1000, 1000
        )
        self.wait_graph_channels_sync(self.fiber1, 6)
        self.wait_graph_channels_sync(self.fiber2, 6)
        self.wait_graph_channels_sync(self.fibers[2], 6)
        self.wait_graph_channels_sync(self.fibers[3], 6)
        # print("channels len:", len(channels["channels"]))
        for i in range(3):
            self.send_invoice_payment(self.fibers[0], self.fibers[2], 1000 * 100000000)
        for i in range(10):
            self.send_invoice_payment(self.fibers[2], self.fibers[0], 2000 * 100000000)
            self.send_invoice_payment(self.fibers[0], self.fibers[2], 2000 * 100000000)

    def test_one_to_mutil(self):
        """
        - a-1-b-1-c
        - a-1-b-2-c
        - a-1-b-1-d-1-c
        Returns:

        """
        self.start_new_fiber(
            self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
        )
        self.start_new_fiber(
            self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
        )

        self.open_channel(self.fibers[0], self.fibers[1], 3000 * 100000000, 0, 0, 0)
        self.open_channel(self.fibers[1], self.fibers[2], 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fibers[1], self.fibers[2], 1000 * 100000000, 0, 0, 0)

        self.open_channel(self.fibers[1], self.fibers[3], 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fibers[3], self.fibers[2], 1000 * 100000000, 0, 0, 0)
        self.send_invoice_payment(self.fibers[0], self.fibers[2], 3000 * 100000000)
        self.send_invoice_payment(self.fibers[2], self.fibers[0], 3000 * 100000000)

    def test_one_mutil_one(self):
        """
        a-1-b-1-c-1-d
        a-1-b-2-c-1-d
        a-1-b-1-e-1-c-1-d
        a-1-b-2-e-2-c-1-d
        Returns:
        """
        self.start_new_fiber(
            self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
        )
        self.start_new_fiber(
            self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
        )
        self.start_new_fiber(
            self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
        )

        self.open_channel(self.fibers[0], self.fibers[1], 4000 * 100000000, 0, 0, 0)
        self.open_channel(self.fibers[1], self.fibers[2], 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fibers[1], self.fibers[2], 1000 * 100000000, 0, 0, 0)

        self.open_channel(self.fibers[1], self.fibers[4], 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fibers[1], self.fibers[4], 1000 * 100000000, 0, 0, 0)

        self.open_channel(self.fibers[1], self.fibers[2], 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fibers[1], self.fibers[2], 1000 * 100000000, 0, 0, 0)

        self.open_channel(self.fibers[4], self.fibers[2], 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fibers[4], self.fibers[2], 1000 * 100000000, 0, 0, 0)

        self.open_channel(self.fibers[2], self.fibers[3], 4000 * 100000000, 0, 0, 0)
        self.send_invoice_payment(self.fibers[0], self.fibers[3], 4000 * 100000000)
        self.send_invoice_payment(self.fibers[3], self.fibers[0], 4000 * 100000000)

    def test_mutil_mutil(self):
        """
        a-1-b-1-c
        a-2-b-2-c
        a-1-d-1-c
        a-2-d-2-c
        Returns:
        """
        self.start_new_fiber(
            self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
        )
        self.start_new_fiber(
            self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
        )

        self.open_channel(self.fibers[0], self.fibers[1], 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fibers[0], self.fibers[1], 1000 * 100000000, 0, 0, 0)

        self.open_channel(self.fibers[0], self.fibers[3], 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fibers[0], self.fibers[3], 1000 * 100000000, 0, 0, 0)

        self.open_channel(self.fibers[3], self.fibers[2], 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fibers[3], self.fibers[2], 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fibers[1], self.fibers[2], 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fibers[1], self.fibers[2], 1000 * 100000000, 0, 0, 0)
        self.send_invoice_payment(self.fibers[0], self.fibers[2], 4000 * 100000000)
        self.send_invoice_payment(self.fibers[2], self.fibers[0], 4000 * 100000000)

    def test_one_one_limit(self):
        N = 12
        for i in range(1, N):
            self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0, 0, 0)
            time.sleep(5)
            self.send_invoice_payment(self.fiber1, self.fiber2, 1000 * 100000000 * i)
            time.sleep(5)
            self.send_invoice_payment(self.fiber2, self.fiber1, 1000 * 100000000 * i)

    def test_one_mid_one_limit(self):
        for i in range(1, 12):
            print("current N:", i)
            fiber = self.start_new_fiber(
                self.generate_account(
                    10000, self.fiber1.account_private, 1000 * 100000000
                )
            )
            self.open_channel(fiber, self.fiber2, 1000 * 100000000, 0, 0, 0)

            self.open_channel(self.fiber1, fiber, 1000 * 100000000, 0, 0, 0)
            self.send_invoice_payment(self.fiber1, self.fiber2, 1000 * 100000000 * i)
            self.send_invoice_payment(self.fiber2, self.fiber1, 1000 * 100000000 * i)

    # def test_hold_timeout(self):
    #     """
    #     todo 模拟一个支付在中途被阻断的情况
    #     Returns:
    #
    #     """
    #     self.fiber3 = self.start_new_fiber(
    #         self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
    #     )
    #
    #     self.fiber4 = self.start_new_fiber(
    #         self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
    #     )
    #     self.fiber5 = self.start_new_fiber(
    #         self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
    #     )
    #
    #     self.open_channel(self.fiber1, self.fiber3, 1000 * 100000000, 0, 0, 0)
    #     self.open_channel(self.fiber3, self.fiber4, 1000 * 100000000, 0, 0, 0)
    #
    #     self.open_channel(self.fiber1, self.fiber2, 2000 * 100000000, 0, 0, 0)
    #     self.open_channel(self.fiber2, self.fiber5, 2000 * 100000000, 0, 0, 0)
    #     self.open_channel(self.fiber5, self.fiber4, 1000 * 100000000, 0, 0, 0)
    #     self.open_channel(self.fiber5, self.fiber4, 1000 * 100000000, 0, 0, 0)
    #     time.sleep(1)
    #     payment = self.send_invoice_payment(
    #         self.fiber1, self.fiber4, 3000 * 100000000, False
    #     )
    #     time.sleep(0.05)
    #     # self.fiber4.get_client().c
    #     # self.wait_payment_state(self.fiber1, payment,"Inflight",interval=0.1)
    #     self.fiber5.stop()
    #     self.wait_payment_state(self.fiber1, payment, "Failed")
    #     time.sleep(200)
    #     # self.get_fiber_graph_balance()
    #     self.fiber5.start()
    #     time.sleep(200)
    #     self.get_fibers_balance_message()
    #     # payment_hash = self.send_payment(self.fiber1, self.fiber4, 100 * 100000000,False)

    # def test_hold_timeout2(self):
    #     """
    #     todo 模拟一个支付在中途被阻断的情况
    #     todo 添加断言
    #     Returns:
    #
    #     """
    #     self.fiber3 = self.start_new_fiber(
    #         self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
    #     )
    #
    #     self.fiber4 = self.start_new_fiber(
    #         self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
    #     )
    #     self.fiber5 = self.start_new_fiber(
    #         self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
    #     )
    #
    #     self.open_channel(self.fiber1, self.fiber3, 1000 * 100000000, 0, 0, 0)
    #     self.open_channel(self.fiber3, self.fiber4, 1000 * 100000000, 0, 0, 0)
    #
    #     self.open_channel(self.fiber1, self.fiber2, 2000 * 100000000, 0, 0, 0)
    #     self.open_channel(self.fiber2, self.fiber5, 2000 * 100000000, 0, 0, 0)
    #     self.open_channel(self.fiber5, self.fiber4, 1000 * 100000000, 0, 0, 0)
    #     self.open_channel(self.fiber5, self.fiber4, 1000 * 100000000, 0, 0, 0)
    #
    #     time.sleep(10)
    #     payment = self.send_invoice_payment(
    #         self.fiber1, self.fiber4, 3000 * 100000000, False
    #     )
    #     # self.wait_payment_state(self.fiber1, payment,"Inflight",interval=0.1)
    #     self.fiber5.stop()
    #     self.wait_payment_state(self.fiber1, payment, "Failed")
    #     time.sleep(200)
    #     self.fiber5.start()
    #     time.sleep(200)
    #     self.get_fiber_graph_balance()

    def test_transfer_self(self):
        self.fiber3 = self.start_new_fiber(
            self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
        )
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber2, self.fiber3, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber2, self.fiber3, 1000 * 100000000, 0, 0, 0)

        self.open_channel(self.fiber3, self.fiber1, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber3, self.fiber1, 1000 * 100000000, 0, 0, 0)
        payments = [[], [], []]
        for j in range(30):
            # self.send_invoice_payment(self.fiber1,self.fiber1,2000 * 100000000,False)
            for i in range(3):
                try:
                    payment_hash = self.send_invoice_payment(
                        self.fibers[i],
                        self.fibers[i],
                        1001 * 100000000,
                        False,
                        None,
                        try_count=0,
                    )
                    payments[i].append(payment_hash)
                except:
                    pass
        for i in range(3):
            for payment_hash in payments[i]:
                self.wait_payment_finished(self.fibers[i], payment_hash, 1000)

        start = time.time()
        timeout = 400
        interval = 2
        while True:
            all_ok = True
            for fiber in self.fibers:
                balance = self.get_fiber_balance(fiber)
                if not (
                    balance["ckb"]["offered_tlc_balance"] == 0
                    and balance["ckb"]["received_tlc_balance"] == 0
                    and balance["ckb"]["local_balance"] == 200000000000
                ):
                    all_ok = False
                    break
            if all_ok:
                break
            if time.time() - start > timeout:
                raise AssertionError(
                    "Balances did not reach expected values within timeout"
                )
            time.sleep(interval)
        print("cost time:", time.time() - start)
        for i in range(3):
            self.send_invoice_payment(
                self.fibers[i], self.fibers[i], 100 * 100000000, True, None
            )

    def test_transfer_self_3(self):
        self.fiber3 = self.start_new_fiber(
            self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
        )
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber2, self.fiber3, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber2, self.fiber3, 1000 * 100000000, 0, 0, 0)

        self.open_channel(self.fiber3, self.fiber1, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber3, self.fiber1, 1000 * 100000000, 0, 0, 0)

        #    self.fiber1.stop()
        self.faucet(
            self.fiber1.account_private,
            10000,
            self.fiber1.account_private,
            10000 * 100000000,
        )
        self.open_channel(
            self.fiber1,
            self.fiber2,
            1000 * 100000000,
            0,
            0,
            0,
            self.get_account_udt_script(self.fiber1.account_private),
        )
        self.open_channel(
            self.fiber1,
            self.fiber2,
            1000 * 100000000,
            0,
            0,
            0,
            self.get_account_udt_script(self.fiber1.account_private),
        )

        self.faucet(
            self.fiber2.account_private,
            10000,
            self.fiber1.account_private,
            10000 * 100000000,
        )
        self.open_channel(
            self.fiber2,
            self.fiber3,
            1000 * 100000000,
            0,
            0,
            0,
            self.get_account_udt_script(self.fiber1.account_private),
        )
        self.open_channel(
            self.fiber2,
            self.fiber3,
            1000 * 100000000,
            0,
            0,
            0,
            self.get_account_udt_script(self.fiber1.account_private),
        )

        self.faucet(
            self.fiber3.account_private,
            10000,
            self.fiber1.account_private,
            10000 * 100000000,
        )
        self.open_channel(
            self.fiber3,
            self.fiber1,
            1000 * 100000000,
            0,
            0,
            0,
            self.get_account_udt_script(self.fiber1.account_private),
        )
        self.open_channel(
            self.fiber3,
            self.fiber1,
            1000 * 100000000,
            0,
            0,
            0,
            self.get_account_udt_script(self.fiber1.account_private),
        )

        for fiber in self.fibers[:3]:
            self.wait_graph_channels_sync(fiber, 12, 120)

        # The CKB payment amount is larger than one channel's balance, so it must
        # be split through multiple CKB paths. UDT channels should remain unused.
        for i in range(3):
            self.send_invoice_payment(
                self.fibers[i],
                self.fibers[i],
                1001 * 100000000,
                True,
                None,
                try_count=10,
            )

        start = time.time()
        timeout = 400
        while True:
            all_ok = True
            for fiber in self.fibers[:3]:
                balance = self.get_fiber_balance(fiber)
                print(balance)
                if balance["ckb"]["offered_tlc_balance"] != 0:
                    all_ok = False
                if balance["ckb"]["received_tlc_balance"] != 0:
                    all_ok = False
                for key in balance.keys():
                    if key == "ckb" and balance["ckb"]["local_balance"] != 200000000000:
                        all_ok = False
                    elif key == "chain":
                        continue
                    elif key != "ckb" and balance[key]["local_balance"] != (
                        200000000000 + DEFAULT_MIN_DEPOSIT_CKB * 2
                    ):
                        all_ok = False
            if all_ok:
                break
            if time.time() - start > timeout:
                raise AssertionError(
                    "Balances did not reach expected values within timeout"
                )
            time.sleep(2)

    def test_split(self):
        """
        去掉了 余额最小值PAYMENT_MAX_PARTS_LIMIT:10000
        Returns:
        """
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber2, self.fiber1, 1000 * 100000000, 10000 - 1, 0, 0)
        self.send_invoice_payment(
            self.fiber1, self.fiber2, 1000 * 100000000 + 10000 - 1
        )

    def test_max_fee(self):
        self.fiber3 = self.start_new_fiber(
            self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
        )
        self.fiber4 = self.start_new_fiber(
            self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
        )
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber2, self.fiber3, 1000 * 100000000, 0, 2000, 0)

        self.open_channel(self.fiber1, self.fiber4, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber4, self.fiber3, 1000 * 100000000, 0, 1000, 0)
        # self.open_channel(self.fiber4, self.fiber3, 1000 * 100000000, 0, 0, 0)

        time.sleep(1)
        invoice = self.fiber3.get_client().new_invoice(
            {
                "amount": hex(1000 * 100000000),
                "currency": "Fibd",
                "description": "test invoice generated by node2",
                "expiry": "0xe10",
                "final_cltv": "0x28",
                "payment_preimage": self.generate_random_preimage(),
                "hash_algorithm": "sha256",
                "allow_mpp": True,
            }
        )
        payment = self.fiber1.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                # "max_fee_amount": hex(109890100),
            }
        )
        print("payment:", payment)

    def test_not_use_false_router1(self):
        """
        拆分router 不会选择false的router
        Returns:
        """
        self.fiber3 = self.start_new_fiber(
            self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
        )
        self.open_channel(self.fiber1, self.fiber2, 3000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber2, self.fiber3, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber2, self.fiber3, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber2, self.fiber3, 1000 * 100000000, 0, 0, 0)
        channels = self.fiber2.get_client().list_channels(
            {
                "pubkey": self.fiber3.get_pubkey(),
            }
        )
        channel_id = channels["channels"][0]["channel_id"]
        self.fibers[1].get_client().update_channel(
            {"channel_id": channels["channels"][0]["channel_id"], "enabled": False}
        )
        time.sleep(1)

        with pytest.raises(Exception) as exc_info:
            # self.send_invoice_payment(self.fiber1, self.fiber2, 1000 * 100000000 + 10000 - 1)
            self.send_invoice_payment(self.fiber1, self.fiber3, 3000 * 100000000)
        expected_error_message = "no path found"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )
        self.send_invoice_payment(self.fiber1, self.fiber3, 2000 * 100000000)
        channels = self.fiber2.get_client().list_channels(
            {
                "pubkey": self.fiber3.get_pubkey(),
            }
        )
        for channel in channels["channels"]:
            if channel["channel_id"] == channel_id:
                assert channel["local_balance"] == hex(1000 * 100000000)

    def test_private_channel(self):
        self.fiber3 = self.start_new_fiber(
            self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
        )

        self.fiber1.get_client().open_channel(
            {
                "pubkey": self.fiber2.get_pubkey(),
                "funding_amount": hex(1000 * 100000000 + DEFAULT_MIN_DEPOSIT_CKB),
                "public": False,
            }
        )
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_pubkey(), "ChannelReady"
        )
        # self.fiber1.get_client().open_channel({
        #     "pubkey": self.fiber1.get_pubkey(),
        #     "funding_amount": hex(1000 * 100000000),
        #     "public": True,
        # })
        # self.wait_for_channel_state(self.fiber1, self.fiber2.get_pubkey(), "ChannelReady")
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0, 0, 0)
        time.sleep(10)
        self.send_invoice_payment(self.fiber1, self.fiber2, 2000 * 100000000)
        self.open_channel(self.fiber3, self.fiber2, 2000 * 100000000, 0, 0, 0)
        time.sleep(10)
        with pytest.raises(Exception) as exc_info:
            self.send_invoice_payment(self.fiber3, self.fiber1, 2000 * 100000000)
        expected_error_message = "no path found"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

    @pytest.mark.skip("todo")
    def test_mini_value(self):
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0, 0, 0)
        channels = self.fiber1.get_client().list_channels({})
        self.fiber1.get_client().update_channel(
            {
                "channel_id": channels["channels"][0]["channel_id"],
                "tlc_minimum_value": hex(10 * 100000000),
            }
        )
        self.fiber1.get_client().update_channel(
            {
                "channel_id": channels["channels"][1]["channel_id"],
                "tlc_minimum_value": hex(10 * 100000000),
            }
        )
        time.sleep(10)
        self.send_invoice_payment(self.fiber1, self.fiber2, 1001 * 100000000)

        # with pytest.raises(Exception) as exc_info:
        # expected_error_message = "no path found"
        # assert expected_error_message in exc_info.value.args[0], (
        #     f"Expected substring '{expected_error_message}' "
        #     f"not found in actual string '{exc_info.value.args[0]}'"
        # )

    def test_cancel_invoice(self):
        self.fiber3 = self.start_new_fiber(
            self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
        )

        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber2, self.fiber3, 3000 * 100000000, 0, 0, 0)
        time.sleep(1)
        for i in range(100):
            payment_hash = self.send_invoice_payment(
                self.fiber1, self.fiber3, 1 * 100000000, False
            )
            self.fiber3.get_client().cancel_invoice({"payment_hash": payment_hash})
        time.sleep(200)
        self.get_fiber_graph_balance()
        for fiber in self.fibers[:2]:
            balance = self.get_fiber_balance(fiber)
            assert balance["ckb"]["offered_tlc_balance"] == 0
            assert balance["ckb"]["received_tlc_balance"] == 0
            assert balance["ckb"]["local_balance"] == 3000 * 100000000

    @pytest.mark.skip("cost half")
    def test_invoice_diff_sender_cost_two(self):
        """
        1->2->3
        4->5->3
          双花
        Returns:
        """
        self.fiber3 = self.start_new_fiber(
            self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
        )
        self.fiber4 = self.start_new_fiber(
            self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
        )
        self.fiber5 = self.start_new_fiber(
            self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
        )
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber2, self.fiber3, 3000 * 100000000, 0, 0, 0)

        self.open_channel(self.fiber4, self.fiber5, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber4, self.fiber5, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber4, self.fiber5, 1000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber5, self.fiber3, 3000 * 100000000, 0, 0, 0)
        time.sleep(5)
        invoice = self.fiber3.get_client().new_invoice(
            {
                "amount": hex(3000 * 100000000),
                "currency": "Fibd",
                "description": "test invoice generated by node2",
                "expiry": "0xe10",
                "final_cltv": "0x28",
                "payment_preimage": self.generate_random_preimage(),
                "hash_algorithm": "sha256",
                "allow_mpp": True,
            }
        )
        self.fiber1.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "max_fee_amount": hex(1000000),
            }
        )
        self.fiber4.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "max_fee_amount": hex(1000000),
            }
        )

        self.wait_payment_finished(
            self.fiber1, invoice["invoice"]["data"]["payment_hash"]
        )
        self.wait_payment_finished(
            self.fiber4, invoice["invoice"]["data"]["payment_hash"]
        )

        self.get_fiber_graph_balance()
        # todo 只能有1笔成功

    @pytest.mark.skip("cost two account same time")
    def test_invoice_same_sender_cost_two(self):
        self.fiber3 = self.start_new_fiber(
            self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
        )
        self.open_channel(self.fiber1, self.fiber2, 2000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber1, self.fiber2, 2000 * 100000000, 0, 0, 0)
        # self.open_channel(self.fiber1, self.fiber2, 2000 * 100000000, 0, 0, 0)
        self.open_channel(self.fiber2, self.fiber3, 4000 * 100000000, 0, 0, 0)
        # self.open_channel(self.fiber2, self.fiber3, 4000 * 100000000, 0, 0, 0)

        time.sleep(5)
        invoice = self.fiber3.get_client().new_invoice(
            {
                "amount": hex(3000 * 100000000),
                "currency": "Fibd",
                "description": "test invoice generated by node2",
                # "expiry": "0xe10",
                # "final_cltv": "0x28",
                "payment_preimage": self.generate_random_preimage(),
                "hash_algorithm": "sha256",
                "allow_mpp": True,
                # "allow_atomic_mpp": True,
            }
        )
        payment = self.fiber1.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "max_fee_amount": hex(1000000),
            }
        )
        with pytest.raises(Exception) as exc_info:
            self.fiber1.get_client().send_payment(
                {
                    "invoice": invoice["invoice_address"],
                    "max_fee_amount": hex(1000000),
                }
            )
        expected_error_message = "Payment session already exists"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )
        self.wait_payment_finished(self.fiber1, payment["payment_hash"], 30)
        balance = self.get_fiber_balance(self.fiber3)
        assert balance["ckb"]["local_balance"] == 300000000000
