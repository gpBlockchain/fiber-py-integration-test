import hashlib
import time

import pytest

from framework.basic_fiber import FiberTest
from framework.util import ckb_hash


class TestPendingTlcHandleUdt(FiberTest):
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 5}
    """
    pending tlc + watch tower,node1 force shutdown
    node1 和 node2 都没有tlc
    node1 有 N 个 tlc
        在tlc过期前 
            时间过去 0～1/3 个 delay_epoch
                node2 可以通过pre_image 解锁部分tlc
                    node2 无法解锁
                node1 无法解锁
            2/3～1
                node1无法取回
                node2 可以通过pre_image 解锁部分tlc
            时间过去 delay_epoch
                node2 可以舍弃tlc
                node1 无法解锁
        在tlc 过期后
            时间过去 0～ 1/3 个 delay_epoch
                node2 可以通过pre_image 解锁部分tlc
                node1 无法解锁
            时间过去 delay_epoch 1/3 -2/3
                node2 可以通过pre_image 解锁部分tlc
                node1 可以解锁
            2/3～1
                node1无法取回
                node2 可以通过pre_image 解锁部分tlc
            时间过去 delay_epoch
                node2 可以通过pre_image 解锁部分tlc
                node2 可以舍弃 tlc
                node1 可以解锁
    node2有N个tlc
        在tlc 过期前
            delay_epoch 过去0-1/3
                node1 可以通过pre_image解锁部分tlc
                    remove_tlc 会失败 可能测不了
                node2 无法解锁
            delay_epoch 1/3 -2/3
                node1 可以通过pre_image 解锁部分tlc
                    remove_tlc 会失败 可能测不了
                node2 无法解锁
            2/3～1
                node2无法取回
                node1 可以通过pre_image 解锁部分tlc
            >delay_epoch
                node1 可以通过pre_image 解锁部分tlc
                    remove_tlc 会失败 可能测不了
                node2 无法解锁
        在tlc 过期后
            delay_epoch 过去0-1/3
                node1 可以通过pre_image 解锁部分tlc
                    remove_tlc 会失败 可能测不了
                node2 无法解锁
            delay_epoch 1/3 -2/3
                node1 可以通过pre_image 解锁部分tlc
                    remove_tlc 会失败 可能测不了
                node2 可以解锁
            2/3～1
                node2无法取回
                node1 可以通过pre_image 解锁部分tlc
            >delay_epoch
                node1 可以通过pre_image 解锁部分tlc
                    remove_tlc 会失败 可能测不了
                node1 可以舍弃tlc
                node2 可以解锁
    node1和node2 都有n个tlc
        复杂的场景5,5个tlc，node2有一个能解锁的tlc
    测试N的上限
    """

    def teardown_method(self, method):
        super().teardown_method(method)

    def teardown_class(cls):
        cls.restore_time()
        super().teardown_class()

    # todo
    def test_1nodes_have_tlc_no_pre_image_node1_shutdown_un_expiry(self):
        """
        2边都有tlc
            未过期
              2边都无法解锁
        """
        self.faucet(
            self.fiber1.account_private,
            0,
            self.fiber1.account_private,
            10000 * 100000000,
        )
        before_balance = self.get_fibers_balance()
        self.open_channel(
            self.fiber1,
            self.fiber2,
            1000 * 100000000,
            1000 * 100000000,
            udt=self.get_account_udt_script(self.fiber1.account_private),
        )
        fiber1_preimage = self.generate_random_preimage()
        fiber1_invoice = self.fiber1.get_client().new_invoice(
            {
                "amount": hex(100000000),
                "currency": "Fibd",
                "description": "test invoice",
                "payment_hash": ckb_hash(fiber1_preimage),
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
            }
        )
        self.fiber2.get_client().send_payment(
            {
                "invoice": fiber1_invoice["invoice_address"],
            }
        )
        time.sleep(3)
        list_channels = self.fiber1.get_client().list_channels({})
        print("list channels:", list_channels)
        self.fiber1.get_client().shutdown_channel(
            {
                "channel_id": self.fiber1.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "force": True,
            }
        )
        force_shutdown_tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, force_shutdown_tx_hash)
        self.node.getClient().generate_epochs("0x1")
        time.sleep(10)
        status = self.node.getClient().get_live_cell("0x0", force_shutdown_tx_hash)
        assert status["status"] == "live"

    def test_2nodes_have_tlc_no_pre_image_node1_shutdown(self):
        """
        2边都有tlc
            未过期
              2边都无法解锁
        """
        self.faucet(
            self.fiber1.account_private,
            0,
            self.fiber1.account_private,
            10000 * 100000000,
        )
        before_balance = self.get_fibers_balance()
        self.open_channel(
            self.fiber1,
            self.fiber2,
            1000 * 100000000,
            1000 * 100000000,
            udt=self.get_account_udt_script(self.fiber1.account_private),
        )
        fiber2_preimage = self.generate_random_preimage()
        fiber1_preimage = self.generate_random_preimage()
        fiber2_invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(10000000),
                "currency": "Fibd",
                "description": "test invoice",
                "payment_hash": ckb_hash(fiber2_preimage),
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
            }
        )
        fiber1_invoice = self.fiber1.get_client().new_invoice(
            {
                "amount": hex(100000000),
                "currency": "Fibd",
                "description": "test invoice",
                "payment_hash": ckb_hash(fiber1_preimage),
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
            }
        )
        self.fiber1.get_client().send_payment(
            {
                "invoice": fiber2_invoice["invoice_address"],
            }
        )
        self.fiber2.get_client().send_payment(
            {
                "invoice": fiber1_invoice["invoice_address"],
            }
        )
        time.sleep(3)
        list_channels = self.fiber1.get_client().list_channels({})
        print("list channels:", list_channels)
        self.fiber1.get_client().shutdown_channel(
            {
                "channel_id": self.fiber1.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "force": True,
            }
        )
        force_shutdown_tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, force_shutdown_tx_hash)
        self.node.getClient().generate_epochs("0x1")
        time.sleep(10)
        status = self.node.getClient().get_live_cell("0x0", force_shutdown_tx_hash)
        assert status["status"] == "live"

    # @pytest.mark.skip("https://github.com/cryptape/acceptance-internal/issues/1244#issuecomment-3512261512")
    def test_2nodes_have_tlc_have_pre_image_node1_shutdown(self):
        """
        2边都有tlc
           没过期
            时间过去 0 ～ 1/3 个 delay_epoch
                node2 无法解锁
                node1 无法解锁
            时间过去 delay_epoch 1/3 - 2/3
                node1 可以通过 preimage 解锁 node2 的tlc
                node1 无法解锁自己的 tlc
                node2 无法解锁没有 pre_image的tlc
            时间过去 delay_epoch 2/3 - 1
                node1 无法取回
                node2 可以通过 pre_image 解锁部分tlc
            时间过去 delay_epoch
                node1 无法取回
                node2 可以通过 pre_image 解锁部分tlc
        """
        self.faucet(
            self.fiber1.account_private,
            0,
            self.fiber1.account_private,
            10000 * 100000000,
        )
        before_balance = self.get_fibers_balance()
        self.open_channel(
            self.fiber1,
            self.fiber2,
            1000 * 100000000,
            1000 * 100000000,
            udt=self.get_account_udt_script(self.fiber1.account_private),
        )
        fiber2_preimage = self.generate_random_preimage()
        fiber2_invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(10000000),
                "currency": "Fibd",
                "description": "test invoice",
                "payment_hash": ckb_hash(fiber2_preimage),
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
            }
        )

        fiber1_preimage = self.generate_random_preimage()
        fiber1_invoice = self.fiber1.get_client().new_invoice(
            {
                "amount": hex(100000000),
                "currency": "Fibd",
                "description": "test invoice",
                "payment_hash": ckb_hash(fiber1_preimage),
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
            }
        )

        fiber1_preimage_2_3 = self.generate_random_preimage()
        fiber1_invoice_2_3 = self.fiber1.get_client().new_invoice(
            {
                "amount": hex(100000000),
                "currency": "Fibd",
                "description": "test invoice",
                "payment_hash": ckb_hash(fiber1_preimage_2_3),
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
            }
        )
        fiber1_preimage_after_delay_epoch = self.generate_random_preimage()
        fiber1_invoice_after_delay_epoch = self.fiber1.get_client().new_invoice(
            {
                "amount": hex(100000000),
                "currency": "Fibd",
                "description": "test invoice",
                "payment_hash": ckb_hash(fiber1_preimage_after_delay_epoch),
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
            }
        )
        self.fiber1.get_client().send_payment(
            {
                "invoice": fiber2_invoice["invoice_address"],
            }
        )
        fiber2_payment = self.fiber2.get_client().send_payment(
            {
                "invoice": fiber1_invoice["invoice_address"],
            }
        )
        fiber2_payment_2_3 = self.fiber2.get_client().send_payment(
            {
                "invoice": fiber1_invoice_2_3["invoice_address"],
            }
        )
        fiber2_payment_after_delay_epoch = self.fiber2.get_client().send_payment(
            {
                "invoice": fiber1_invoice_after_delay_epoch["invoice_address"],
            }
        )

        time.sleep(3)
        list_channels = self.fiber1.get_client().list_channels({})
        print("list channels:", list_channels)
        self.fiber1.get_client().shutdown_channel(
            {
                "channel_id": self.fiber1.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "force": True,
            }
        )
        force_shutdown_tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, force_shutdown_tx_hash)
        # 时间过去 0～ 1/3 个 delay_epoch
        #                 node2 无法解锁
        #                 node1 无法解锁
        time.sleep(10)
        for i in range(600):
            self.Miner.miner_with_version(self.node, "0x0")
        time.sleep(10)
        # 时间过去 delay_epoch 1/3 -2/3
        #                 node1 可以通过 pre_image 解锁 node2 的tlc
        #                 node1 无法解锁自己的tlc
        #                 node2 无法解锁没有pre_image的tlc
        self.fiber1.get_client().settle_invoice(
            {
                "payment_hash": fiber2_payment["payment_hash"],
                "payment_preimage": fiber1_preimage,
            }
        )
        tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        msg = self.get_tx_message(tx_hash)
        print("msg:", msg)
        assert (
            msg["input_cells"][0]["udt_capacity"]
            - msg["output_cells"][0]["udt_capacity"]
            == 100000000
        )
        # 2/3～1
        #     node1无法取回
        #     node2 可以通过pre_image 解锁部分tlc
        for i in range(1200):
            self.Miner.miner_with_version(self.node, "0x0")
        time.sleep(10)
        self.fiber1.get_client().settle_invoice(
            {
                "payment_hash": fiber2_payment_2_3["payment_hash"],
                "payment_preimage": fiber1_preimage_2_3,
            }
        )
        tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        msg = self.get_tx_message(tx_hash)
        print("msg:", msg)
        assert (
            msg["input_cells"][0]["udt_capacity"]
            - msg["output_cells"][0]["udt_capacity"]
            == 100000000
        )

        # 时间过去 delay_epoch
        #     node2 可以通过pre_image 解锁部分tlc
        self.node.getClient().generate_epochs("0x1")
        time.sleep(10)
        self.fiber1.get_client().settle_invoice(
            {
                "payment_hash": fiber2_payment_after_delay_epoch["payment_hash"],
                "payment_preimage": fiber1_preimage_after_delay_epoch,
            }
        )
        tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        msg = self.get_tx_message(tx_hash)
        print("msg:", msg)
        assert (
            msg["input_cells"][0]["udt_capacity"]
            - msg["output_cells"][0]["udt_capacity"]
            == 100000000
        )

    def test_1nodes_have_tlc_have_pre_image_node1_shutdown(self):
        """
        1边有tlc
           没过期
            时间过去 0 ～ 1/3 个 delay_epoch
                node2 无法解锁
                node1 无法解锁
            时间过去 delay_epoch 1/3 - 2/3
                node1 可以通过 preimage 解锁 node2 的tlc
                node1 无法解锁自己的 tlc
                node2 无法解锁没有 pre_image的tlc
            时间过去 delay_epoch 2/3 - 1
                node1 无法取回
                node2 可以通过 pre_image 解锁部分tlc
            时间过去 delay_epoch
                node1 无法取回
                node2 可以通过 pre_image 解锁部分tlc
        """
        self.faucet(
            self.fiber1.account_private,
            0,
            self.fiber1.account_private,
            10000 * 100000000,
        )
        before_balance = self.get_fibers_balance()
        self.open_channel(
            self.fiber1,
            self.fiber2,
            1000 * 100000000,
            1000 * 100000000,
            udt=self.get_account_udt_script(self.fiber1.account_private),
        )
        fiber1_preimage = self.generate_random_preimage()
        fiber1_invoice = self.fiber1.get_client().new_invoice(
            {
                "amount": hex(100000000),
                "currency": "Fibd",
                "description": "test invoice",
                "payment_hash": ckb_hash(fiber1_preimage),
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
            }
        )

        fiber1_preimage_2_3 = self.generate_random_preimage()
        fiber1_invoice_2_3 = self.fiber1.get_client().new_invoice(
            {
                "amount": hex(100000000),
                "currency": "Fibd",
                "description": "test invoice",
                "payment_hash": ckb_hash(fiber1_preimage_2_3),
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
            }
        )
        fiber1_preimage_after_delay_epoch = self.generate_random_preimage()
        fiber1_invoice_after_delay_epoch = self.fiber1.get_client().new_invoice(
            {
                "amount": hex(100000000),
                "currency": "Fibd",
                "description": "test invoice",
                "payment_hash": ckb_hash(fiber1_preimage_after_delay_epoch),
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
            }
        )
        fiber2_payment = self.fiber2.get_client().send_payment(
            {
                "invoice": fiber1_invoice["invoice_address"],
            }
        )
        fiber2_payment_2_3 = self.fiber2.get_client().send_payment(
            {
                "invoice": fiber1_invoice_2_3["invoice_address"],
            }
        )
        fiber2_payment_after_delay_epoch = self.fiber2.get_client().send_payment(
            {
                "invoice": fiber1_invoice_after_delay_epoch["invoice_address"],
            }
        )

        time.sleep(3)
        list_channels = self.fiber1.get_client().list_channels({})
        print("list channels:", list_channels)
        self.fiber1.get_client().shutdown_channel(
            {
                "channel_id": self.fiber1.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "force": True,
            }
        )
        force_shutdown_tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, force_shutdown_tx_hash)
        # 时间过去 0～ 1/3 个 delay_epoch
        #                 node2 无法解锁
        #                 node1 无法解锁
        time.sleep(10)
        # 时间过去 delay_epoch 1/3 -2/3
        #                 node1 可以通过 pre_image 解锁 node2 的tlc
        #                 node1 无法解锁自己的tlc
        #                 node2 无法解锁没有pre_image的tlc
        for i in range(600):
            self.Miner.miner_with_version(self.node, "0x0")
        time.sleep(10)

        self.fiber1.get_client().settle_invoice(
            {
                "payment_hash": fiber2_payment["payment_hash"],
                "payment_preimage": fiber1_preimage,
            }
        )
        tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        msg = self.get_tx_message(tx_hash)
        print("msg:", msg)
        assert (
            msg["input_cells"][0]["udt_capacity"]
            - msg["output_cells"][0]["udt_capacity"]
            == 100000000
        )
        # 2/3 ～ 1
        #     node1无法取回
        #     node2 可以通过 pre_image 解锁部分tlc
        for i in range(1200):
            self.Miner.miner_with_version(self.node, "0x0")
        time.sleep(10)
        self.fiber1.get_client().settle_invoice(
            {
                "payment_hash": fiber2_payment_2_3["payment_hash"],
                "payment_preimage": fiber1_preimage_2_3,
            }
        )
        tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        msg = self.get_tx_message(tx_hash)
        print("msg:", msg)
        assert (
            msg["input_cells"][0]["udt_capacity"]
            - msg["output_cells"][0]["udt_capacity"]
            == 100000000
        )

        # 时间过去 delay_epoch
        #     node2 可以通过pre_image 解锁部分tlc
        self.node.getClient().generate_epochs("0x1")
        time.sleep(10)
        self.fiber1.get_client().settle_invoice(
            {
                "payment_hash": fiber2_payment_after_delay_epoch["payment_hash"],
                "payment_preimage": fiber1_preimage_after_delay_epoch,
            }
        )
        tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        msg = self.get_tx_message(tx_hash)
        print("msg:", msg)
        assert (
            msg["input_cells"][0]["udt_capacity"]
            - msg["output_cells"][0]["udt_capacity"]
            == 100000000
        )

    def test_1nodes_have_tlc_have_pre_image_node2_shutdown(self):
        """
        1边有tlc
           没过期
            时间过去 0 ～ 1/3 个 delay_epoch
                node2 无法解锁
                node1 无法解锁
            时间过去 delay_epoch 1/3 - 2/3
                node1 可以通过 preimage 解锁 node2 的tlc
                node1 无法解锁自己的 tlc
                node2 无法解锁没有 pre_image的tlc
            时间过去 delay_epoch 2/3 - 1
                node1 无法取回
                node2 可以通过 pre_image 解锁部分tlc
            时间过去 delay_epoch
                node1 无法取回
                node2 可以通过 pre_image 解锁部分tlc
        """
        self.faucet(
            self.fiber1.account_private,
            0,
            self.fiber1.account_private,
            10000 * 100000000,
        )
        before_balance = self.get_fibers_balance()
        self.open_channel(
            self.fiber1,
            self.fiber2,
            1000 * 100000000,
            1000 * 100000000,
            udt=self.get_account_udt_script(self.fiber1.account_private),
        )
        fiber1_preimage = self.generate_random_preimage()
        fiber1_invoice = self.fiber1.get_client().new_invoice(
            {
                "amount": hex(100000000),
                "currency": "Fibd",
                "description": "test invoice",
                "payment_hash": ckb_hash(fiber1_preimage),
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
            }
        )

        fiber1_preimage_2_3 = self.generate_random_preimage()
        fiber1_invoice_2_3 = self.fiber1.get_client().new_invoice(
            {
                "amount": hex(100000000),
                "currency": "Fibd",
                "description": "test invoice",
                "payment_hash": ckb_hash(fiber1_preimage_2_3),
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
            }
        )
        fiber1_preimage_after_delay_epoch = self.generate_random_preimage()
        fiber1_invoice_after_delay_epoch = self.fiber1.get_client().new_invoice(
            {
                "amount": hex(100000000),
                "currency": "Fibd",
                "description": "test invoice",
                "payment_hash": ckb_hash(fiber1_preimage_after_delay_epoch),
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
            }
        )
        fiber2_payment_after_delay_epoch = self.fiber2.get_client().send_payment(
            {
                "invoice": fiber1_invoice_after_delay_epoch["invoice_address"],
            }
        )
        fiber2_payment = self.fiber2.get_client().send_payment(
            {
                "invoice": fiber1_invoice["invoice_address"],
            }
        )
        fiber2_payment_2_3 = self.fiber2.get_client().send_payment(
            {
                "invoice": fiber1_invoice_2_3["invoice_address"],
            }
        )

        time.sleep(3)
        list_channels = self.fiber1.get_client().list_channels({})
        print("list channels:", list_channels)
        self.fiber2.get_client().shutdown_channel(
            {
                "channel_id": self.fiber1.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "force": True,
            }
        )
        force_shutdown_tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, force_shutdown_tx_hash)
        # 时间过去 0～ 1/3 个 delay_epoch
        #                 node2 无法解锁
        #                 node1 无法解锁
        time.sleep(10)
        for i in range(600):
            self.Miner.miner_with_version(self.node, "0x0")
        time.sleep(10)
        # 时间过去 delay_epoch 1/3 -2/3
        #                 node1 可以通过 pre_image 解锁 node2 的tlc
        #                 node1 无法解锁自己的tlc
        #                 node2 无法解锁没有pre_image的tlc
        self.fiber1.get_client().settle_invoice(
            {
                "payment_hash": fiber2_payment["payment_hash"],
                "payment_preimage": fiber1_preimage,
            }
        )
        tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        msg = self.get_tx_message(tx_hash)
        print("msg:", msg)
        assert (
            msg["input_cells"][0]["udt_capacity"]
            - msg["output_cells"][0]["udt_capacity"]
            == 100000000
        )
        # 2/3 ～ 1
        #     node1无法取回
        #     node2 可以通过 pre_image 解锁部分tlc
        for i in range(1200):
            self.Miner.miner_with_version(self.node, "0x0")
        time.sleep(10)
        self.fiber1.get_client().settle_invoice(
            {
                "payment_hash": fiber2_payment_2_3["payment_hash"],
                "payment_preimage": fiber1_preimage_2_3,
            }
        )
        tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        msg = self.get_tx_message(tx_hash)
        print("msg:", msg)
        assert (
            msg["input_cells"][0]["udt_capacity"]
            - msg["output_cells"][0]["udt_capacity"]
            == 100000000
        )

        # 时间过去 delay_epoch
        #     node2 可以通过pre_image 解锁部分tlc
        # todo 会发送解锁质押ckb交易，不会发送解锁tlc交易
        self.node.getClient().generate_epochs("0x1")
        time.sleep(10)
        self.fiber1.get_client().settle_invoice(
            {
                "payment_hash": fiber2_payment_after_delay_epoch["payment_hash"],
                "payment_preimage": fiber1_preimage_after_delay_epoch,
            }
        )
        tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        msg = self.get_tx_message(tx_hash)
        print("msg:", msg)
        assert (
            msg["input_cells"][0]["udt_capacity"]
            - msg["output_cells"][0]["udt_capacity"]
            == 100000000
        )

    # todo
    def test_settle_2_tlc(self):
        """
        节点1发现有多个tlc可以解锁
            但是也会一个个解锁
        """
        self.faucet(
            self.fiber1.account_private,
            0,
            self.fiber1.account_private,
            10000 * 100000000,
        )
        self.open_channel(
            self.fiber1,
            self.fiber2,
            1000 * 100000000,
            1000 * 100000000,
            udt=self.get_account_udt_script(self.fiber1.account_private),
        )
        tls_size = 2
        preimages = []
        invoices = []
        payments = []
        for i in range(tls_size):
            preimages.append(self.generate_random_preimage())
        for i in range(tls_size):
            fiber1_invoice = self.fiber1.get_client().new_invoice(
                {
                    "amount": hex(100000000),
                    "currency": "Fibd",
                    "description": "test invoice",
                    "payment_hash": ckb_hash(preimages[i]),
                    "udt_type_script": self.get_account_udt_script(
                        self.fiber1.account_private
                    ),
                }
            )
            invoices.append(fiber1_invoice)
        for i in range(tls_size):
            payment = self.fiber2.get_client().send_payment(
                {
                    "invoice": invoices[i]["invoice_address"],
                }
            )
            payments.append(payment)
        time.sleep(3)
        self.fiber1.get_client().shutdown_channel(
            {
                "channel_id": self.fiber1.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "force": True,
            }
        )
        force_shutdown_tx = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, force_shutdown_tx)
        for i in range(tls_size):
            self.fiber1.get_client().settle_invoice(
                {
                    "payment_hash": payments[i]["payment_hash"],
                    "payment_preimage": preimages[i],
                }
            )
        for i in range(tls_size):
            for j in range(600):
                self.Miner.miner_with_version(self.node, "0x0")
            settle_tx = self.wait_and_check_tx_pool_fee(1000, False)
            self.Miner.miner_until_tx_committed(self.node, settle_tx)
            msg = self.get_tx_message(settle_tx)
            assert (
                msg["input_cells"][0]["udt_capacity"]
                - msg["output_cells"][0]["udt_capacity"]
                == 100000000
            )

    # todo
    def test_tlc_expiry_2nodes_have_tlc_node1_shutdown_no_preimage(self):
        """
        2边都有tlc
            2边都没有preimage
        """
        self.faucet(
            self.fiber1.account_private,
            0,
            self.fiber1.account_private,
            10000 * 100000000,
        )
        before_balance = self.get_fibers_balance()
        self.open_channel(
            self.fiber1,
            self.fiber2,
            1000 * 100000000,
            1000 * 100000000,
            udt=self.get_account_udt_script(self.fiber1.account_private),
        )
        fiber2_preimage = self.generate_random_preimage()
        fiber1_preimage = self.generate_random_preimage()
        fiber2_invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(10000000),
                "currency": "Fibd",
                "description": "test invoice",
                "payment_hash": ckb_hash(fiber2_preimage),
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
            }
        )
        fiber1_invoice = self.fiber1.get_client().new_invoice(
            {
                "amount": hex(100000000),
                "currency": "Fibd",
                "description": "test invoice",
                "payment_hash": ckb_hash(fiber1_preimage),
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
            }
        )
        self.fiber1.get_client().send_payment(
            {
                "invoice": fiber2_invoice["invoice_address"],
            }
        )
        self.fiber2.get_client().send_payment(
            {
                "invoice": fiber1_invoice["invoice_address"],
            }
        )
        time.sleep(3)
        list_channels = self.fiber1.get_client().list_channels({})
        print("list channels:", list_channels)
        self.add_time_and_generate_epoch(25, 4)
        self.fiber1.get_client().shutdown_channel(
            {
                "channel_id": self.fiber1.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "force": True,
            }
        )
        force_shutdown_tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, force_shutdown_tx_hash)
        while len(self.get_commit_cells()) > 0:
            for i in range(600):
                self.Miner.miner_with_version(self.node, "0x0")
            time.sleep(10)
        after_balance = self.get_fibers_balance()
        result = self.get_balance_change(before_balance, after_balance)
        tx_trace = self.get_ln_tx_trace(force_shutdown_tx_hash)
        for tx in tx_trace:
            print("tx:", tx)

        # 第1笔强制关闭交易，间隔大约1200个块
        assert (
            1200
            < tx_trace[1]["msg"]["block_number"] - tx_trace[0]["msg"]["block_number"]
            < 1300
        )
        # 第2笔强制关闭交易，间隔大约1200个块
        assert (
            1200
            < tx_trace[2]["msg"]["block_number"] - tx_trace[1]["msg"]["block_number"]
            < 1300
        )
        # 第3笔强制关闭交易，间隔大约1800个块
        assert (
            1800
            < tx_trace[-1]["msg"]["block_number"] - tx_trace[-2]["msg"]["block_number"]
            < 2000
        )
        #  第4笔强制关闭交易，间隔大约1800个块
        assert (
            1800
            < tx_trace[-2]["msg"]["block_number"] - tx_trace[-3]["msg"]["block_number"]
            < 2000
        )
        # 节点1 消耗 1000 ckb+ 手续费
        # assert abs(result[0]['ckb'] - 1000 * 100000000) < 100000
        assert result[0]["udt"] == 1000 * 100000000
        # 节点2 获得1000 ckb + 手续费
        # assert abs(result[1]['ckb'] + 1000 * 100000000) < 100000
        assert result[1]["udt"] == -1000 * 100000000

    # todo
    def test_tlc_expiry_2nodes_have_tlc_node1_shutdown_node1_have_preimage_node2_stop(
        self,
    ):
        """
        过期
        2边都有tlc
        """
        self.faucet(
            self.fiber1.account_private,
            0,
            self.fiber1.account_private,
            10000 * 100000000,
        )
        before_balance = self.get_fibers_balance()
        self.open_channel(
            self.fiber1,
            self.fiber2,
            1000 * 100000000,
            1000 * 100000000,
            udt=self.get_account_udt_script(self.fiber1.account_private),
        )
        fiber2_preimage = self.generate_random_preimage()
        fiber2_invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(10000000),
                "currency": "Fibd",
                "description": "test invoice",
                "payment_hash": ckb_hash(fiber2_preimage),
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
            }
        )

        fiber1_preimage = self.generate_random_preimage()
        fiber1_invoice = self.fiber1.get_client().new_invoice(
            {
                "amount": hex(100000000),
                "currency": "Fibd",
                "description": "test invoice",
                "payment_hash": ckb_hash(fiber1_preimage),
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
            }
        )

        fiber1_preimage_2_3 = self.generate_random_preimage()
        fiber1_invoice_2_3 = self.fiber1.get_client().new_invoice(
            {
                "amount": hex(100000000),
                "currency": "Fibd",
                "description": "test invoice",
                "payment_hash": ckb_hash(fiber1_preimage_2_3),
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
            }
        )
        fiber1_preimage_after_delay_epoch = self.generate_random_preimage()
        fiber1_invoice_after_delay_epoch = self.fiber1.get_client().new_invoice(
            {
                "amount": hex(100000000),
                "currency": "Fibd",
                "description": "test invoice",
                "payment_hash": ckb_hash(fiber1_preimage_after_delay_epoch),
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
            }
        )
        self.fiber1.get_client().send_payment(
            {
                "invoice": fiber2_invoice["invoice_address"],
            }
        )
        fiber2_payment = self.fiber2.get_client().send_payment(
            {
                "invoice": fiber1_invoice["invoice_address"],
            }
        )
        fiber2_payment_2_3 = self.fiber2.get_client().send_payment(
            {
                "invoice": fiber1_invoice_2_3["invoice_address"],
            }
        )
        fiber2_payment_after_delay_epoch = self.fiber2.get_client().send_payment(
            {
                "invoice": fiber1_invoice_after_delay_epoch["invoice_address"],
            }
        )

        time.sleep(3)
        list_channels = self.fiber1.get_client().list_channels({})
        print("list channels:", list_channels)
        self.add_time_and_generate_epoch(25, 6)
        self.fiber1.get_client().shutdown_channel(
            {
                "channel_id": self.fiber1.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "force": True,
            }
        )
        force_shutdown_tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, force_shutdown_tx_hash)
        self.fiber2.stop()
        # 时间过去 0～ 1/3 个 delay_epoch
        #                 node2 无法解锁
        #                 node1 无法解锁
        time.sleep(10)
        for i in range(600):
            self.Miner.miner_with_version(self.node, "0x0")
        time.sleep(10)
        # 时间过去 delay_epoch 1/3 -2/3
        #                 node1 可以通过 pre_image 解锁 node2 的tlc
        #                 node1 无法解锁自己的tlc
        #                 node2 无法解锁没有pre_image的tlc
        self.fiber1.get_client().settle_invoice(
            {
                "payment_hash": fiber2_payment["payment_hash"],
                "payment_preimage": fiber1_preimage,
            }
        )
        tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        msg = self.get_tx_message(tx_hash)
        print("msg:", msg)
        assert (
            msg["input_cells"][0]["udt_capacity"]
            - msg["output_cells"][0]["udt_capacity"]
            == 100000000
        )
        # 2/3～1
        #     node1可以取回
        for i in range(1200):
            self.Miner.miner_with_version(self.node, "0x0")
        tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        msg = self.get_tx_message(tx_hash)
        print("msg:", msg)
        assert (
            msg["input_cells"][0]["udt_capacity"]
            - msg["output_cells"][0]["udt_capacity"]
            == 10000000
        )

        # node2 可以通过pre_image 解锁部分tlc
        for i in range(1200):
            self.Miner.miner_with_version(self.node, "0x0")
        time.sleep(10)
        self.fiber1.get_client().settle_invoice(
            {
                "payment_hash": fiber2_payment_2_3["payment_hash"],
                "payment_preimage": fiber1_preimage_2_3,
            }
        )
        tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        msg = self.get_tx_message(tx_hash)
        print("msg:", msg)
        assert (
            msg["input_cells"][0]["udt_capacity"]
            - msg["output_cells"][0]["udt_capacity"]
            == 100000000
        )

        # 时间过去 delay_epoch
        #     node2 会直接解锁本金
        self.node.getClient().generate_epochs("0x1")
        tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        msg = self.get_tx_message(tx_hash)
        print("msg:", msg)
        assert (
            msg["input_cells"][0]["udt_capacity"]
            - msg["output_cells"][0]["udt_capacity"]
            == 109790000000
        )

    # todo
    def test_tlc_expiry_1nodes_have_tlc_node1_shutdown(self):
        self.faucet(
            self.fiber1.account_private,
            0,
            self.fiber1.account_private,
            10000 * 100000000,
        )

        self.open_channel(
            self.fiber1,
            self.fiber2,
            1000 * 100000000,
            1000 * 100000000,
            udt=self.get_account_udt_script(self.fiber1.account_private),
        )
        tls_size = 2
        preimages = []
        invoices = []
        payments = []
        for i in range(tls_size):
            preimages.append(self.generate_random_preimage())
        for i in range(tls_size):
            fiber1_invoice = self.fiber1.get_client().new_invoice(
                {
                    "amount": hex(100000000),
                    "currency": "Fibd",
                    "description": "test invoice",
                    "payment_hash": ckb_hash(preimages[i]),
                    "udt_type_script": self.get_account_udt_script(
                        self.fiber1.account_private
                    ),
                }
            )
            invoices.append(fiber1_invoice)
        for i in range(tls_size):
            payment = self.fiber2.get_client().send_payment(
                {
                    "invoice": invoices[i]["invoice_address"],
                }
            )
            payments.append(payment)
        self.add_time_and_generate_block(25, 100)
        self.fiber1.get_client().shutdown_channel(
            {
                "channel_id": self.fiber1.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "force": True,
            }
        )
        force_shutdown_tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, force_shutdown_tx_hash)
        while len(self.get_commit_cells()) > 0:
            for i in range(600):
                self.Miner.miner_with_version(self.node, "0x0")
            time.sleep(10)
        txs = self.get_ln_tx_trace(force_shutdown_tx_hash)
        for tx in txs:
            print("tx:", tx)

    # todo
    def test_tlc_expiry_1nodes_have_tlc_node2_shutdown(self):
        self.faucet(
            self.fiber1.account_private,
            0,
            self.fiber1.account_private,
            10000 * 100000000,
        )

        self.open_channel(
            self.fiber1,
            self.fiber2,
            1000 * 100000000,
            1000 * 100000000,
            udt=self.get_account_udt_script(self.fiber1.account_private),
        )
        tls_size = 2
        preimages = []
        invoices = []
        payments = []
        for i in range(tls_size):
            preimages.append(self.generate_random_preimage())
        for i in range(tls_size):
            fiber1_invoice = self.fiber1.get_client().new_invoice(
                {
                    "amount": hex(100000000),
                    "currency": "Fibd",
                    "description": "test invoice",
                    "payment_hash": ckb_hash(preimages[i]),
                    "udt_type_script": self.get_account_udt_script(
                        self.fiber1.account_private
                    ),
                }
            )
            invoices.append(fiber1_invoice)
        for i in range(tls_size):
            payment = self.fiber2.get_client().send_payment(
                {
                    "invoice": invoices[i]["invoice_address"],
                }
            )
            payments.append(payment)
        self.add_time_and_generate_block(25, 100)
        self.fiber2.get_client().shutdown_channel(
            {
                "channel_id": self.fiber1.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "force": True,
            }
        )
        force_shutdown_tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, force_shutdown_tx_hash)
        while len(self.get_commit_cells()) > 0:
            for i in range(600):
                self.Miner.miner_with_version(self.node, "0x0")
            time.sleep(10)
        txs = self.get_ln_tx_trace(force_shutdown_tx_hash)
        for tx in txs:
            print("tx:", tx)

    # todo
    def test_2nodes_have_tlc_node2_shutdown_expiry(self):
        self.faucet(
            self.fiber1.account_private,
            0,
            self.fiber1.account_private,
            10000 * 100000000,
        )

        before_balance = self.get_fibers_balance()
        self.open_channel(
            self.fiber1,
            self.fiber2,
            1000 * 100000000,
            1000 * 100000000,
            udt=self.get_account_udt_script(self.fiber1.account_private),
        )
        fiber2_preimage = self.generate_random_preimage()
        fiber1_preimage = self.generate_random_preimage()
        fiber2_invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(1000000),
                "currency": "Fibd",
                "description": "test invoice",
                "payment_hash": ckb_hash(fiber2_preimage),
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
            }
        )
        fiber1_invoice = self.fiber1.get_client().new_invoice(
            {
                "amount": hex(1000000),
                "currency": "Fibd",
                "description": "test invoice",
                "payment_hash": ckb_hash(fiber1_preimage),
                "udt_type_script": self.get_account_udt_script(
                    self.fiber1.account_private
                ),
            }
        )
        self.fiber1.get_client().send_payment(
            {
                "invoice": fiber2_invoice["invoice_address"],
            }
        )
        self.fiber2.get_client().send_payment(
            {
                "invoice": fiber1_invoice["invoice_address"],
            }
        )
        time.sleep(3)
        list_channels = self.fiber1.get_client().list_channels({})
        print("list channels:", list_channels)
        self.add_time_and_generate_epoch(25, 1)
        self.fiber2.get_client().shutdown_channel(
            {
                "channel_id": self.fiber1.get_client().list_channels({})["channels"][0][
                    "channel_id"
                ],
                "force": True,
            }
        )
        force_shutdown_tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, force_shutdown_tx_hash)
        while len(self.get_commit_cells()) > 0:
            for i in range(600):
                self.Miner.miner_with_version(self.node, "0x0")
            time.sleep(10)
        after_balance = self.get_fibers_balance()
        result = self.get_balance_change(before_balance, after_balance)
        tx_trace = self.get_ln_tx_trace(force_shutdown_tx_hash)
        for tx in tx_trace:
            print("tx:", tx)
        # 第一笔强制关闭交易，间隔大约1800个块
        assert (
            1800
            < tx_trace[-1]["msg"]["block_number"] - tx_trace[-2]["msg"]["block_number"]
            < 2000
        )
        #  第二笔强制关闭交易，间隔大约1800个块
        assert (
            1800
            < tx_trace[-2]["msg"]["block_number"] - tx_trace[-3]["msg"]["block_number"]
            < 2000
        )
        # 节点1 消耗 1000 ckb+ 手续费
        assert result[0]["udt"] == 1000 * 100000000
        # 节点2 获得1000 ckb + 手续费
        assert result[1]["udt"] == -1000 * 100000000
