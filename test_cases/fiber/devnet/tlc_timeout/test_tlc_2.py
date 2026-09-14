"""Integration regressions for Fiber PR #1618 (issues #1611 and #1612).

Both cases use the same route and held payment::

    fiber1 (A) -> fiber2 (B) -> fiber3 (C) -> fiber4 (D)

The B-C channel is force-closed while the payment is pending.  The tests
separate the two orderings fixed by the PR instead of relying on the original
race between the close confirmation and C's ``RemoveTlc``.
"""

import math
import time

from framework.basic_fiber import COMMIT_LOCK_CODE_HASH, FiberTest
from framework.util import change_time

CKB = 100000000
CHANNEL_BALANCE = 1000 * CKB
PAYMENT_AMOUNT = 1 * CKB
COMMITMENT_DELAY_SECONDS = 4 * 60 * 60
XUDT_COMPATIBLE_WITNESS = bytes([16, 0, 0, 0] * 4)


class TestPR1618OnchainTlcReconcile(FiberTest):
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 5}

    @classmethod
    def teardown_class(cls):
        cls.restore_time()
        super().teardown_class()

    def _channel(self, fiber, channel_id):
        channels = fiber.get_client().list_channels({"include_closed": True})[
            "channels"
        ]
        return next(
            (channel for channel in channels if channel["channel_id"] == channel_id),
            None,
        )

    def _payment_tlc(self, fiber, channel_id, payment_hash):
        channel = self._channel(fiber, channel_id)
        if channel is None:
            return None
        return next(
            (
                tlc
                for tlc in channel.get("pending_tlcs", [])
                if tlc["payment_hash"] == payment_hash
            ),
            None,
        )

    def _wait_for_tlc_status(
        self, fiber, channel_id, payment_hash, expected_status, timeout=120
    ):
        tlc = None
        for _ in range(timeout):
            tlc = self._payment_tlc(fiber, channel_id, payment_hash)
            if tlc is not None and tlc["status"] == expected_status:
                return tlc
            time.sleep(1)
        assert False, (
            f"TLC {payment_hash} on {channel_id} did not reach "
            f"{expected_status}; last TLC={tlc}"
        )

    def _wait_for_offered_balance_zero(self, fiber, channel_id, timeout=240):
        channel = None
        for _ in range(timeout):
            channel = self._channel(fiber, channel_id)
            if channel is not None and int(channel["offered_tlc_balance"], 16) == 0:
                return channel
            time.sleep(1)
        assert False, (
            f"offered TLC balance on {channel_id} did not reach zero "
            f"within {timeout}s: {channel}"
        )

    def _advance_clock_to(self, unix_time_ms):
        seconds = unix_time_ms / 1000 - time.time()
        minutes = max(1, math.ceil(seconds / 60))
        hours, minutes = divmod(minutes, 60)
        change_time(hours, minutes)

    def _advance_to_receiver_remove(self, receiver_tlc):
        # HoldTlcTimeout fires before absolute expiry.  This is the same
        # threshold used by the existing tlc_timeout integration coverage.
        remove_lead_seconds = 60 + 2 * COMMITMENT_DELAY_SECONDS / 3
        remove_time_ms = int(receiver_tlc["expiry"], 16) - int(
            remove_lead_seconds * 1000
        )
        self._advance_clock_to(remove_time_ms)

    def _advance_past_tlc_expiry_and_delay(self, tlc):
        # Cross the exact B-C TLC expiry, then advance one CKB epoch so the
        # force-close commitment output is spendable by the watchtower.
        self._advance_clock_to(int(tlc["expiry"], 16) + 60 * 1000)
        self.node.getClient().generate_epochs("0x1", 0)

    def _wait_for_spending_tx(self, tx_hash, timeout=240):
        spending_tx = None
        for _ in range(timeout):
            spending_tx, _ = self.get_ln_cell_death_hash(tx_hash)
            if spending_tx is not None:
                return spending_tx
            time.sleep(1)
        assert False, f"output 0 of {tx_hash} was not spent within {timeout}s"

    def _settlement_unlock_types(self, tx_hash):
        tx = self.node.getClient().get_transaction(tx_hash)["transaction"]
        return self._decode_settlement_unlock_types(tx["witnesses"][0], tx_hash)

    @staticmethod
    def _decode_settlement_unlock_types(witness_hex, tx_hash="fixture"):
        witness = bytes.fromhex(witness_hex.removeprefix("0x"))
        assert witness.startswith(XUDT_COMPATIBLE_WITNESS), (
            f"settlement witness in {tx_hash} is missing the 16-byte "
            "xUDT-compatible prefix"
        )
        settlement = witness[len(XUDT_COMPATIBLE_WITNESS) :]
        assert settlement and settlement[0] == 1, (
            f"unexpected settlement witness type in {tx_hash}: "
            f"{settlement[0] if settlement else None}"
        )

        pending_tlc_count = settlement[1]
        cursor = 74 + 85 * pending_tlc_count
        unlock_types = []
        while cursor < len(settlement):
            assert cursor + 67 <= len(
                settlement
            ), f"truncated settlement unlock in {tx_hash} at byte {cursor}"
            unlock_types.append(settlement[cursor])
            with_preimage = settlement[cursor + 1] == 1
            cursor += 99 if with_preimage else 67

        assert cursor == len(
            settlement
        ), f"invalid settlement witness length in {tx_hash}"
        return unlock_types

    def _setup_held_payment(self):
        fiber3 = self.start_new_fiber(self.generate_account(10000))
        fiber4 = self.start_new_fiber(self.generate_account(10000))

        a_b_channel_id = self.open_channel(self.fiber1, self.fiber2, CHANNEL_BALANCE, 0)
        b_c_channel_id = self.open_channel(self.fiber2, fiber3, CHANNEL_BALANCE, 0)
        c_d_channel_id = self.open_channel(fiber3, fiber4, CHANNEL_BALANCE, 0)
        self.wait_graph_channels_sync(self.fiber1, 3, timeout=120)

        invoice = fiber4.get_client().new_invoice(
            {
                "amount": hex(PAYMENT_AMOUNT),
                "currency": "Fibd",
                "description": "PR-1618 held payment",
                "payment_hash": self.generate_random_preimage(),
            }
        )
        payment = self.fiber1.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "max_fee_rate": hex(1000000000000000),
            }
        )
        payment_hash = payment["payment_hash"]
        self.wait_invoice_state(fiber4, payment_hash, "Received", timeout=120)

        a_b_tlc = self._wait_for_tlc_status(
            self.fiber1,
            a_b_channel_id,
            payment_hash,
            {"Outbound": "Committed"},
        )
        b_c_tlc = self._wait_for_tlc_status(
            self.fiber2,
            b_c_channel_id,
            payment_hash,
            {"Outbound": "Committed"},
        )
        receiver_tlc = self._wait_for_tlc_status(
            fiber4,
            c_d_channel_id,
            payment_hash,
            {"Inbound": "Committed"},
        )
        return {
            "fiber3": fiber3,
            "fiber4": fiber4,
            "a_b_channel_id": a_b_channel_id,
            "b_c_channel_id": b_c_channel_id,
            "c_d_channel_id": c_d_channel_id,
            "payment_hash": payment_hash,
            "a_b_tlc": a_b_tlc,
            "b_c_tlc": b_c_tlc,
            "receiver_tlc": receiver_tlc,
        }

    def _force_close_b_c(self, case):
        self.fiber2.get_client().shutdown_channel(
            {"channel_id": case["b_c_channel_id"], "force": True}
        )
        close_tx = self.wait_and_check_tx_pool_fee(1000, False)
        self.wait_for_channel_state(
            self.fiber2.get_client(),
            case["fiber3"].get_pubkey(),
            "ShuttingDown",
            timeout=60,
            include_closed=True,
            channel_id=case["b_c_channel_id"],
        )
        return close_tx

    def _assert_upstream_failed(self, case):
        self._wait_for_offered_balance_zero(
            self.fiber1,
            case["a_b_channel_id"],
        )
        result = self.wait_payment_finished(
            self.fiber1, case["payment_hash"], timeout=120
        )
        assert result["status"] == "Failed", result
        a_b_channel = self._channel(self.fiber1, case["a_b_channel_id"])
        assert a_b_channel["state"]["state_name"] == "ChannelReady", a_b_channel

    def test_terminal_final_party_sweep_reconciles_upstream_tlc(self):
        """#1611: terminal 0xff -> 0xfe sweeps must record exact TLC settlement."""
        case = self._setup_held_payment()
        close_tx = self._force_close_b_c(case)

        # Confirm B-C before C removes its TLC.  The peer RemoveTlc is then
        # dropped for the closed actor, leaving B's downstream TLC committed.
        self.Miner.miner_until_tx_committed(self.node, close_tx)
        self.wait_for_channel_state(
            self.fiber2.get_client(),
            case["fiber3"].get_pubkey(),
            "Closed",
            timeout=120,
            include_closed=True,
            channel_id=case["b_c_channel_id"],
        )
        self._advance_to_receiver_remove(case["receiver_tlc"])
        self._wait_for_offered_balance_zero(
            case["fiber3"],
            case["c_d_channel_id"],
        )
        committed_tlc = self._payment_tlc(
            self.fiber2, case["b_c_channel_id"], case["payment_hash"]
        )
        assert committed_tlc["status"] == {"Outbound": "Committed"}, committed_tlc

        # Stop B so C deterministically commits the intermediate 0xff sweep
        # first.  After B restarts it must consume the successor commitment
        # cell with terminal 0xfe, the branch fixed by PR #1618.
        self.fiber2.stop()
        self._advance_past_tlc_expiry_and_delay(case["b_c_tlc"])
        c_final_party_tx = self._wait_for_spending_tx(close_tx)
        assert self._settlement_unlock_types(c_final_party_tx) == [0xFF]

        self.fiber2.start(fnn_log_level=self.fnn_log_level)
        self.fiber1.connect_peer(self.fiber2)
        b_terminal_tx = self._wait_for_spending_tx(c_final_party_tx)
        assert self._settlement_unlock_types(b_terminal_tx) == [0xFE]

        terminal_tx = self.node.getClient().get_transaction(b_terminal_tx)[
            "transaction"
        ]
        assert (
            terminal_tx["outputs"][0]["lock"]["code_hash"] != COMMIT_LOCK_CODE_HASH
        ), "terminal 0xfe sweep unexpectedly created a successor commitment cell"

        self._assert_upstream_failed(case)

    def test_uncommitted_remote_removed_reconciles_upstream_tlc(self):
        """#1612: RemoteRemoved without remove confirmation must reconcile on-chain."""
        case = self._setup_held_payment()
        close_tx = self._force_close_b_c(case)

        # Keep the close unconfirmed while D times out.  B receives C's
        # RemoveTlc and marks the TLC RemoteRemoved, but ShuttingDown rejects
        # the following commitment handshake, so A-B is still unresolved.
        self._advance_to_receiver_remove(case["receiver_tlc"])
        remote_removed = self._wait_for_tlc_status(
            self.fiber2,
            case["b_c_channel_id"],
            case["payment_hash"],
            {"Outbound": "RemoteRemoved"},
        )
        time.sleep(2)
        b_c_channel = self._channel(self.fiber2, case["b_c_channel_id"])
        assert b_c_channel["state"]["state_name"] == "ShuttingDown", b_c_channel
        assert self._payment_tlc(
            self.fiber1, case["a_b_channel_id"], case["payment_hash"]
        ), "upstream TLC disappeared before the B-C remove handshake was rejected"
        close_status = self.node.getClient().get_transaction(close_tx)["tx_status"][
            "status"
        ]
        assert close_status in ("pending", "proposed"), close_status
        assert remote_removed["status"] == {"Outbound": "RemoteRemoved"}

        self.Miner.miner_until_tx_committed(self.node, close_tx)
        self._advance_past_tlc_expiry_and_delay(case["b_c_tlc"])
        self._wait_for_spending_tx(close_tx)
        self._assert_upstream_failed(case)
