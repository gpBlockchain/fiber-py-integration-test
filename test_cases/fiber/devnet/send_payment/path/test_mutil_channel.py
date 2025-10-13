from framework.basic_fiber import FiberTest


class TestMutilChannel(FiberTest):

    def test_mutil_channel(self):
        """
        1. 节点1 和节点2之间建立多个channel
        2. 节点1 疯狂给节点2发交易
             可以用到多个channel
        Returns:
        """
        open_channel_size = 5
        send_payment_size = 20
        for i in range(open_channel_size):
            self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1)

        payment_list = []
        before_channels = self.fiber1.get_client().list_channels({})

        for i in range(send_payment_size):
            payment_list.append(
                self.send_payment(self.fiber1, self.fiber2, 1 * 100000000, False)
            )

        for payment_hash in payment_list:
            self.wait_payment_finished(self.fiber1, payment_hash, 120)

        channels = self.fiber1.get_client().list_channels({})
        for i in range(len(before_channels["channels"])):
            print(before_channels["channels"][i])
        used_channel = 0
        for i in range(len(channels["channels"])):
            print(channels["channels"][i])
            if (
                channels["channels"][i]["remote_balance"]
                > before_channels["channels"][i]["remote_balance"]
            ):
                used_channel += 1
        assert used_channel > 1
