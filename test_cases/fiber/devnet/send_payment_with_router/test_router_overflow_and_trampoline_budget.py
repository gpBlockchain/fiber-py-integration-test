"""Regression coverage for router fee and overflow validation.

Covered PRs:
* #1392: trampoline routing must deduct the outer route fee from the user's
  max-fee budget before assigning inner trampoline budget.
* #1409: explicit router validation should reject obvious amount overflows
  before dispatching TLCs.
"""

import pytest

from framework.basic_fiber import FiberTest

U128_MAX = (1 << 128) - 1


class TestRouterOverflowAndTrampolineBudget(FiberTest):
    __test__ = True

    def _channel_outpoint(self, fiber, peer):
        channels = fiber.get_client().list_channels({"pubkey": peer.get_pubkey()})[
            "channels"
        ]
        assert channels
        return channels[0]["channel_outpoint"]

    def test_explicit_router_rejects_overflow_amount_before_tlc_dispatch(self):
        self.open_channel(self.fiber1, self.fiber2, 300 * 100000000, 0)

        router = self.fiber1.get_client().build_router(
            {
                "amount": hex(1),
                "udt_type_script": None,
                "hops_info": [
                    {
                        "pubkey": self.fiber2.get_pubkey(),
                        "channel_outpoint": self._channel_outpoint(
                            self.fiber1, self.fiber2
                        ),
                    }
                ],
                "final_tlc_expiry_delta": None,
            }
        )["router_hops"]
        router[0]["amount_received"] = hex(U128_MAX)

        with pytest.raises(Exception):
            self.fiber1.get_client().send_payment_with_router(
                {
                    "payment_hash": None,
                    "invoice": None,
                    "keysend": True,
                    "custom_records": None,
                    "dry_run": True,
                    "udt_type_script": None,
                    "router": router,
                }
            )

        assert self.fiber1.get_client().list_payments({})["payments"] == []

    def test_trampoline_budget_includes_outer_route_fee(self):
        fiber3 = self.start_new_fiber(self.generate_account(10000))
        self.fiber1.connect_peer(fiber3)
        fiber3.connect_peer(self.fiber2)

        self.open_channel(
            self.fiber1,
            fiber3,
            500 * 100000000,
            0,
            fiber1_fee=900000,
            fiber2_fee=900000,
        )
        self.open_channel(
            fiber3,
            self.fiber2,
            500 * 100000000,
            0,
            fiber1_fee=900000,
            fiber2_fee=900000,
        )

        amount = 10 * 100000000
        dry_run = self.fiber1.get_client().send_payment(
            {
                "target_pubkey": self.fiber2.get_pubkey(),
                "amount": hex(amount),
                "keysend": True,
                "trampoline_hops": [fiber3.get_pubkey()],
                "max_fee_amount": hex(100 * 100000000),
                "dry_run": True,
            }
        )
        required_fee = int(dry_run["fee"], 16)
        assert required_fee > 0

        with pytest.raises(Exception) as exc_info:
            self.fiber1.get_client().send_payment(
                {
                    "target_pubkey": self.fiber2.get_pubkey(),
                    "amount": hex(amount),
                    "keysend": True,
                    "trampoline_hops": [fiber3.get_pubkey()],
                    "max_fee_amount": hex(1),
                    "timeout": hex(3),
                }
            )
        assert "max_fee_amount is too low" in str(exc_info.value)
