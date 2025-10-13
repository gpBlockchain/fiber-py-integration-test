from framework.basic_fiber import FiberTest


class TestSendPaymentWithUpdateChannel(FiberTest):

    def test_01(self):
        """
        Test sending payments with updating channel.
        Returns:
            None
        """
        # Start new fibers with initial accounts
        self.start_new_fiber(self.generate_account(10000))
        self.start_new_fiber(self.generate_account(10000))

        # Open channels between fibers
        self.open_channel(
            self.fibers[0], self.fibers[1], 1000 * 100000000, 1000 * 100000000
        )
        self.open_channel(
            self.fibers[1], self.fibers[2], 1000 * 100000000, 1000 * 100000000
        )
        self.open_channel(
            self.fibers[2], self.fibers[3], 1000 * 100000000, 1000 * 100000000
        )

        payment_hashes = []

        for j in range(3):
            # Send initial payments
            for i in range(30):
                try:
                    payment_hash = self.send_payment(
                        self.fibers[0], self.fibers[3], 100000000, False, None, 0
                    )
                    payment_hashes.append(payment_hash)
                except:
                    pass

            # Get channel ID and update channel
            N3N4_CHANNEL_ID = (
                self.fibers[3]
                .get_client()
                .list_channels({})["channels"][0]["channel_id"]
            )
            channels = (
                self.fibers[1]
                .get_client()
                .list_channels({"peer_id": self.fibers[2].get_peer_id()})
            )
            self.fibers[1].get_client().update_channel(
                {
                    "channel_id": channels["channels"][0]["channel_id"],
                    "tlc_fee_proportional_millionths": hex(10000 + (1 + j) * 10000),
                }
            )

            # Send additional payments
            for i in range(30):
                try:
                    payment_hash = self.send_payment(
                        self.fibers[0], self.fibers[3], 100000000, False, None, 0
                    )
                    payment_hashes.append(payment_hash)
                except:
                    pass

            payment_results = []

            # Get payment results
            for payment_hash in payment_hashes:
                payment = (
                    self.fibers[0]
                    .get_client()
                    .get_payment({"payment_hash": payment_hash})
                )
                print("payment status:", payment["status"])
                payment_results.append(payment)

            # Print payment results
            idx = 0
            for payment in payment_results:
                print(
                    f'idx:{idx} status:{payment["status"]}, fee:{int(payment["fee"], 16)},hash:{payment["payment_hash"]}'
                )
                idx += 1
                self.wait_payment_finished(
                    self.fibers[0], payment["payment_hash"], 1200
                )

            # Check transaction success
            self.send_payment(self.fibers[0], self.fibers[3], 1)
