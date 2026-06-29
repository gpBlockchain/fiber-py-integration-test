"""Regression coverage for amount and pagination safety fixes.

Covered PRs:
* #1363: amount overflow checks on RPC-facing values.
* #1398: clamp unbounded ``list_payments.limit``.
"""

import pytest

from framework.basic_fiber import FiberTest

U128_MAX = (1 << 128) - 1
U64_MAX = (1 << 64) - 1


class TestPaymentRpcLimits(FiberTest):
    __test__ = True

    def test_list_payments_huge_limit_is_clamped_and_paginates(self):
        self.open_channel(self.fiber1, self.fiber2, 300 * 100000000, 0)

        sent_hashes = []
        for _ in range(20):
            sent_hashes.append(self.send_payment(self.fiber1, self.fiber2, 1))

        result = self.fiber1.get_client().list_payments({"limit": hex(U64_MAX)})
        assert len(result["payments"]) <= 500
        assert len(result["payments"]) >= len(sent_hashes)
        assert result["last_cursor"] == result["payments"][-1]["payment_hash"]

        next_page = self.fiber1.get_client().list_payments(
            {"limit": hex(U64_MAX), "after": result["last_cursor"]}
        )
        assert len(next_page["payments"]) <= 500

    def test_send_payment_rejects_amount_plus_fee_overflow(self):
        self.open_channel(self.fiber1, self.fiber2, 300 * 100000000, 0)

        with pytest.raises(Exception):
            self.fiber1.get_client().send_payment(
                {
                    "target_pubkey": self.fiber2.get_pubkey(),
                    "amount": hex(U128_MAX),
                    "keysend": True,
                    "max_fee_amount": hex(1),
                    "timeout": hex(3),
                }
            )

    def test_open_channel_rejects_amount_over_u64_for_ckb(self):
        with pytest.raises(Exception):
            self.fiber1.get_client().open_channel(
                {
                    "pubkey": self.fiber2.get_pubkey(),
                    "funding_amount": hex(1 << 64),
                    "public": True,
                }
            )
