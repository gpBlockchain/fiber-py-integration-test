import pytest

from framework.basic_fiber import FiberTest
from framework.test_fiber import FiberConfigPath


class TestP2p(FiberTest):
    # debug = True
    # @pytest.mark.skip("todo")
    def test_old_fiber(self):
        """New nodes require ONCHAIN_FULL_PAYMENT_HASH; new<->V091 opens are rejected.

        After fiber #1656 admission, CURRENT_DEV cannot open or accept a Legacy
        channel with a V091 peer. Assert that rejection, then still exercise a
        new-new channel path so the suite keeps a positive p2p payment check.
        """
        old_fiber = self.start_new_fiber(
            self.generate_account(10000), fiber_version=FiberConfigPath.V091_DEV
        )
        with pytest.raises(Exception) as raised:
            self.open_channel(
                self.fiber1, old_fiber, 1000 * 100000000, 1000 * 100000000
            )
        message = str(raised.value)
        assert "ONCHAIN_FULL_PAYMENT_HASH is required" in message, message

        # Positive control: same-version V1 channels still pay end-to-end.
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 1000 * 100000000)
        self.send_payment(self.fiber1, self.fiber2, 1)
        self.send_payment(self.fiber2, self.fiber1, 1)

        channel_id = self.fiber1.get_client().list_channels(
            {"pubkey": self.fiber2.get_pubkey()}
        )["channels"][0]["channel_id"]
        self.fiber1.get_client().shutdown_channel({"channel_id": channel_id})
        self.wait_for_channel_state(
            self.fiber1.get_client(),
            self.fiber2.get_pubkey(),
            "Closed",
            include_closed=True,
            channel_id=channel_id,
        )
