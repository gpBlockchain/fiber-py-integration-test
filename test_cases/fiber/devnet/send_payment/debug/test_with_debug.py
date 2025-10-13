import time

from framework.basic_fiber import FiberTest
from framework.test_fiber import FiberConfigPath


class TestWithDebug(FiberTest):
    fiber_version = FiberConfigPath.CURRENT_DEV_DEBUG

    # @pytest.mark.skip("https://github.com/nervosnetwork/fiber/issues/620")
    def test_not_hophit_issue620(self):
        """
        a-私-b-c-d-私-a
        1. a->b
        2. a->c
        3. a->d
        4. a->a(不支持，不能自己转给自己)
        5. 路径选择
            5.1. b-a，预期是能成功(大概率走这个)
            5.2. 如果走的是b-c-d-a，则需要走hint才可以成功，预期是失败
        Returns:

        """
        self.start_new_fiber(self.generate_account(10000))  # c
        self.start_new_fiber(self.generate_account(10000))  # d

        fiber1_balance = 1000 * 100000000
        fiber1_fee = 1000
        self.fiber1.get_client().open_channel(
            {
                "peer_id": self.fiber2.get_peer_id(),
                "funding_amount": hex(fiber1_balance + 62 * 100000000),
                "tlc_fee_proportional_millionths": hex(fiber1_fee),
                "public": False,
            }
        )  # a-b private channel
        time.sleep(1)
        self.wait_for_channel_state(
            self.fiber1.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY"
        )
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

        for i in range(1, len(self.fibers)):  # b,c,d
            self.send_payment(
                self.fibers[0], self.fibers[i], 1 * 100000000
            )  # a->b,a->c,a>d
        print(f"debug:a-a,route")
        # a->a是不支持的，除非a-私-b-c-d-私-a这么走一圈，异常捕捉在方法里面
        self.send_payment(self.fibers[0], self.fibers[0], 1 * 100000000)

        # b-a(不通过hophit应该发送失败)
        try:
            payment = (
                self.fibers[1]
                .get_client()
                .send_payment(  # b
                    {
                        "target_pubkey": self.fibers[0]
                        .get_client()
                        .node_info()["node_id"],
                        "amount": hex(1 * 100000000),
                        "keysend": True,
                    }
                )
            )
            print(f"debug payment content:{payment}")

            channels = (
                self.fibers[1]
                .get_client()
                .list_channels({"peer_id": self.fibers[0].get_peer_id()})
            )
            print(f"b-a,channel:{channels}")
            ba_channel_outpoint = channels["channels"][0]["channel_outpoint"]
            print(f"b-a, channel_outpoint:{ba_channel_outpoint}")
            assert (
                payment["routers"][0]["nodes"][0]["channel_outpoint"]
                == ba_channel_outpoint
            )
        except Exception as e:
            # 如果走的是b-c-d-a，不通过hophit应该发送失败
            error_message = str(e)
            assert (
                error_message
                == "Error: Send payment error: Failed to build route, PathFind error: no path found"
            ), f"Unexpected error message: {error_message}"
