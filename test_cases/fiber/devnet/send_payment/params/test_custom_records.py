import time

import pytest

from framework.basic_fiber import FiberTest


class TestCustomRecords(FiberTest):

    def test_custom(self):
        """
        {}
            none
            单独的key
            重复的key
                会过滤掉重复的key
            key 最大值
                0xffffffff
            key 最小值
                0x0
            特别大
                 OnionPacket(Sphinx(HopDataLenTooLarge))
            查询
                我方可以通过get_payment 查询
                接受方可以通过get_payment 查询: 目前不可以
                Returns:

        """
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1000 * 100000000)
        payment = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": self.fiber2.get_client().node_info()["node_id"],
                "amount": hex(100),
                "keysend": True,
                "allow_self_payment": True,
                "custom_records": {
                    "0x1": "0x1234",
                    "0x2": "0x5678",
                },
            }
        )
        print("payment:", payment)
        time.sleep(1)
        payment = self.fiber1.get_client().get_payment(
            {
                "payment_hash": payment["payment_hash"],
            }
        )
        assert {"0x1": "0x1234", "0x2": "0x5678"} == payment["custom_records"]
        # todo
        # self.fiber2.get_client().get_payment({
        #     "payment_hash": payment["payment_hash"],
        # })

        # custom_records empty string
        payment = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": self.fiber2.get_client().node_info()["node_id"],
                "amount": hex(100),
                "keysend": True,
                "allow_self_payment": True,
                "custom_records": {},
            }
        )
        print("payment:", payment)
        time.sleep(1)
        payment = self.fiber1.get_client().get_payment(
            {
                "payment_hash": payment["payment_hash"],
            }
        )
        assert {} == payment["custom_records"]

        payment = self.fiber1.get_client().get_payment(
            {
                "payment_hash": payment["payment_hash"],
            }
        )

        # 单独的key
        custom_records = {}
        for i in range(0, 20):
            custom_records.update({hex(i): self.generate_random_preimage()})
        payment = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": self.fiber2.get_client().node_info()["node_id"],
                "amount": hex(100),
                "keysend": True,
                "allow_self_payment": True,
                "custom_records": custom_records,
            }
        )
        print("payment:", payment)
        time.sleep(1)
        payment = self.fiber1.get_client().get_payment(
            {
                "payment_hash": payment["payment_hash"],
            }
        )
        assert custom_records == payment["custom_records"]

        # 最大值 和最小值
        payment = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": self.fiber2.get_client().node_info()["node_id"],
                "amount": hex(100),
                "keysend": True,
                "allow_self_payment": True,
                "custom_records": {
                    hex(65535): "0x1234",
                    "0x0": "0x5678",
                },
            }
        )
        print("payment:", payment)
        time.sleep(1)
        payment = self.fiber1.get_client().get_payment(
            {
                "payment_hash": payment["payment_hash"],
            }
        )
        assert {
            "0xffff": "0x1234",
            "0x0": "0x5678",
        } == payment["custom_records"]

        # 特别大 报错:  OnionPacket(Sphinx(HopDataLenTooLarge))
        with pytest.raises(Exception) as exc_info:
            payment = self.fiber1.get_client().send_payment(
                {
                    "target_pubkey": self.fiber2.get_client().node_info()["node_id"],
                    "amount": hex(100),
                    "keysend": True,
                    "allow_self_payment": True,
                    "custom_records": {
                        "0x12": self.generate_random_str(4096 + 2),
                        # "0x0": "0x5678",
                    },
                }
            )
        expected_error_message = "value can not more than 2048 bytes"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )

        # none
        payment = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": self.fiber2.get_client().node_info()["node_id"],
                "amount": hex(100),
                "keysend": True,
                "allow_self_payment": True,
            }
        )
        print("payment:", payment)
        time.sleep(1)
        payment = self.fiber1.get_client().get_payment(
            {
                "payment_hash": payment["payment_hash"],
            }
        )
        assert None == payment["custom_records"]
