import time
from datetime import datetime

import pytest

from framework.basic_fiber import FiberTest


class TestTlcTimeout(FiberTest):
    enable_cluster_clock = True
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 5}

    def teardown_class(cls):
        cls.restore_time()
        super().teardown_class()

    def test_remote_node_timeout(self):
        router_length = 2
        for i in range(router_length):
            account_private = self.generate_account(10000)
            fiber = self.start_new_fiber(account_private)
            fiber.connect_peer(self.fiber1)
            fiber.connect_peer(self.fiber2)
        for i in range(len(self.fibers) - 1):
            linked_fiber = self.fibers[(i + 1) % len(self.fibers)]
            current_fiber = self.fibers[i]
            linked_fiber.connect_peer(current_fiber)
            time.sleep(1)
            # open channel
            current_fiber.get_client().open_channel(
                {
                    "pubkey": linked_fiber.get_pubkey(),
                    "funding_amount": hex(1000 * 100000000),
                    "public": True,
                }
            )
            self.wait_for_channel_state(
                current_fiber.get_client(), linked_fiber.get_pubkey(), "ChannelReady"
            )

        time.sleep(1)

        invoice = (
            self.fibers[-1]
            .get_client()
            .new_invoice(
                {
                    "amount": hex(1 * 100000000),
                    "currency": "Fibd",
                    "description": "expired hold invoice",
                    # "expiry": "expiry_hex",
                    "payment_hash": self.generate_random_preimage(),
                    # "hash_algorithm": "sha256",
                }
            )
        )
        payment = self.fiber1.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
            }
        )
        time.sleep(10)
        # self.fibers[-1].stop()

        # 获取tlc的过期时间
        tlc = self.get_pending_tlc(self.fibers[-1], payment["payment_hash"])
        tlc_seconds = tlc["Inbound"][0]["expiry_seconds"]

        # 获取修改时间
        # tlc remove 时间: expiry_seconds - 1 * 60 - 2/3 * 4* 60 * 60
        tlc_seconds = tlc_seconds - 1 * 60 - 2 / 3 * 4 * 60 * 60
        hour = int(tlc_seconds / (60 * 60))
        minutes = int(tlc_seconds / 60 % 60)
        self.advance_business_time(hours=hour, minutes=minutes)
        self.node.getClient().generate_epochs("0x1")
        time.sleep(10)
        balance = self.get_fiber_balance(self.fibers[-1])
        print(balance)

        beginTime = time.time()
        while (
            self.get_fiber_balance(self.fiber1)
            .get("ckb", {"offered_tlc_balance": 0})
            .get("offered_tlc_balance")
            > 0
        ):
            time.sleep(10)
        endTime = time.time()
        print("cost time:", endTime - beginTime)
        time.sleep(5)
        fiber1_balance = self.get_fiber_balance(self.fiber1)
        assert fiber1_balance["ckb"]["offered_tlc_balance"] == 0
        self.wait_payment_state(self.fiber1, payment["payment_hash"], "Failed")

    def test_mid_node_shutdown_when_d_no_expiry(self):
        """
        a-b-c-d
          b-c 断连
            1. 时间:d(inbound快到期) c-d 能够正常在链下发送remove_tlc
            2. b-c 因为强制shutdown 会正常通过链上超时settle tlc
            3. a-b 能够正常在链下发送remove_tlc
        Returns:
        """
        router_length = 2
        for i in range(router_length):
            account_private = self.generate_account(10000)
            fiber = self.start_new_fiber(account_private)
            fiber.connect_peer(self.fiber1)
            fiber.connect_peer(self.fiber2)
        for i in range(len(self.fibers) - 1):
            linked_fiber = self.fibers[(i + 1) % len(self.fibers)]
            current_fiber = self.fibers[i]
            linked_fiber.connect_peer(current_fiber)
            time.sleep(1)
            # open channel
            current_fiber.get_client().open_channel(
                {
                    "pubkey": linked_fiber.get_pubkey(),
                    "funding_amount": hex(1000 * 100000000),
                    "public": True,
                }
            )
            self.wait_for_channel_state(
                current_fiber.get_client(), linked_fiber.get_pubkey(), "ChannelReady"
            )

        time.sleep(1)

        invoice = (
            self.fibers[-1]
            .get_client()
            .new_invoice(
                {
                    "amount": hex(1 * 100000000),
                    "currency": "Fibd",
                    "description": "expired hold invoice",
                    "payment_hash": self.generate_random_preimage(),
                }
            )
        )
        payment = self.fiber1.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
            }
        )
        time.sleep(10)

        self.fiber2.get_client().shutdown_channel(
            {
                "channel_id": self.fiber2.get_client().list_channels(
                    {"pubkey": self.fibers[2].get_pubkey()}
                )["channels"][0]["channel_id"],
                "force": True,
            }
        )
        shutdown_tx = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, shutdown_tx)
        self.wait_for_channel_state(
            self.fiber2.get_client(), self.fibers[2].get_pubkey(), "Closed", 320, True
        )
        self.wait_for_channel_state(
            self.fibers[2].get_client(),
            self.fibers[1].get_pubkey(),
            "Closed",
            320,
            True,
        )

        # 获取tlc的过期时间
        tlc = self.get_pending_tlc(self.fibers[-1], payment["payment_hash"])
        tlc_seconds = tlc["Inbound"][0]["expiry_seconds"]

        # 获取修改时间
        # tlc remove 时间: expiry_seconds - 1 * 60 - 2/3 * 4* 60 * 60
        tlc_seconds = tlc_seconds - 1 * 60 - 2 / 3 * 4 * 60 * 60
        hour = int(tlc_seconds / (60 * 60))
        minutes = int(tlc_seconds / 60 % 60)

        # 时间慢慢加上去

        self.advance_business_time(hours=hour, minutes=minutes)
        self.node.getClient().generate_epochs("0x1")
        time.sleep(10)
        balance = self.get_fiber_balance(self.fibers[-1])
        print(balance)

        beginTime = time.time()
        while (
            self.get_fiber_balance(self.fibers[-2])
            .get("ckb", {"offered_tlc_balance": 0})
            .get("offered_tlc_balance")
            > 0
        ):
            time.sleep(10)
        endTime = time.time()
        print("cost time:", endTime - beginTime)
        tlc = self.get_pending_tlc(self.fiber1, payment["payment_hash"])

        tlc_seconds = tlc["Outbound"][0]["expiry_seconds"]
        # tlc_seconds = tlc['Outbound'][0]['expiry_seconds']

        # 获取修改时间
        # tlc remove 时间: expiry_seconds - 1 * 60 - 2/3 * 4* 60 * 60
        tlc_seconds = tlc_seconds - 1 * 60 - 2 / 3 * 4 * 60 * 60
        hour = int(tlc_seconds / (60 * 60))
        minutes = int(tlc_seconds / 60 % 60)
        # 先让 InBould commit cell 过期
        latest_commit_tx_number = self.get_latest_commit_tx_number()
        self.add_time_and_generate_epoch(hour, 1)
        time.sleep(20)
        new_latest_commit_tx_number = self.get_latest_commit_tx_number()
        assert new_latest_commit_tx_number != latest_commit_tx_number

        # todo 等待
        for i in range(70):
            if (
                self.get_fiber_balance(self.fiber1)
                .get("ckb", {"offered_tlc_balance": 0})
                .get("offered_tlc_balance")
                == 0
            ):
                assert (
                    len(
                        self.fiber1.get_client().list_channels(
                            {"pubkey": self.fiber2.get_pubkey()}
                        )["channels"]
                    )
                    == 1
                )
                return
            time.sleep(1)
        raise Exception("time out for wait")

    def test_mid_node_shutdown_when_d_expiry(self):
        """
        a-b-c-d
          b-c 断连
            1. 时间:d(inbound已到期) c-d 能够正常在链下发送remove_tlc
            2. b-c 因为强制shutdown 会正常通过链上超时settle tlc
            3. a-b 能够正常在链下发送remove_tlc
        Returns:

        """
        router_length = 2
        for i in range(router_length):
            account_private = self.generate_account(10000)
            fiber = self.start_new_fiber(account_private)
            fiber.connect_peer(self.fiber1)
            fiber.connect_peer(self.fiber2)
        for i in range(len(self.fibers) - 1):
            linked_fiber = self.fibers[(i + 1) % len(self.fibers)]
            current_fiber = self.fibers[i]
            linked_fiber.connect_peer(current_fiber)
            time.sleep(1)
            # open channel
            current_fiber.get_client().open_channel(
                {
                    "pubkey": linked_fiber.get_pubkey(),
                    "funding_amount": hex(1000 * 100000000),
                    "public": True,
                }
            )
            self.wait_for_channel_state(
                current_fiber.get_client(), linked_fiber.get_pubkey(), "ChannelReady"
            )

        time.sleep(1)

        invoice = (
            self.fibers[-1]
            .get_client()
            .new_invoice(
                {
                    "amount": hex(1 * 100000000),
                    "currency": "Fibd",
                    "description": "expired hold invoice",
                    # "expiry": "expiry_hex",
                    "payment_hash": self.generate_random_preimage(),
                    # "hash_algorithm": "sha256",
                }
            )
        )
        payment = self.fiber1.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
            }
        )
        time.sleep(10)

        self.fiber2.get_client().shutdown_channel(
            {
                "channel_id": self.fiber2.get_client().list_channels(
                    {"pubkey": self.fibers[2].get_pubkey()}
                )["channels"][0]["channel_id"],
                "force": True,
            }
        )
        #
        # shutdown_tx = self.wait_and_check_tx_pool_fee(1000,False)
        # self.Miner.miner_until_tx_committed(self.node,shutdown_tx)
        # self.wait_for_channel_state(self.fiber2.get_client(),self.fibers[2].get_pubkey(), "Closed",320,True)
        # self.wait_for_channel_state(self.fibers[2].get_client(),self.fibers[1].get_pubkey(), "Closed",320,True)

        # 获取tlc的过期时间
        tlc = self.get_pending_tlc(self.fibers[-1], payment["payment_hash"])
        tlc_seconds = tlc["Inbound"][0]["expiry_seconds"]

        # 获取修改时间
        # tlc remove 时间: expiry_seconds - 1 * 60 - 2/3 * 4* 60 * 60
        tlc_seconds = tlc_seconds - 1 * 60 - 2 / 3 * 4 * 60 * 60
        hour = int(tlc_seconds / (60 * 60))
        minutes = int(tlc_seconds / 60 % 60)

        self.advance_business_time(hours=hour, minutes=minutes)
        self.node.getClient().generate_epochs("0x1")
        time.sleep(10)
        balance = self.get_fiber_balance(self.fibers[-1])
        print(balance)

        beginTime = time.time()
        while (
            self.get_fiber_balance(self.fibers[-2])
            .get("ckb", {"offered_tlc_balance": 0})
            .get("offered_tlc_balance")
            > 0
        ):
            time.sleep(10)
        endTime = time.time()
        print("cost time:", endTime - beginTime)
        tlc = self.get_pending_tlc(self.fiber1, payment["payment_hash"])

        tlc_seconds = tlc["Outbound"][0]["expiry_seconds"]
        # tlc_seconds = tlc['Outbound'][0]['expiry_seconds']

        # 获取修改时间
        # tlc remove 时间: expiry_seconds - 1 * 60 - 2/3 * 4* 60 * 60
        tlc_seconds = tlc_seconds - 1 * 60 - 2 / 3 * 4 * 60 * 60
        hour = int(tlc_seconds / (60 * 60))
        minutes = int(tlc_seconds / 60 % 60)
        # 先让 InBould commit cell 过期
        latest_commit_tx_number = self.get_latest_commit_tx_number()
        self.add_time_and_generate_epoch(hour, 1)
        time.sleep(20)
        new_latest_commit_tx_number = self.get_latest_commit_tx_number()
        assert new_latest_commit_tx_number != latest_commit_tx_number

        # todo 等待
        for i in range(70):
            if (
                self.get_fiber_balance(self.fiber1)
                .get("ckb", {"offered_tlc_balance": 0})
                .get("offered_tlc_balance")
                == 0
            ):
                assert (
                    len(
                        self.fiber1.get_client().list_channels(
                            {"pubkey": self.fiber2.get_pubkey()}
                        )["channels"]
                    )
                    == 1
                )
                return
            time.sleep(1)
        raise Exception("time out for wait")

    def test_remote_node_shutdown(self):
        """
        a-b-c-d
            d 节点不在线
            1. c-d inbound 已到期，c 强制shutdown后,链上发送settle_tlc 取回交易
            2. b-c a-b 链下取回tlc
        Returns:
        """
        router_length = 2
        for i in range(router_length):
            account_private = self.generate_account(10000)
            fiber = self.start_new_fiber(account_private)
            fiber.connect_peer(self.fiber1)
            fiber.connect_peer(self.fiber2)
        for i in range(len(self.fibers) - 1):
            linked_fiber = self.fibers[(i + 1) % len(self.fibers)]
            current_fiber = self.fibers[i]
            linked_fiber.connect_peer(current_fiber)
            time.sleep(1)
            # open channel
            current_fiber.get_client().open_channel(
                {
                    "pubkey": linked_fiber.get_pubkey(),
                    "funding_amount": hex(1000 * 100000000),
                    "public": True,
                }
            )
            self.wait_for_channel_state(
                current_fiber.get_client(), linked_fiber.get_pubkey(), "ChannelReady"
            )

        time.sleep(1)

        invoice = (
            self.fibers[-1]
            .get_client()
            .new_invoice(
                {
                    "amount": hex(1 * 100000000),
                    "currency": "Fibd",
                    "description": "expired hold invoice",
                    # "expiry": "expiry_hex",
                    "payment_hash": self.generate_random_preimage(),
                    # "hash_algorithm": "sha256",
                }
            )
        )
        payment = self.fiber1.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
            }
        )
        time.sleep(10)
        self.fibers[-1].stop()

        # 获取tlc的过期时间
        tlc = self.get_pending_tlc(self.fibers[-2], payment["payment_hash"])
        tlc_seconds = tlc["Inbound"][0]["expiry_seconds"]
        # tlc_seconds = tlc['Outbound'][0]['expiry_seconds']

        # 获取修改时间
        # tlc remove 时间: expiry_seconds - 1 * 60 - 2/3 * 4* 60 * 60
        tlc_seconds = tlc_seconds - 1 * 60 - 2 / 3 * 4 * 60 * 60
        hour = int(tlc_seconds / (60 * 60))
        minutes = int(tlc_seconds / 60 % 60)
        self.advance_business_time(hours=hour)
        self.node.getClient().generate_epochs("0x1", 0)
        shutdown_tx_hash = self.wait_and_check_tx_pool_fee(1000, False, 60 * 5)
        self.Miner.miner_until_tx_committed(self.node, shutdown_tx_hash)
        self.node.getClient().generate_epochs("0x1", 0)
        settle_tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, shutdown_tx_hash)
        time.sleep(100)
        balance = self.get_fiber_balance(self.fibers[-2])
        assert balance["ckb"]["offered_tlc_balance"] == 0
        time.sleep(1)
        balance = self.get_fiber_balance(self.fiber1)
        assert balance["ckb"]["offered_tlc_balance"] == 0

    @pytest.mark.skip("耗时太久")
    def test_remove_tlc_mix(self):
        """
        2. a-b-c-d-e
           h-j-k-d-e-f
           发送交易a->e,h->f
                d-e 强制shutdown
                   - a-b-c-d 超时正常remove_tlc,不会强制shutdown
                   - e-f 超时正常remove_tlc,不会强制shutdown h-j-k-d 超时正常remove_tlc,不会强制shutdown
        Returns:
        """
        self.nodeA = self.fiber1
        self.nodeB = self.fiber2
        #
        self.nodeC = self.start_new_fiber(self.generate_account(10000))
        self.nodeD = self.start_new_fiber(self.generate_account(10000))
        self.nodeE = self.start_new_fiber(self.generate_account(10000))
        self.nodeF = self.start_new_fiber(self.generate_account(10000))
        self.nodeH = self.start_new_fiber(self.generate_account(10000))
        self.nodeJ = self.start_new_fiber(self.generate_account(10000))
        self.nodeK = self.start_new_fiber(self.generate_account(10000))

        self.open_channel(self.nodeA, self.nodeB, 1000 * 100000000, 1000 * 100000000)
        self.open_channel(self.nodeB, self.nodeC, 1000 * 100000000, 1000 * 100000000)
        self.open_channel(self.nodeC, self.nodeD, 1000 * 100000000, 1000 * 100000000)
        self.open_channel(self.nodeD, self.nodeE, 1000 * 100000000, 1000 * 100000000)

        self.open_channel(self.nodeH, self.nodeJ, 1000 * 100000000, 1000 * 100000000)
        self.open_channel(self.nodeJ, self.nodeK, 1000 * 100000000, 1000 * 100000000)
        self.open_channel(self.nodeK, self.nodeD, 1000 * 100000000, 1000 * 100000000)
        self.open_channel(self.nodeE, self.nodeF, 1000 * 100000000, 1000 * 100000000)

        # new invoice by node-E
        invoiceE = self.nodeE.get_client().new_invoice(
            {
                "amount": hex(1 * 100000000),
                "currency": "Fibd",
                "description": "expired hold invoice",
                # "expiry": "expiry_hex",
                "payment_hash": self.generate_random_preimage(),
                # "hash_algorithm": "sha256",
            }
        )
        paymentA = self.nodeA.get_client().send_payment(
            {
                "invoice": invoiceE["invoice_address"],
            }
        )
        time.sleep(5)
        # new invoice by node - F
        invoiceF = self.nodeF.get_client().new_invoice(
            {
                "amount": hex(1 * 100000000),
                "currency": "Fibd",
                "description": "expired hold invoice",
                # "expiry": "expiry_hex",
                "payment_hash": self.generate_random_preimage(),
                # "hash_algorithm": "sha256",
            }
        )
        paymentH = self.nodeH.get_client().send_payment(
            {
                "invoice": invoiceF["invoice_address"],
            }
        )

        # 获取每一个时间点
        tlc_seconds_list = []
        for i in range(len(self.fibers)):
            pending_tlcH = self.get_pending_tlc(
                self.fibers[i], paymentH["payment_hash"]
            )
            pending_tlcA = self.get_pending_tlc(
                self.fibers[i], paymentA["payment_hash"]
            )
            if len(pending_tlcA["Inbound"]) == 1:
                tlc_seconds_list.append(pending_tlcA["Inbound"][0]["expiry_seconds"])
            if len(pending_tlcH["Inbound"]) == 1:
                tlc_seconds_list.append(pending_tlcH["Inbound"][0]["expiry_seconds"])
        tlc_seconds_list.sort()
        # force shutdown d-e
        # self.nodeD.get_client().shutdown_channel({
        #     "channel_id": self.nodeD.get_client().list_channels({"pubkey": self.nodeE.get_pubkey()})['channels'][0][
        #         'channel_id'],
        #     "force": True
        # })
        # force_shutdown_tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        # self.Miner.miner_until_tx_committed(self.node, force_shutdown_tx_hash)
        add_hour = 0
        begin_time = datetime.now()
        for tlc_seconds in tlc_seconds_list:
            print("tlc_seconds:", tlc_seconds)
            # 添加时间
            now = datetime.now()
            past_time = (now - begin_time).total_seconds()
            tlc_seconds = tlc_seconds - past_time
            tlc_seconds = tlc_seconds - 1 * 60 - 2 / 3 * 4 * 60 * 60
            hour = int(tlc_seconds / (60 * 60))
            minutes = int(tlc_seconds / 60 % 60)
            self.add_time_and_generate_epoch(hour, 1)
            self.advance_business_time(minutes=minutes)
            self.node.getClient().generate_epochs("0x1", 0)
            time.sleep(5 * 60)
            offered_tlc_balance_is_zero = True
            for fiber in self.fibers:
                fiber_balance = self.get_fiber_balance(fiber).get(
                    "ckb", {"offered_tlc_balance": 0}
                )
                if fiber_balance["offered_tlc_balance"] != 0:
                    offered_tlc_balance_is_zero = False
            if offered_tlc_balance_is_zero:
                return
        raise Exception("offered_tlc_balance != zero")
