import time

import pytest

from framework.basic_fiber import FiberTest


class Test484(FiberTest):
    # FiberTest.debug = True

    # @pytest.mark.skip("https://github.com/nervosnetwork/fiber/pull/484")
    def test_484(self):
        self.open_channel(
            self.fiber1, self.fiber2, 1000 * 100000000, 1000 * 100000000, 1000, 1000
        )
        payment1 = self.send_payment(self.fiber1, self.fiber2, 600 * 100000000, False)
        try:

            payment2 = self.send_payment(
                self.fiber1, self.fiber2, 600 * 100000000, False, try_count=0
            )
        except Exception as e:
            print(f"Expected failure: {e}")
            payment2 = None
        payment3 = self.send_payment(self.fiber1, self.fiber2, 300 * 100000000, False)
        self.wait_payment_state(self.fiber1, payment1, "Success")
        if payment2 != None:
            self.wait_payment_state(self.fiber1, payment2, "Failed")
        self.wait_payment_state(self.fiber1, payment3, "Success")
        self.send_payment(self.fiber2, self.fiber1, 300 * 100000000)
