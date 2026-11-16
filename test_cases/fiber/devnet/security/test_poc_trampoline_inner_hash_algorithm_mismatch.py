"""Regression for FBR-2026-0060 / PR #1673: trampoline hash-algorithm binding.

Before PR #1673, ``forward_trampoline_packet`` (fiber
``crates/fiber-lib/src/fiber/network.rs``) read ``hash_algorithm`` from the inner
trampoline onion payload and started the downstream payment with it, without
comparing it to the hash algorithm of the upstream TLC it was forwarding. The
channel layer only binds the *outer* onion to the AddTlc
(``add_tlc.hash_algorithm != peeled.current.hash_algorithm``); nothing bound the
inner trampoline payload to either of them.

That let a malicious sender split one ``payment_hash`` across two algorithms:

    A(sender, attack fnn) -> B(trampoline, current fnn) -> C(recipient, current)

A locks the A->B TLC under the session default ``ckb_hash`` while the crafted
inner payload asks B to forward the same hash under ``sha256``. C's ``sha256``
invoice matches, C settles and reveals its preimage, and B pays C from its own
balance - but that preimage never satisfies the ``ckb_hash`` lock on the A->B
TLC, so B can never collect from A and eats the loss when A is refunded at
expiry.

PR #1673 rejects such an inner payload with ``InvalidOnionPayload`` before
building ``SendPaymentData`` or starting the downstream payment actor. This file
pins both sides of the new guard:

* ``PR1673-01``: the mismatched inner algorithm is rejected at B; the failure is
  relayed to A, the upstream TLC unwinds, C is never paid, B starts no
  downstream payment, and no balance moves.
* ``PR1673-02``: a matching ``CkbHash`` trampoline payment still completes end
  to end, with both hop TLCs settling and exact fee accounting.

A needs the p2p-tap build's ``FIBER_TEST_TRAMPOLINE_INNER_HASH_ALGORITHM`` hook
to craft the mismatched inner payload - stock RPCs cannot express it (see
``framework/attack_fnn.py``). Without the hook the inner payload follows the
session algorithm and the mismatch scenario cannot be built at all.

Observation limits
------------------

* B's boundary call and the trampoline error code are not exposed over RPC, so
  the rejection is observed through the exact log line the new guard writes
  before returning (``inner hash_algorithm Sha256 does not match upstream TLC
  hash_algorithm CkbHash``) together with the absence of any downstream payment
  session or TLC. A wording change of that line breaks ``PR1673-01`` and must be
  re-checked against the product.
* A's own ``failed_error`` does **not** carry ``InvalidOnionPayload``: after the
  reject the sender rebuilds the route with ``remain_fee_amount() == 0`` (the
  failed attempt already consumed the whole fee budget, itself capped by
  ``DEFAULT_MAX_FEE_RATE``) and reports that route-build error instead. The
  regression therefore asserts the failure and its observable consequences,
  not that string.
* ``list_channels`` exposes neither a TLC ``hash_algorithm`` nor a removal
  reason, so the ``CkbHash`` premise of the upstream TLC and the Fulfill nature
  of the removal are inferred from the hook (which rewrites only
  ``hash_algorithm``), the invoice attributes, the two ``Success`` payment
  sessions, the ``Paid`` invoice and the exact balance movement.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from framework.attack_fnn import TRAMPOLINE_INNER_SHA256_ENV
from framework.basic_p2p import P2pFiberTest
from framework.util import ckb_hash

CKB = 100000000
PAYMENT_AMOUNT = 1 * CKB
CHANNEL_FUND = 200 * CKB
# Same generous budget the honest trampoline tests use; B only needs the direct
# B->C hop, so almost all of it stays unspent.
TRAMPOLINE_FEE_BUDGET = 20000000
# Bounded window that proves the rejected hash never produces downstream effects.
NO_DOWNSTREAM_SECONDS = 10

# The new guard's rendered log line for the rejected CkbHash/Sha256 pair. The
# node log is the only direct observation of the boundary rejection: no RPC
# exposes the trampoline error code or the TLC removal reason.
GUARD_LOG_MARKER = (
    "inner hash_algorithm Sha256 does not match upstream TLC hash_algorithm CkbHash"
)

MISMATCH_TEST = "test_mismatched_inner_hash_algorithm_is_rejected"


def sha256_hex(preimage_hex):
    raw = bytes.fromhex(preimage_hex.replace("0x", ""))
    return "0x" + hashlib.sha256(raw).digest().hex()


def hex_int(value):
    return int(value, 16) if isinstance(value, str) else int(value)


class TestPocTrampolineInnerHashAlgorithmMismatch(P2pFiberTest):
    auto_open_channel = False

    def setup_method(self, method):
        # Only the rejection scenario needs a sender whose inner trampoline
        # payload is desynchronized from its payment session; the matching
        # control must run the stock behaviour.
        self.attacker_env = (
            TRAMPOLINE_INNER_SHA256_ENV if method.__name__ == MISMATCH_TEST else None
        )
        super().setup_method(method)

    # ---------------------------------------------------------------- helpers

    def _build_topology(self):
        """A --up--> B(trampoline) --down--> C, both channels public and ready."""
        alice = self.attacker
        router = self.victim
        carol = self.start_new_fiber(self.generate_account(1000))
        ch_up = self.open_channel(alice, router, CHANNEL_FUND, 0)
        ch_down = self.open_channel(router, carol, CHANNEL_FUND, 0)
        time.sleep(3)
        # A must find the first trampoline hop, B must find C.
        self.wait_graph_channels_sync(alice, 2, timeout=90)
        self.wait_graph_channels_sync(router, 2, timeout=90)
        return alice, router, carol, ch_up, ch_down

    def _channel(self, fiber, channel_id):
        channels = fiber.get_client().list_channels({"include_closed": True})[
            "channels"
        ]
        for channel in channels:
            if channel["channel_id"] == channel_id:
                return channel
        raise AssertionError(f"channel {channel_id} not found on {fiber.tmp_path}")

    def _tlcs(self, fiber, channel_id, payment_hash):
        return [
            tlc
            for tlc in (self._channel(fiber, channel_id).get("pending_tlcs") or [])
            if tlc.get("payment_hash") == payment_hash
        ]

    def _single_tlc(self, fiber, channel_id, payment_hash, side):
        tlcs = self._tlcs(fiber, channel_id, payment_hash)
        assert len(tlcs) == 1, f"expected one {side} TLC, got {tlcs}"
        tlc = tlcs[0]
        assert side in tlc["status"], (side, tlc)
        return tlc

    def _wait_tlc_appear(self, fiber, channel_id, payment_hash, timeout):
        """Best-effort capture of the committed TLC before it unwinds."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            tlcs = self._tlcs(fiber, channel_id, payment_hash)
            if tlcs:
                return tlcs[0]
            time.sleep(0.1)
        return None

    def _wait_tlc_settled(self, fiber, channel_id, payment_hash, side, timeout=90):
        """Wait until the TLC is removed or acked as removed.

        ``RemoveAckConfirmed`` is the terminal status while the removal is still
        visible in ``list_channels``; once the removal is applied the TLC leaves
        ``pending_tlcs`` entirely. Both outcomes prove it was unwound rather
        than left locked.
        """
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            tlcs = self._tlcs(fiber, channel_id, payment_hash)
            if not tlcs:
                return "removed"
            status = tlcs[0]["status"]
            last = status.get(side)
            if last == "RemoveAckConfirmed":
                return last
            time.sleep(0.2)
        raise AssertionError(
            f"{side} TLC {payment_hash} on {channel_id} never settled: {last}"
        )

    def _assert_no_payment_session(self, fiber, payment_hash):
        try:
            session = fiber.get_client().get_payment({"payment_hash": payment_hash})
        except Exception as exc:
            assert "payment session not found" in str(exc).lower(), exc
            return
        raise AssertionError(f"unexpected downstream payment session: {session}")

    def _wait_payment_session(self, fiber, payment_hash, timeout=60):
        """Positive control for the receiver-side payment session query."""
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            try:
                session = fiber.get_client().get_payment({"payment_hash": payment_hash})
            except Exception as exc:
                last = str(exc)
            else:
                if session["status"] in ("Inflight", "Success"):
                    return session
                last = session["status"]
            time.sleep(0.5)
        raise AssertionError(
            f"trampoline node has no live payment session for {payment_hash}: {last}"
        )

    def _wait_node_log(self, fiber, needle, timeout=30):
        """Wait for a line the product writes while handling this payment.

        The RPC surface does not expose a TLC removal reason or the trampoline
        error code, so the node log is the only direct observation of the
        boundary rejection itself.
        """
        log_path = Path(f"{fiber.tmp_path}/node.log")
        deadline = time.time() + timeout
        content = ""
        while time.time() < deadline:
            if log_path.exists():
                content = log_path.read_text(errors="replace")
                if needle in content:
                    return content
            time.sleep(0.2)
        raise AssertionError(f"{needle!r} never appeared in {log_path}")

    @staticmethod
    def _invoice_hash_algorithm(invoice_result):
        for attr in invoice_result["invoice"]["data"]["attrs"]:
            if "hash_algorithm" in attr:
                return attr["hash_algorithm"]
        return None

    def _assert_no_downstream_for(self, router, carol, ch_down, payment_hash):
        """The rejected hash must have no downstream representation at all."""
        assert self._tlcs(router, ch_down, payment_hash) == [], "B started a B->C TLC"
        assert self._tlcs(carol, ch_down, payment_hash) == [], "C received a TLC"
        invoice_state = carol.get_client().get_invoice({"payment_hash": payment_hash})
        assert invoice_state["status"] == "Open", invoice_state
        return invoice_state["status"]

    def _balance(self, fiber, channel_id):
        return hex_int(self._channel(fiber, channel_id)["local_balance"])

    def _assert_ready(self, fiber, channel_id):
        channel = self._channel(fiber, channel_id)
        state = channel["state"]["state_name"]
        assert state == "ChannelReady", f"{channel_id} state {state}"
        assert channel.get("shutdown_transaction_hash") is None, channel

    def _balances(self, alice, router, carol, ch_up, ch_down):
        return {
            "alice_up": self._balance(alice, ch_up),
            "router_up": self._balance(router, ch_up),
            "router_down": self._balance(router, ch_down),
            "carol_down": self._balance(carol, ch_down),
        }

    # ------------------------------------------------------------- test cases

    # TEST-MAP: PR1673-01
    def test_mismatched_inner_hash_algorithm_is_rejected(self):
        alice, router, carol, ch_up, ch_down = self._build_topology()

        # C publishes a sha256 hold invoice (hash only, no local preimage); the
        # malicious sender keeps ckb_hash in its payment session and only the
        # inner trampoline payload says sha256.
        preimage = self.generate_random_preimage()
        payment_hash = sha256_hex(preimage)
        invoice = carol.get_client().new_invoice(
            {
                "amount": hex(PAYMENT_AMOUNT),
                "currency": "Fibd",
                "description": "sha256 hold invoice paid through a trampoline hop",
                "payment_hash": payment_hash,
                "hash_algorithm": "sha256",
                "allow_trampoline_routing": True,
            }
        )
        assert invoice["invoice"]["data"]["payment_hash"] == payment_hash
        invoice_now = carol.get_client().get_invoice({"payment_hash": payment_hash})
        assert self._invoice_hash_algorithm(invoice_now) == "sha256", invoice_now

        before = self._balances(alice, router, carol, ch_up, ch_down)

        payment = alice.get_client().send_payment(
            {
                "target_pubkey": carol.get_client().node_info()["pubkey"],
                "amount": hex(PAYMENT_AMOUNT),
                "payment_hash": payment_hash,
                "max_fee_amount": hex(TRAMPOLINE_FEE_BUDGET),
                "trampoline_hops": [router.get_client().node_info()["pubkey"]],
            }
        )
        assert payment["payment_hash"] == payment_hash

        # The A->B TLC is committed before B rejects the inner payload. Without
        # this the run would not have exercised the trampoline boundary at all.
        upstream = self._wait_tlc_appear(router, ch_up, payment_hash, timeout=30)
        assert upstream is not None, "A never committed the upstream TLC"
        assert "Inbound" in upstream["status"], upstream

        # The rejection travels back to A: the payment fails and no preimage
        # ever becomes visible. A's own failed_error records its sender-side
        # retry failure - the failed attempt consumes the whole fee budget, so
        # the rebuild sees remain_fee_amount() == 0 - not the trampoline error
        # code; the boundary rejection itself is observed below.
        result = self.wait_payment_finished(alice, payment_hash, timeout=120)
        assert result["status"] == "Failed", result
        assert result["payment_preimage"] is None, result
        assert result.get("failed_error"), result

        # The new guard logs the rejection before returning
        # InvalidOnionPayload; no RPC exposes the trampoline error code or the
        # TLC removal reason, so the node log is the direct observation here.
        # Match the guard's own rendered line, not just a shared prefix.
        node_log = self._wait_node_log(router, GUARD_LOG_MARKER, timeout=30)
        guard_line = next(
            line for line in node_log.splitlines() if GUARD_LOG_MARKER in line
        )

        # The upstream TLC is unwound instead of being left locked; the helper
        # raises unless it observed RemoveAckConfirmed or the TLC's removal.
        router_status = self._wait_tlc_settled(
            router, ch_up, payment_hash, "Inbound", timeout=90
        )
        alice_status = self._wait_tlc_settled(
            alice, ch_up, payment_hash, "Outbound", timeout=90
        )

        # No downstream side effect: immediately after the failure and again
        # across the bounded window, no B->C TLC exists, C received nothing and
        # its invoice stays Open.
        self._assert_no_downstream_for(router, carol, ch_down, payment_hash)
        deadline = time.time() + NO_DOWNSTREAM_SECONDS
        while time.time() < deadline:
            invoice_status = self._assert_no_downstream_for(
                router, carol, ch_down, payment_hash
            )
            time.sleep(1)
        self._assert_no_payment_session(router, payment_hash)

        # No money moved on either hop.
        after = self._balances(alice, router, carol, ch_up, ch_down)
        assert after == before, (before, after)
        for fiber, channel_id in (
            (alice, ch_up),
            (router, ch_up),
            (router, ch_down),
            (carol, ch_down),
        ):
            self._assert_ready(fiber, channel_id)

        print(
            "mismatched trampoline forward rejected",
            {
                "payment_hash": payment_hash,
                "failed_error": result.get("failed_error"),
                "guard_log_line": guard_line,
                "invoice_hash_algorithm": self._invoice_hash_algorithm(invoice_now),
                "upstream_tlc": upstream,
                "router_up_status": router_status,
                "alice_up_status": alice_status,
                "router_down_tlcs": self._tlcs(router, ch_down, payment_hash),
                "carol_invoice": invoice_status,
                "balances_before": before,
                "balances_after": after,
            },
        )

    # TEST-MAP: PR1673-02
    def test_matching_ckb_hash_trampoline_payment_succeeds(self):
        alice, router, carol, ch_up, ch_down = self._build_topology()

        # Matching control: upstream TLC, inner Forward and C's hold invoice all
        # use ckb_hash.
        preimage = self.generate_random_preimage()
        payment_hash = ckb_hash(preimage)
        invoice = carol.get_client().new_invoice(
            {
                "amount": hex(PAYMENT_AMOUNT),
                "currency": "Fibd",
                "description": "ckb_hash hold invoice paid through a trampoline hop",
                "payment_hash": payment_hash,
                "hash_algorithm": "ckb_hash",
                "allow_trampoline_routing": True,
            }
        )
        assert invoice["invoice"]["data"]["payment_hash"] == payment_hash
        invoice_now = carol.get_client().get_invoice({"payment_hash": payment_hash})
        assert self._invoice_hash_algorithm(invoice_now) == "ckb_hash", invoice_now

        before = self._balances(alice, router, carol, ch_up, ch_down)

        payment = alice.get_client().send_payment(
            {
                "target_pubkey": carol.get_client().node_info()["pubkey"],
                "amount": hex(PAYMENT_AMOUNT),
                "payment_hash": payment_hash,
                "max_fee_amount": hex(TRAMPOLINE_FEE_BUDGET),
                "trampoline_hops": [router.get_client().node_info()["pubkey"]],
            }
        )
        assert payment["payment_hash"] == payment_hash

        # C holds the TLC; both hops are observable before settlement.
        self.wait_invoice_state(carol, payment_hash, "Received", timeout=120)
        up_out = self._single_tlc(alice, ch_up, payment_hash, "Outbound")
        up_in = self._single_tlc(router, ch_up, payment_hash, "Inbound")
        down_out = self._single_tlc(router, ch_down, payment_hash, "Outbound")
        down_in = self._single_tlc(carol, ch_down, payment_hash, "Inbound")

        incoming = hex_int(up_in["amount"])
        outgoing = hex_int(down_out["amount"])
        assert hex_int(up_out["amount"]) == incoming, (up_out, up_in)
        assert hex_int(down_in["amount"]) == outgoing, (down_out, down_in)
        assert outgoing == PAYMENT_AMOUNT, outgoing
        assert 0 < incoming - outgoing <= TRAMPOLINE_FEE_BUDGET, (incoming, outgoing)

        # Positive control for PR1673-01's "no payment session" check: when the
        # guard accepts the forward, the trampoline node really does expose a
        # live payment session for this hash.
        router_session = self._wait_payment_session(router, payment_hash)

        carol.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )

        result = self.wait_payment_finished(alice, payment_hash, timeout=120)
        assert result["status"] == "Success", result
        assert result["payment_preimage"] == preimage, result
        self.wait_invoice_state(carol, payment_hash, "Paid", timeout=120)

        # The trampoline node's own downstream session settles with the same
        # preimage - the Fulfill evidence available over RPC. list_channels
        # exposes no TLC removal reason and the terminal status is pruned too
        # fast to assert reliably, so the fulfilled outcome is pinned by the
        # two Success sessions, C's Paid invoice and the balance equations.
        settled = self.wait_payment_finished(router, payment_hash, timeout=120)
        assert settled["status"] == "Success", settled
        assert settled["payment_preimage"] == preimage, settled

        # Every recorded hop TLC is released; the helper raises while any of
        # them is still locked in an active state.
        for fiber, channel_id, side in (
            (alice, ch_up, "Outbound"),
            (router, ch_up, "Inbound"),
            (router, ch_down, "Outbound"),
            (carol, ch_down, "Inbound"),
        ):
            self._wait_tlc_settled(fiber, channel_id, payment_hash, side, timeout=90)

        # Exact two-hop accounting: A pays incoming, B nets the fee, C is paid
        # outgoing.
        fee = hex_int(result["fee"])
        assert incoming - outgoing == fee, (incoming, outgoing, fee)
        after = self._balances(alice, router, carol, ch_up, ch_down)
        assert after["alice_up"] == before["alice_up"] - incoming, after
        assert after["router_up"] == before["router_up"] + incoming, after
        assert after["router_down"] == before["router_down"] - outgoing, after
        assert after["carol_down"] == before["carol_down"] + outgoing, after
        for fiber, channel_id in (
            (alice, ch_up),
            (router, ch_up),
            (router, ch_down),
            (carol, ch_down),
        ):
            self._assert_ready(fiber, channel_id)

        print(
            "matching trampoline payment settled",
            {
                "payment_hash": payment_hash,
                "invoice_hash_algorithm": self._invoice_hash_algorithm(invoice_now),
                "upstream_tlc_id": up_out["id"],
                "downstream_tlc_id": down_out["id"],
                "router_session_at_hold": router_session["status"],
                "router_session_final": settled["status"],
                "incoming": incoming,
                "outgoing": outgoing,
                "fee": fee,
                "balances_before": before,
                "balances_after": after,
            },
        )
