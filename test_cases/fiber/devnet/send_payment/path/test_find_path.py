import time
import heapq

import pytest

from framework.basic_fiber import FiberTest


class FindPath(FiberTest):
    # lowest fee path
    # basic graph path finding

    # FiberTest.debug = True
    # 简单连通图 A->B->C
    # 单向图 A->B->C
    # 环形图 A->B->C->A
    # 多路径图 A->B->D A->C->D
    # 魔方图

    def test_linked_net(self):
        for i in range(1):
            fiber = self.start_new_fiber(self.generate_account(10000))
            fiber.connect_peer(self.fiber1)
            fiber.connect_peer(self.fiber2)
        for i in range(2):
            self.open_channel(
                self.fibers[0], self.fibers[1], 1000 * 100000000, 1000 * 100000000
            )
        self.open_channel(
            self.fibers[0], self.fibers[1], 1000 * 100000000, 1000 * 100000000
        )
        for i in range(2):
            self.open_channel(
                self.fibers[0], self.fibers[1], 10000 * 100000000, 1000 * 100000000
            )
        for i in range(2):
            self.open_channel(
                self.fibers[1], self.fibers[2], 1000 * 100000000, 1000 * 100000000
            )
        self.open_channel(
            self.fibers[1], self.fibers[2], 1000 * 100000000, 1000 * 100000000
        )
        for i in range(2):
            self.open_channel(
                self.fibers[1], self.fibers[2], 1000 * 100000000, 1000 * 100000000
            )

        self.send_payment(self.fibers[0], self.fibers[2], 3000 * 10000000)

    def test_mul_path(self):
        """
        1. A -> B -> D B(fee= 1000)
        2. A -> C -> D C(fee= 3000)
        3. A -> E ->F -> D E(fee= 1000) F(fee= 1000)
        Returns:
        """
        deploy_hash, deploy_index = self.udtContract.get_deploy_hash_and_index()
        update_config = {
            "ckb_rpc_url": self.node.rpcUrl,
            "fiber_open_channel_auto_accept_min_ckb_funding_amount": "1000000000000000",
            "ckb_udt_whitelist": True,
            "xudt_script_code_hash": self.Contract.get_ckb_contract_codehash(
                deploy_hash, deploy_index, True, self.node.rpcUrl
            ),
            "xudt_cell_deps_tx_hash": deploy_hash,
            "xudt_cell_deps_index": deploy_index,
        }
        for i in range(6):
            fiber = self.start_new_fiber(self.generate_account(1000000), update_config)
            self.fiber2.connect_peer(fiber)
        self.open_channel(
            self.new_fibers[0],
            self.new_fibers[2],
            110 * 100000000,
            110 * 100000000,
            1000,
            3000,
        )
        self.open_channel(
            self.new_fibers[2],
            self.new_fibers[3],
            110 * 100000000,
            110 * 100000000,
            1000,
            1000,
        )
        self.open_channel(
            self.new_fibers[0],
            self.new_fibers[1],
            110 * 100000000,
            110 * 100000000,
            1000,
            2500,
        )
        self.open_channel(
            self.new_fibers[1],
            self.new_fibers[3],
            110 * 100000000,
            110 * 100000000,
            1000,
            1000,
        )
        self.open_channel(
            self.new_fibers[0],
            self.new_fibers[4],
            110 * 100000000,
            110 * 100000000,
            1000,
            1000,
        )
        self.open_channel(
            self.new_fibers[4],
            self.new_fibers[5],
            110 * 100000000,
            110 * 100000000,
            1000,
            1000,
        )
        self.open_channel(
            self.new_fibers[5],
            self.new_fibers[3],
            110 * 100000000,
            110 * 100000000,
            1000,
            1000,
        )

        payment_hash1 = self.send_payment(
            self.new_fibers[0], self.new_fibers[3], 100 * 100000000
        )
        payment_hash2 = self.send_payment(
            self.new_fibers[0], self.new_fibers[3], 100 * 100000000
        )
        payment_hash3 = self.send_payment(
            self.new_fibers[0], self.new_fibers[3], 100 * 100000000
        )
        payment1 = (
            self.new_fibers[0].get_client().get_payment({"payment_hash": payment_hash1})
        )
        payment2 = (
            self.new_fibers[0].get_client().get_payment({"payment_hash": payment_hash2})
        )
        payment3 = (
            self.new_fibers[0].get_client().get_payment({"payment_hash": payment_hash3})
        )
        print("payment1", payment1)
        print("payment2", payment2)
        print("payment3", payment3)

    debug = True

    # @pytest.mark.skip("https://github.com/nervosnetwork/fiber/issues/475")
    def test_cycle_net(self):
        """
        0-1-2
        | | |
        3-4-5
        channel(1000,1000)
        for i in range(100):
            send_payment(500 ,self)
        Returns:
        """
        for i in range(4):
            fiber = self.start_new_fiber(self.generate_account(10000))
            fiber.connect_peer(self.fiber1)
        for i in range(2):
            self.open_channel(
                self.fibers[i], self.fibers[i + 1], 1000 * 100000000, 1000 * 100000000
            )
        for i in range(2):
            self.open_channel(
                self.fibers[i + 3],
                self.fibers[i + 4],
                1000 * 100000000,
                1000 * 100000000,
            )
        for i in range(3):
            self.open_channel(
                self.fibers[i], self.fibers[i + 3], 1000 * 100000000, 1000 * 100000000
            )
        hashes = [[], [], [], [], [], []]
        for j in range(100):
            for i in range(len(self.fibers)):
                try:
                    payment_hash = self.send_payment(
                        self.fibers[i], self.fibers[i], 500 * 10000000, False, None, 0
                    )
                    hashes[i].append(payment_hash)
                except:
                    pass

        for i in range(len(hashes)):
            for j in range(len(hashes[i])):
                self.wait_payment_finished(self.fibers[i], hashes[i][j], 1200)

        for i in range(len(self.fibers)):
            for i in range(20):
                payment_hash = self.send_payment(
                    self.fibers[i], self.fibers[i], 500 * 10000000, False
                )
                result = self.wait_payment_finished(self.fibers[i], payment_hash, 1200)
                if result["status"] == "Success":
                    break
                time.sleep(1)
                if i == 19:
                    raise Exception("payment failed")
        for i in range(len(self.fibers)):
            channels_balance = self.get_fiber_balance(self.fibers[i])
            assert channels_balance["ckb"]["offered_tlc_balance"] == 0
            assert channels_balance["ckb"]["received_tlc_balance"] == 0