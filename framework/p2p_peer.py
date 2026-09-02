"""P2P fault-injection driver for the attack/debug fnn.

Wraps the debug Dev RPCs so tests can drop, capture, delay, and inject
Fiber channel messages without touching the honest victim.

Prefer ``P2pFiberTest`` in ``framework.basic_p2p`` when writing a new
scenario: it already starts ``current/fnn`` as victim and ``attack/fnn``
as this peer, then opens a ready channel.

Typical use:

    peer = P2pPeer(attacker_fiber)
    peer.intercept(
        channel_id,
        capture_in=["RevokeAndAck", "CommitmentSigned"],
        drop_out=["RevokeAndAck"],
    )
    peer.send_cs(channel_id)
    peer.wait_deliver("RevokeAndAck")

Queue rules (easy to get wrong):

- ``wait()`` / ``take()`` drain the inbound capture queue. Do not call
  them if you still want ``deliver()`` / ``wait_deliver()`` later.
- ``wait_held()`` / ``take_held()`` drain the outbound hold queue. Do
  not call them if you still want ``release_held()`` / ``wait_release()``.
- ``send_raw`` and the ``send_*`` helpers bypass intercept on the way
  out, so a held/dropped kind can still be injected.

Kind names accept ``CommitmentSigned`` or ``commitment_signed``.
"""

from __future__ import annotations

import time

from framework.fiber_rpc import FiberRPCClient
from framework.test_fiber import Fiber

# Names match FiberChannelMessage Display / intercept matching.
# All-zero id: intercept every channel (needed while funding still uses a temp id).
WILDCARD_CHANNEL_ID = "0x" + "00" * 32

# Channel establishment after OpenChannel (BOLT-02-like / docs/specs/p2p-message.md).
OPEN_CHANNEL_KINDS = (
    "OpenChannel",
    "AcceptChannel",
    "TxUpdate",
    "TxComplete",
    "CommitmentSigned",
    "TxSignatures",
    "ChannelReady",
    "AnnouncementSignatures",
)

CHANNEL_KINDS = (
    "OpenChannel",
    "AcceptChannel",
    "AnnouncementSignatures",
    "AddTlc",
    "ChannelReady",
    "ClosingSigned",
    "CommitmentSigned",
    "ReestablishChannel",
    "RemoveTlc",
    "RevokeAndAck",
    "Shutdown",
    "TxAbort",
    "TxAckRBF",
    "TxComplete",
    "TxInitRBF",
    "TxSignatures",
    "TxUpdate",
    "UpdateTlcInfo",
)


def _hex_u64(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return hex(int(value))


def _hex_u128(value):
    return _hex_u64(value)


def hex_int(value):
    if value is None:
        return None
    if isinstance(value, int):
        return value
    return int(value, 16)


class P2pPeer:
    """One attack/debug Fiber node used as a controllable P2P peer."""

    def __init__(self, fiber: Fiber):
        self.fiber = fiber
        self.client: FiberRPCClient = fiber.get_client()
        self._inbox = []

    def intercept(
        self,
        channel_id,
        *,
        capture_in=None,
        drop_in=None,
        drop_out=None,
        hold_out=None,
        capture_all_in=False,
        drop_raa=False,
    ):
        """Configure intercept on this peer for one channel.

        Kind names accept ``CommitmentSigned`` or ``commitment_signed``.
        Passing empty lists clears that action; call ``clear_intercept``
        to turn everything off.
        """
        params = {
            "channel_id": channel_id,
            "suppress_outbound_revoke_and_ack": bool(drop_raa),
            "capture_inbound": bool(capture_all_in),
            "inbound_capture_kinds": list(capture_in or []),
            "inbound_drop_kinds": list(drop_in or []),
            "outbound_drop_kinds": list(drop_out or []),
            "outbound_hold_kinds": list(hold_out or []),
        }
        return self.client.set_fiber_message_intercept(params)

    def clear_intercept(self, channel_id):
        return self.intercept(channel_id)

    def take(self, kind=None):
        """Drain captured inbound messages. Optionally keep only ``kind``."""
        result = self.client.take_captured_fiber_messages() or {}
        messages = list(result.get("messages") or [])
        self._inbox.extend(messages)
        if kind is None:
            taken = self._inbox
            self._inbox = []
            return taken
        matched = []
        leftover = []
        for message in self._inbox:
            if self._kind_eq(message.get("kind"), kind):
                matched.append(message)
            else:
                leftover.append(message)
        self._inbox = leftover
        return matched

    def wait(self, kind, timeout=30):
        """Block until one captured inbound message of ``kind`` arrives.

        This drains the message from the node. If you later need the
        channel actor to see it, use ``wait_deliver()`` instead.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            matched = self.take(kind)
            if matched:
                return matched[0]
            time.sleep(0.2)
        raise TimeoutError(f"timed out waiting for captured {kind}")

    def deliver(self, count=None, kind=None):
        """Release captured inbound messages into this node's channel actor."""
        params = {"kinds": [kind] if kind else []}
        if count is not None:
            params["count"] = _hex_u64(count)
        return self.client.deliver_captured_fiber_messages(params)

    def wait_deliver(self, kind=None, count=1, timeout=30):
        """Wait until ``count`` captured inbound messages are delivered.

        Polls ``deliver()`` and does not drain via ``take()`` / ``wait()``.
        """
        deadline = time.time() + timeout
        delivered = 0
        while time.time() < deadline:
            result = self.deliver(kind=kind)
            delivered += hex_int(result.get("delivered") or "0x0")
            if delivered >= count:
                return delivered
            time.sleep(0.2)
        raise TimeoutError(
            f"timed out delivering {count} captured {kind or 'message'}(s); got {delivered}"
        )

    def take_held(self):
        result = self.client.take_held_outbound_fiber_messages() or {}
        return list(result.get("messages") or [])

    def wait_held(self, kind=None, timeout=30):
        """Drain the hold queue until a matching outbound message is seen.

        Consumes the queue. Use ``wait_release()`` if the peer should
        still receive the message later.
        """
        deadline = time.time() + timeout
        leftover = []
        while time.time() < deadline:
            leftover.extend(self.take_held())
            if kind is None:
                if leftover:
                    taken = leftover
                    leftover = []
                    return taken
            else:
                matched = [
                    message
                    for message in leftover
                    if self._kind_eq(message.get("kind"), kind)
                ]
                leftover = [
                    message
                    for message in leftover
                    if not self._kind_eq(message.get("kind"), kind)
                ]
                if matched:
                    return matched
            time.sleep(0.2)
        raise TimeoutError(f"timed out waiting for held {kind or 'message'}")

    def release_held(self, count=None, kind=None):
        params = {"kinds": [kind] if kind else []}
        if count is not None:
            params["count"] = _hex_u64(count)
        return self.client.release_held_outbound_fiber_messages(params)

    def wait_release(self, kind=None, count=1, timeout=30):
        """Wait until ``count`` held outbound messages are sent to the peer."""
        deadline = time.time() + timeout
        released = 0
        while time.time() < deadline:
            result = self.release_held(kind=kind)
            released += hex_int(result.get("released") or "0x0")
            if released >= count:
                return released
            time.sleep(0.2)
        raise TimeoutError(
            f"timed out releasing {count} held {kind or 'message'}(s); got {released}"
        )

    def musig2(self, channel_id):
        return self.client.get_channel_musig2_public({"channel_id": channel_id})

    def send_raw(self, channel_id, kind, **fields):
        params = {"channel_id": channel_id, "kind": kind}
        for key, value in fields.items():
            if value is not None:
                params[key] = value
        return self.client.send_raw_channel_message(params)

    def send_cs(
        self,
        channel_id,
        nonce_commitment_number=None,
        funding_tx_partial_signature=None,
    ):
        params = {"channel_id": channel_id, "kind": "commitment_signed"}
        if nonce_commitment_number is not None:
            params["nonce_commitment_number"] = _hex_u64(nonce_commitment_number)
        if funding_tx_partial_signature is not None:
            params["funding_tx_partial_signature"] = funding_tx_partial_signature
        return self.client.send_raw_channel_message(params)

    def send_shutdown(self, channel_id, close_script=None, fee_rate=None):
        params = {"channel_id": channel_id, "kind": "shutdown"}
        if close_script is not None:
            params["close_script"] = close_script
        if fee_rate is not None:
            params["fee_rate"] = _hex_u64(fee_rate)
        return self.client.send_raw_channel_message(params)

    def send_add_tlc(
        self,
        channel_id,
        amount=1,
        payment_hash=None,
        expiry=None,
        tlc_id=None,
    ):
        params = {
            "channel_id": channel_id,
            "kind": "add_tlc",
            "amount": _hex_u128(amount),
        }
        if payment_hash is not None:
            params["payment_hash"] = payment_hash
        if expiry is not None:
            params["expiry"] = _hex_u64(expiry)
        if tlc_id is not None:
            params["tlc_id"] = _hex_u64(tlc_id)
        return self.client.send_raw_channel_message(params)

    def send_remove_tlc(self, channel_id, tlc_id, error_code="TemporaryChannelFailure"):
        return self.client.send_raw_channel_message(
            {
                "channel_id": channel_id,
                "kind": "remove_tlc",
                "tlc_id": _hex_u64(tlc_id),
                "remove_fail_error_code": error_code,
            }
        )

    def send_reestablish(self, channel_id, local_cn=None, remote_cn=None):
        params = {"channel_id": channel_id, "kind": "reestablish_channel"}
        if local_cn is not None:
            params["local_commitment_number"] = _hex_u64(local_cn)
        if remote_cn is not None:
            params["remote_commitment_number"] = _hex_u64(remote_cn)
        return self.client.send_raw_channel_message(params)

    def send_tx_abort(self, channel_id, message="p2p-test"):
        return self.client.send_raw_channel_message(
            {
                "channel_id": channel_id,
                "kind": "tx_abort",
                "abort_message": message,
            }
        )

    def send_closing_signed(self, channel_id, partial_signature):
        return self.client.send_raw_channel_message(
            {
                "channel_id": channel_id,
                "kind": "closing_signed",
                "funding_tx_partial_signature": partial_signature,
            }
        )

    @staticmethod
    def _kind_eq(left, right):
        def normalize(value):
            return "".join(ch for ch in str(value or "") if ch not in "_-").lower()

        return normalize(left) == normalize(right)
