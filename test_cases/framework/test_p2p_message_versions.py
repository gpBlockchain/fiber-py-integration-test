"""Explicit wire-version filters must work without aliasing Legacy to V2."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from framework.p2p_peer import P2pPeer

CS_KINDS = ("CommitmentSigned", "CommitmentSignedV2")
RAA_KINDS = ("RevokeAndAck", "RevokeAndAckV2")


def make_peer():
    client = Mock()
    client.take_captured_fiber_messages.return_value = {"messages": []}
    return P2pPeer(SimpleNamespace(get_client=lambda: client)), client


@pytest.mark.parametrize("kind", CS_KINDS + ("commitment_signed_v2",))
def test_take_accepts_explicit_wire_versions_and_keeps_other_messages(kind):
    peer, client = make_peer()
    message = {"kind": kind, "payload": "0x01"}
    other = {"kind": "AddTlc", "payload": "0x02"}
    client.take_captured_fiber_messages.side_effect = [
        {"messages": [message, other]},
        {"messages": []},
    ]
    assert peer.take(CS_KINDS) == [message]
    assert peer.take() == [other]


def test_legacy_filter_does_not_silently_match_v2():
    peer, client = make_peer()
    message = {"kind": "CommitmentSignedV2", "payload": "0x01"}
    client.take_captured_fiber_messages.side_effect = [
        {"messages": [message]},
        {"messages": []},
    ]
    assert peer.take("CommitmentSigned") == []
    assert peer.take("commitment_signed_v2") == [message]


@pytest.mark.parametrize("kind", CS_KINDS)
def test_wait_and_wait_held_accept_explicit_wire_versions(kind):
    peer, client = make_peer()
    message = {"kind": kind, "payload": "0x01"}
    client.take_captured_fiber_messages.return_value = {"messages": [message]}
    client.take_held_outbound_fiber_messages.return_value = {"messages": [message]}
    assert peer.wait(CS_KINDS, timeout=1) == message
    assert peer.wait_held(CS_KINDS, timeout=1) == [message]


@pytest.mark.parametrize(
    ("kind", "expected"),
    [(None, []), ("RevokeAndAck", ["RevokeAndAck"]), (RAA_KINDS, list(RAA_KINDS))],
)
def test_deliver_and_release_send_flat_explicit_filters(kind, expected):
    peer, client = make_peer()
    peer.deliver(count=1, kind=kind)
    peer.release_held(count=1, kind=kind)
    params = {"kinds": expected, "count": "0x1"}
    client.deliver_captured_fiber_messages.assert_called_once_with(params)
    client.release_held_outbound_fiber_messages.assert_called_once_with(params)


def test_intercept_preserves_both_versions_and_can_be_cleared():
    peer, client = make_peer()
    peer.intercept("channel", capture_in=CS_KINDS, drop_out=RAA_KINDS, drop_raa=True)
    params = client.set_fiber_message_intercept.call_args.args[0]
    assert params["inbound_capture_kinds"] == list(CS_KINDS)
    assert params["outbound_drop_kinds"] == list(RAA_KINDS)
    assert params["suppress_outbound_revoke_and_ack"] is True
    peer.clear_intercept("channel")
    params = client.set_fiber_message_intercept.call_args.args[0]
    assert params["inbound_capture_kinds"] == []
    assert params["outbound_drop_kinds"] == []
    assert params["suppress_outbound_revoke_and_ack"] is False
