import time

import pytest

from framework.basic_fiber import FiberTest
from framework.config import DEFAULT_MIN_DEPOSIT_CKB
from framework.test_fiber import FiberConfigPath
from test_cases.fiber.devnet.migration._helpers import start_with_confirm


class TestData(FiberTest):
    # debug = True
    # @pytest.mark.skip("migration failed")
    def test_old_fiber(self):
        """
         1. start fiber
         2. open_channel with fiber
         3. stress test with fiber
         4. stop fiber
         5. restart fiber
         6. sleep 10 seconds
         7. restart other fiber
         8. send_payment
        Returns:
        """
        # 1. start fiber
        old_fiber_1 = self.start_new_fiber(
            self.generate_account(10000), fiber_version=FiberConfigPath.V070_DEV
        )
        old_fiber_2 = self.start_new_fiber(
            self.generate_account(10000), fiber_version=FiberConfigPath.V070_DEV
        )
        old_fiber_1.connect_peer(old_fiber_2)
        time.sleep(1)

        # 2. open_channel with fiber
        # self.open_channel(
        #     old_fiber_1, old_fiber_2, 1000 * 100000000, 1000 * 100000000, 1000, 1000
        # )
        old_fiber_1.get_client().open_channel(
            {
                "peer_id": old_fiber_1.get_client().list_peers()["peers"][0]["peer_id"],
                "funding_amount": hex(1000 * 100000000 + DEFAULT_MIN_DEPOSIT_CKB),
                "public": True,
            }
        )
        time.sleep(30)
        # 3. stress test with fiber
        for i in range(20):
            self.send_invoice_payment(old_fiber_1, old_fiber_2, 1, False)
            self.send_invoice_payment(old_fiber_2, old_fiber_1, 1, False)

        old_fiber_1.stop()
        old_fiber_2.stop()
        time.sleep(5)
        #  4. migration and restart fiber CURRENT_DEV
        old_fiber_1.fiber_config_enum = FiberConfigPath.V081_DEV
        old_fiber_2.fiber_config_enum = FiberConfigPath.V081_DEV
        old_fiber_1.migration()
        old_fiber_2.migration()

        old_fiber_1.fiber_config_enum = FiberConfigPath.CURRENT_DEV
        old_fiber_2.fiber_config_enum = FiberConfigPath.CURRENT_DEV
        time.sleep(5)
        start_with_confirm(old_fiber_1, confirm="y")
        start_with_confirm(old_fiber_2, confirm="y")
        time.sleep(10)
        self.open_channel(
            old_fiber_1, old_fiber_2, 1000 * 100000000, 1000 * 100000000, 1000, 1000
        )
        # 5. send_payment
        time.sleep(5)
        self.send_payment(old_fiber_1, old_fiber_2, 100000)
        self.send_payment(old_fiber_2, old_fiber_1, 200000)

    #     channels = old_fiber_1.get_client().list_channels({})
    #     before_balance = self.get_fibers_balance()
    #     fiber2_balance = self.get_fiber_balance(old_fiber_2)
    #     old_fiber_1.get_client().shutdown_channel(
    #         {
    #             "channel_id": channels["channels"][0]["channel_id"],
    #             "close_script": self.get_account_script(self.Config.ACCOUNT_PRIVATE_1),
    #             "fee_rate": "0x3FC",
    #         }
    #     )
    #     tx_hash = self.wait_and_check_tx_pool_fee(1000, False, 120)
    #     tx_message = self.get_tx_message(tx_hash)
    #     print("tx message:", tx_message)
    #     after_balance = self.get_fibers_balance()
    #     result = self.get_balance_change(before_balance, after_balance)
    #     print("result:", result)
    #     print("fiber2_balance:", fiber2_balance)
    #     # assert result == [
    #     #     {'ckb': 0, 'udt': 0}, {'ckb': 0, 'udt': 0},
    #     #     {'ckb': 0, 'udt': 0}, {'ckb': -109899999900, 'udt': 0}]
    #
    #     assert fiber2_balance["ckb"]["local_balance"] + 99 * 100000000 == abs(
    #         result[3]["ckb"]
    #     )
    #
    # def test_bbbb(self):
    #     old_fiber_1 = self.start_new_mock_fiber("")
    #     old_fiber_2 = self.start_new_mock_fiber("")
    #     self.open_channel(
    #         old_fiber_1,
    #         old_fiber_2, 1000 * 100000000, 1000 * 100000000, 1000, 1000
    #     )
    #     # 5. send_payment
    #     self.send_payment(old_fiber_1, old_fiber_2, 100000)
    #     self.send_payment(old_fiber_2, old_fiber_1, 200000)
    #     channels = old_fiber_1.get_client().list_channels({})
    #     before_balance = self.get_fibers_balance()
    #     fiber2_balance = self.get_fiber_balance(old_fiber_2)
    #     old_fiber_1.get_client().shutdown_channel(
    #         {
    #             "channel_id": channels["channels"][0]["channel_id"],
    #             "close_script": self.get_account_script(self.Config.ACCOUNT_PRIVATE_1),
    #             "fee_rate": "0x3FC",
    #         }
    #     )
    #     tx_hash = self.wait_and_check_tx_pool_fee(1000, False, 120)
    #     tx_message = self.get_tx_message(tx_hash)
    #     print("tx message:", tx_message)
    #     after_balance = self.get_fibers_balance()
    #     result = self.get_balance_change(before_balance, after_balance)
    #     print("result:", result)
    #     print("fiber2_balance:", fiber2_balance)
    #     # assert result == [
    #     #     {'ckb': 0, 'udt': 0}, {'ckb': 0, 'udt': 0},
    #     #     {'ckb': 0, 'udt': 0}, {'ckb': -109899999900, 'udt': 0}]
    #
    #     assert fiber2_balance["ckb"]["local_balance"] + 99 * 100000000 == abs(
    #         result[3]["ckb"]
    #     )
