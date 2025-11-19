import time

from framework.basic_fiber import FiberTest
from framework.config import DEFAULT_MIN_DEPOSIT_CKB


class TestCkbWithUDT(FiberTest):

    def test_ckb_with_udt(self):
        """
        https://github.com/nervosnetwork/fiber/pull/681
        a=b=c=a(ckb,udt1,udt2)
        a->a 100笔 ckb
        a-> 100 笔 udt1
        检查余额： udt2 没变

        https://github.com/nervosnetwork/fiber/pull/683
        a=b=c=a(ckb,udt1,udt2)
        a.send(a-(ckb)-b-(udt1)-c-(ckb)-a) 报错
        a.send(a-(udt1)-b-(udt1)-c-(udt2)-a) 报错
        a.send(a-(ckb)-b-(ckb)-c-(udt2)-a) 报错
        a.send(a-(udt1)-b-(udt2)-c-(udt1)-a) 报错

        Returns:

        """
        self.start_new_fiber(
            self.generate_account(10000, self.fiber1.account_private, 1000 * 100000000)
        )
        self.faucet(
            self.fibers[1].account_private,
            10000,
            self.fiber1.account_private,
            10000 * 100000000,
        )
        self.faucet(
            self.fibers[2].account_private,
            0,
            self.fiber1.account_private,
            10000 * 100000000,
        )
        self.faucet(
            self.fiber1.account_private,
            0,
            self.fiber1.account_private,
            10000 * 100000000,
        )

        self.open_channel(
            self.fibers[0],
            self.fibers[1],
            1000 * 100000000,
            1000 * 100000000,
            1000,
            1000,
        )
        self.open_channel(
            self.fibers[1],
            self.fibers[2],
            1000 * 100000000,
            1000 * 100000000,
            1000,
            1000,
        )
        self.open_channel(
            self.fibers[2],
            self.fibers[0],
            1000 * 100000000,
            1000 * 100000000,
            1000,
            1000,
        )

        self.open_channel(
            self.fibers[0],
            self.fibers[1],
            1000 * 100000000,
            1000 * 100000000,
            1000,
            1000,
            self.get_account_udt_script(self.fiber1.account_private),
        )
        self.open_channel(
            self.fibers[1],
            self.fibers[2],
            1000 * 100000000,
            1000 * 100000000,
            1000,
            1000,
            self.get_account_udt_script(self.fiber1.account_private),
        )

        self.send_payment(self.fibers[1], self.fibers[2], 1000 * 100000000)
        self.open_channel(
            self.fibers[2],
            self.fibers[0],
            1000 * 100000000,
            1000 * 100000000,
            1000,
            1000,
            self.get_account_udt_script(self.fiber1.account_private),
        )

        self.faucet(
            self.fibers[2].account_private,
            0,
            self.fiber2.account_private,
            10000 * 100000000,
        )
        self.faucet(
            self.fiber1.account_private,
            0,
            self.fiber2.account_private,
            10000 * 100000000,
        )
        self.faucet(
            self.fibers[1].account_private,
            10000,
            self.fiber2.account_private,
            10000 * 100000000,
        )
        self.open_channel(
            self.fibers[0],
            self.fibers[1],
            1000 * 100000000,
            1000 * 100000000,
            1000,
            1000,
            self.get_account_udt_script(self.fiber2.account_private),
        )
        self.open_channel(
            self.fibers[1],
            self.fibers[2],
            1000 * 100000000,
            1000 * 100000000,
            1000,
            1000,
            self.get_account_udt_script(self.fiber2.account_private),
        )
        self.open_channel(
            self.fibers[2],
            self.fibers[0],
            1000 * 100000000,
            1000 * 100000000,
            1000,
            1000,
            self.get_account_udt_script(self.fiber2.account_private),
        )

        before_fibers_balance = self.get_fibers_balance_message()
        for i in range(30):
            self.send_payment(self.fibers[0], self.fibers[0], 1, False)
            self.send_payment(
                self.fibers[0],
                self.fibers[0],
                1,
                False,
                self.get_account_udt_script(self.fiber2.account_private),
            )

        time.sleep(10)
        after_fibers_balance = self.get_fibers_balance_message()
        print("before_fibers_balance:", before_fibers_balance)
        print("after_fibers_balance", after_fibers_balance)
        key = self.get_account_udt_script(self.fiber1.account_private)["args"]
        for fiber in self.fibers:
            fiber_map = self.get_fiber_balance(fiber)
            assert fiber_map[key] == {
                "local_balance": 200000000000 + DEFAULT_MIN_DEPOSIT_CKB,
                "offered_tlc_balance": 0,
                "received_tlc_balance": 0,
            }

        #
        self.send_payment(self.fibers[0], self.fibers[0], 1)
        self.send_payment(
            self.fibers[0],
            self.fibers[0],
            1,
            self.get_account_udt_script(self.fiber2.account_private),
        )

        amount = 1
        ckb_router = self.fiber1.get_client().build_router(
            {
                "amount": hex(amount),
                "hops_info": [
                    self.get_node_hops_info(self.fiber1, self.fiber2, amount)[0],
                    self.get_node_hops_info(self.fibers[1], self.fibers[2], amount)[0],
                    self.get_node_hops_info(self.fibers[2], self.fibers[0], amount)[0],
                ],
            }
        )
        udt1_router = self.fiber1.get_client().build_router(
            {
                "amount": hex(amount),
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
                "hops_info": [
                    self.get_node_hops_info(
                        self.fiber1,
                        self.fiber2,
                        amount,
                        self.get_account_udt_script(self.fiber1.account_private),
                    )[0],
                    self.get_node_hops_info(
                        self.fibers[1],
                        self.fibers[2],
                        amount,
                        self.get_account_udt_script(self.fiber1.account_private),
                    )[0],
                    self.get_node_hops_info(
                        self.fibers[2],
                        self.fibers[0],
                        amount,
                        self.get_account_udt_script(self.fiber1.account_private),
                    )[0],
                ],
            }
        )
        udt2_router = self.fiber1.get_client().build_router(
            {
                "amount": hex(amount),
                "udt_type_script": self.get_account_udt_script(
                    self.fiber2.account_private
                ),
                "hops_info": [
                    self.get_node_hops_info(
                        self.fiber1,
                        self.fiber2,
                        amount,
                        self.get_account_udt_script(self.fiber2.account_private),
                    )[0],
                    self.get_node_hops_info(
                        self.fibers[1],
                        self.fibers[2],
                        amount,
                        self.get_account_udt_script(self.fiber2.account_private),
                    )[0],
                    self.get_node_hops_info(
                        self.fibers[2],
                        self.fibers[0],
                        amount,
                        self.get_account_udt_script(self.fiber2.account_private),
                    )[0],
                ],
            }
        )

        # ckb udt1 udt2 will failed
        router = []
        router.append(ckb_router["router_hops"][0])
        router.append(udt1_router["router_hops"][1])
        router.append(udt2_router["router_hops"][2])

        payment = self.fiber1.get_client().send_payment_with_router(
            {
                "keysend": True,
                "dry_run": False,
                "router": router,
            }
        )
        self.wait_payment_state(self.fiber1, payment["payment_hash"], "Failed")

        # udt1 udt1 udt2 will failed
        router = []
        router.append(udt1_router["router_hops"][0])
        router.append(udt1_router["router_hops"][1])
        router.append(udt2_router["router_hops"][2])

        payment = self.fiber1.get_client().send_payment_with_router(
            {
                "keysend": True,
                "dry_run": False,
                "router": router,
            }
        )
        self.wait_payment_state(self.fiber1, payment["payment_hash"], "Failed")

        # ckb udt ckb will failed
        router = []
        router.append(ckb_router["router_hops"][0])
        router.append(udt1_router["router_hops"][1])
        router.append(ckb_router["router_hops"][2])

        payment = self.fiber1.get_client().send_payment_with_router(
            {
                "keysend": True,
                "dry_run": False,
                "router": router,
            }
        )
        self.wait_payment_state(self.fiber1, payment["payment_hash"], "Failed")

        # udt1 udt2 udt1 will failed
        router = []
        router.append(udt1_router["router_hops"][0])
        router.append(udt2_router["router_hops"][1])
        router.append(udt1_router["router_hops"][2])

        payment = self.fiber1.get_client().send_payment_with_router(
            {
                "keysend": True,
                "dry_run": False,
                "router": router,
            }
        )
        self.wait_payment_state(self.fiber1, payment["payment_hash"], "Failed")
