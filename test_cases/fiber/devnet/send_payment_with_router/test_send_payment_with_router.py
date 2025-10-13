import time
from framework.basic_fiber import FiberTest
import pytest


class TestSendPaymentWithRouter(FiberTest):
    """
    测试 send_payment_with_router RPC 的功能：
    1. 基本支付功能
       - 使用指定路由发送支付，d call a ,route info: d-a channel outpoint
       - 验证支付状态和路由信息

    2. 路由指定测试
       - 使用 build_router 构建的路由进行支付
       - 手动指定完整路由进行支付
       - 测试无效路由的情况，b call a ,走route info: b-c-d-私-a网络（检查应该不支持自动拼接完整的路由）
       - b-c-d-私-a网络-b网络(网络成环状)，b call b成环，走route info:b-c-d-a-b

    3. 支付选项测试
       - 测试指定 payment_hash 的情况
       - 测试使用 invoice 的情况
       - 测试 keysend 支付
       - 测试带自定义记录的支付

    4. 特殊情况测试
       - 使用 dry_run 模式测试支付可行性
       - 测试 UDT 支付
       - 测试支付失败的错误处理

    5. 路由追踪
       - 验证支付历史中的路由信息
       - 验证每个节点的金额和通道信息
    """

    def test_base_send_payment_with_router(self):
        """
        b-c-d-私-a网络
        1. d-a建立了路由关系，查看构建的路由返回信息
        2. d call a ,route info: d-a channel outpoint
        """
        self.start_new_fiber(self.generate_account(10000))
        self.start_new_fiber(self.generate_account(10000))

        fiber1_balance = 1000 * 100000000
        fiber1_fee = 1000

        self.open_channel(self.fibers[1], self.fibers[2], 1000 * 100000000, 1)  # b-c
        self.open_channel(
            self.fibers[2], self.fibers[3], 1000 * 100000000, 1
        )  # c-d == b-c-d

        self.fibers[3].connect_peer(self.fibers[0])  # d-a
        time.sleep(1)
        self.fibers[3].get_client().open_channel(  # d -a private channel
            {
                "peer_id": self.fibers[0].get_peer_id(),
                "funding_amount": hex(fiber1_balance + 62 * 100000000),
                "tlc_fee_proportional_millionths": hex(fiber1_fee),
                "public": False,
            }
        )
        time.sleep(1)
        self.wait_for_channel_state(
            self.fibers[3].get_client(), self.fibers[0].get_peer_id(), "CHANNEL_READY"
        )
        # 查看d-a的channeloutpoint，预期能调用成功
        print(f"a peer_id:{self.fibers[0].get_peer_id()}")
        print(f"d peer_id:{self.fibers[3].get_peer_id()}")
        channels = (
            self.fibers[3]
            .get_client()
            .list_channels({"peer_id": self.fibers[0].get_peer_id()})
        )
        print(f"d-a,channel:{channels}")
        da_channel_outpoint = channels["channels"][0]["channel_outpoint"]
        print(f"d-a, channel_outpoint:{da_channel_outpoint}")

        router_hops = (
            self.fibers[3]
            .get_client()
            .build_router(
                {
                    "amount": hex(1 + 62 * 100000000),
                    "udt_type_script": None,
                    "hops_info": [
                        {
                            "pubkey": self.fibers[0]
                            .get_client()
                            .node_info()["node_id"],
                            "channel_outpoint": da_channel_outpoint,
                        },
                    ],
                    "final_tlc_expiry_delta": None,
                }
            )
        )
        print(f"router_hops:{router_hops}")
        hop = router_hops["router_hops"][0]
        print(f"hop:{hop}")
        assert hop["channel_outpoint"] == da_channel_outpoint
        assert hop["target"] == self.fibers[0].get_client().node_info()["node_id"]
        assert hop["amount_received"] == hex(1 + 62 * 100000000)

        # d call a ,route info: d-a channel outpoint
        payment = (
            self.fibers[3]
            .get_client()
            .send_payment_with_router(
                {
                    "payment_hash": None,
                    "invoice": None,
                    "keysend": True,
                    "custom_records": None,
                    "dry_run": False,
                    "udt_type_script": None,
                    "router": router_hops["router_hops"],
                }
            )
        )
        print(f"payment:{payment}")
        assert payment["status"] == "Created"
        self.wait_payment_state(self.fibers[3], payment["payment_hash"], "Success")

    # @pytest.mark.skip("https://github.com/nervosnetwork/fiber/issues/641")
    def test_auto_send_payment_with_router(self):
        """
        b-c-d-私-a网络
        1. d-a建立了路由关系，查看构建的路由返回信息
        2. b call a ,走route info: b-c-d-私-a网络（检查应该不支持自动拼接完整的路由）
        """
        self.start_new_fiber(self.generate_account(10000))
        self.start_new_fiber(self.generate_account(10000))

        fiber1_balance = 1000 * 100000000
        fiber1_fee = 1000

        self.open_channel(self.fibers[1], self.fibers[2], 1000 * 100000000, 1)  # b-c
        self.open_channel(
            self.fibers[2], self.fibers[3], 1000 * 100000000, 1
        )  # c-d == b-c-d

        self.fibers[3].connect_peer(self.fibers[0])  # d-a
        time.sleep(1)
        self.fibers[3].get_client().open_channel(  # d -a private channel
            {
                "peer_id": self.fibers[0].get_peer_id(),
                "funding_amount": hex(fiber1_balance + 62 * 100000000),
                "tlc_fee_proportional_millionths": hex(fiber1_fee),
                "public": False,
            }
        )
        time.sleep(1)
        self.wait_for_channel_state(
            self.fibers[3].get_client(), self.fibers[0].get_peer_id(), "CHANNEL_READY"
        )
        # 查看d-a的channeloutpoint，预期能调用成功
        print(f"a peer_id:{self.fibers[0].get_peer_id()}")
        print(f"d peer_id:{self.fibers[3].get_peer_id()}")
        channels = (
            self.fibers[3]
            .get_client()
            .list_channels({"peer_id": self.fibers[0].get_peer_id()})
        )
        print(f"d-a,channel:{channels}")
        da_channel_outpoint = channels["channels"][0]["channel_outpoint"]
        print(f"d-a, channel_outpoint:{da_channel_outpoint}")

        router_hops = (
            self.fibers[3]
            .get_client()
            .build_router(
                {
                    "amount": hex(1 + 62 * 100000000),
                    "udt_type_script": None,
                    "hops_info": [
                        {
                            "pubkey": self.fibers[0]
                            .get_client()
                            .node_info()["node_id"],
                            "channel_outpoint": da_channel_outpoint,
                        },
                    ],
                    "final_tlc_expiry_delta": None,
                }
            )
        )
        print(f"router_hops:{router_hops}")
        hop = router_hops["router_hops"][0]
        print(f"hop:{hop}")
        assert hop["channel_outpoint"] == da_channel_outpoint
        assert hop["target"] == self.fibers[0].get_client().node_info()["node_id"]
        assert hop["amount_received"] == hex(1 + 62 * 100000000)

        # b call a ,走route info: b-c-d-私-a网络（检查应该不支持自动拼接完整的路由）
        try:
            payment = (
                self.fibers[1]
                .get_client()
                .send_payment_with_router(
                    {
                        "payment_hash": None,
                        "invoice": None,
                        "keysend": True,
                        "custom_records": None,
                        "dry_run": False,
                        "udt_type_script": None,
                        "router": router_hops["router_hops"],
                    }
                )
            )
        except Exception as e:
            error_message = str(e)
            assert (
                "Error: Send payment first hop error: Failed to send onion packet with error UnknownNextPeer"
                in error_message
            ), f"预期错误信息不匹配，实际错误: {error_message}"

    def test_loop_send_payment_with_router(self):
        """
        b-c-d-私-a网络-b网络(网络成环状)
        1. b call b成环，走route info:b-c-d-a-b
        """
        self.start_new_fiber(self.generate_account(10000))
        self.start_new_fiber(self.generate_account(10000))

        fiber1_balance = 1000 * 100000000
        fiber1_fee = 1000
        self.open_channel(self.fibers[0], self.fibers[1], 1000 * 100000000, 1)  # a-b
        self.open_channel(self.fibers[1], self.fibers[2], 1000 * 100000000, 1)  # b-c
        self.open_channel(
            self.fibers[2], self.fibers[3], 1000 * 100000000, 1
        )  # c-d == b-c-d

        self.fibers[3].connect_peer(self.fibers[0])  # d-a
        time.sleep(1)
        self.fibers[3].get_client().open_channel(  # d -a private channel
            {
                "peer_id": self.fibers[0].get_peer_id(),
                "funding_amount": hex(fiber1_balance + 62 * 100000000),
                "tlc_fee_proportional_millionths": hex(fiber1_fee),
                "public": False,
            }
        )
        time.sleep(1)
        self.wait_for_channel_state(
            self.fibers[3].get_client(), self.fibers[0].get_peer_id(), "CHANNEL_READY"
        )

        bc_channel_outpoint = self.get_channel_outpoint(self.fibers[1], self.fibers[2])
        print(f"b-c, channel_outpoint:{bc_channel_outpoint}")
        cd_channel_outpoint = self.get_channel_outpoint(self.fibers[2], self.fibers[3])
        print(f"c-d, channel_outpoint:{cd_channel_outpoint}")
        da_channel_outpoint = self.get_channel_outpoint(self.fibers[3], self.fibers[0])
        print(f"d-a, channel_outpoint:{da_channel_outpoint}")
        ab_channel_outpoint = self.get_channel_outpoint(self.fibers[0], self.fibers[1])
        print(f"a-b, channel_outpoint:{ab_channel_outpoint}")

        bc_router_hops = (
            self.fibers[1]
            .get_client()
            .build_router(
                {
                    "amount": hex(4 * (1 + 62 * 100000000)),
                    "udt_type_script": None,
                    "hops_info": [
                        {
                            "pubkey": self.fibers[2]
                            .get_client()
                            .node_info()["node_id"],
                            "channel_outpoint": bc_channel_outpoint,
                        },
                    ],
                    "final_tlc_expiry_delta": None,
                }
            )
        )

        cd_router_hops = (
            self.fibers[2]
            .get_client()
            .build_router(
                {
                    "amount": hex(3 * (1 + 62 * 100000000)),
                    "udt_type_script": None,
                    "hops_info": [
                        {
                            "pubkey": self.fibers[3]
                            .get_client()
                            .node_info()["node_id"],
                            "channel_outpoint": cd_channel_outpoint,
                        },
                    ],
                    "final_tlc_expiry_delta": None,
                }
            )
        )

        da_router_hops = (
            self.fibers[3]
            .get_client()
            .build_router(
                {
                    "amount": hex(2 * (1 + 62 * 100000000)),
                    "udt_type_script": None,
                    "hops_info": [
                        {
                            "pubkey": self.fibers[0]
                            .get_client()
                            .node_info()["node_id"],
                            "channel_outpoint": da_channel_outpoint,
                        },
                    ],
                    "final_tlc_expiry_delta": None,
                }
            )
        )

        ab_router_hops = (
            self.fibers[0]
            .get_client()
            .build_router(
                {
                    "amount": hex(1 * (1 + 62 * 100000000)),
                    "udt_type_script": None,
                    "hops_info": [
                        {
                            "pubkey": self.fibers[1]
                            .get_client()
                            .node_info()["node_id"],
                            "channel_outpoint": ab_channel_outpoint,
                        },
                    ],
                    "final_tlc_expiry_delta": None,
                }
            )
        )

        # 获取各个路由跳的基本信息
        bc_hop = bc_router_hops["router_hops"][0]
        cd_hop = cd_router_hops["router_hops"][0]
        da_hop = da_router_hops["router_hops"][0]
        ab_hop = ab_router_hops["router_hops"][0]

        # 修改每一跳的 incoming_tlc_expiry 值，依次增加 172800000
        base_expiry = 86400000  # 基础过期时间
        delta = 172800000  # 每一跳增加的差值

        # 从最后一跳开始，依次增加过期时间
        ab_hop["incoming_tlc_expiry"] = hex(base_expiry)  # 0x5265c00
        da_hop["incoming_tlc_expiry"] = hex(base_expiry + delta)  # 0xa4cb800
        cd_hop["incoming_tlc_expiry"] = hex(base_expiry + 2 * delta)  # 0xf731400
        bc_hop["incoming_tlc_expiry"] = hex(base_expiry + 3 * delta)  # 0x14997000

        # b call b ,route info:b-c，c-d，d-a，a-b的route
        payment = (
            self.fibers[1]
            .get_client()
            .send_payment_with_router(
                {
                    "payment_hash": None,
                    "invoice": None,
                    "keysend": True,
                    "custom_records": None,
                    "dry_run": False,
                    "udt_type_script": None,
                    "router": [
                        bc_hop,
                        cd_hop,
                        da_hop,
                        ab_hop,
                    ],
                }
            )
        )

        print(f"payment:{payment}")
        assert payment["status"] == "Created"
        self.wait_payment_state(self.fibers[1], payment["payment_hash"], "Success")

    def get_channel_outpoint(self, from_fiber, to_fiber):
        """
        获取两个节点之间的通道outpoint
        Args:
            from_fiber: 起始节点
            to_fiber: 目标节点

        Returns:
            channel_outpoint: 通道outpoint
        """
        channels = from_fiber.get_client().list_channels(
            {"peer_id": to_fiber.get_peer_id()}
        )
        print(f"{from_fiber.get_peer_id()}-{to_fiber.get_peer_id()},channel:{channels}")
        channel_outpoint = channels["channels"][0]["channel_outpoint"]
        print(
            f"{from_fiber.get_peer_id()}-{to_fiber.get_peer_id()}, channel_outpoint:{channel_outpoint}"
        )
        return channel_outpoint
