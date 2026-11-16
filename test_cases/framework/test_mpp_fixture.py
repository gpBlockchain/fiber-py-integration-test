"""MPP fixture 的金额、分页和配置编辑辅助测试。"""

from unittest.mock import Mock

import pytest

from test_cases.fiber.devnet.send_payment.mpp import mpp_fixture as fixture
from framework.helper.fiber_ops import clear_bootnodes, calculate_payment_hash
from framework.helper.channel_settlement import ChannelSettlement
from test_cases.fiber.devnet.send_payment.mpp import test_mpp_payment_history as history


def test_sha256_uses_standard_known_vector():
    assert calculate_payment_hash("0x" + "00" * 32, "sha256") == (
        "0x66687aadf862bd776c8fc18b8e9f8e20089714856ee233b3902a591d0d5f2925"
    )


def cleared_fixture():
    case = fixture.MppFixture()
    case.payment_hash = "0x" + "01" * 32
    before = [
        dict(local_balance="0x64", remote_balance="0x0"),
        dict(local_balance="0x0", remote_balance="0x64"),
    ]
    rows = [
        dict(local_balance="0x5a", remote_balance="0xa"),
        dict(local_balance="0xa", remote_balance="0x5a"),
    ]
    for row in rows:
        row.update(
            pending_tlcs=[], offered_tlc_balance="0x0", received_tlc_balance="0x0"
        )
    case.settlement = ChannelSettlement(Mock(), "commitment")
    case.settlement.assert_channel_open = lambda edge: rows
    return case, dict(before=before, sent=10), rows


def test_open_settlement_requires_exact_two_sided_balances():
    case, edge, rows = cleared_fixture()
    assert case.settlement.payment_is_settled(edge, case.payment_hash)
    rows[0]["local_balance"] = "0x50"
    with pytest.raises(AssertionError):
        case.settlement.payment_is_settled(edge, case.payment_hash)


def test_pending_target_tlc_is_not_cleared_even_with_zero_aggregate():
    case, edge, rows = cleared_fixture()
    rows[0]["pending_tlcs"] = [
        dict(payment_hash=case.payment_hash, status={"Outbound": "Committed"})
    ]
    assert case.settlement.payment_is_settled(edge, case.payment_hash) is False
    rows[0]["pending_tlcs"][0]["status"] = {"Outbound": "RemoveAckConfirmed"}
    assert case.settlement.payment_is_settled(edge, case.payment_hash)


def test_refund_does_not_accept_a_fee_deduction():
    case, edge, rows = cleared_fixture()
    for i in range(2):
        rows[i].update(edge["before"][i])
    assert case.settlement.payment_is_settled(edge, case.payment_hash, success=False)
    rows[0]["local_balance"] = "0x63"
    with pytest.raises(AssertionError):
        case.settlement.payment_is_settled(edge, case.payment_hash, success=False)


@pytest.mark.parametrize("version,deposit", [("legacy", 99), ("v1", 100)])
def test_chain_settlement_uses_original_channel_deposit(version, deposit):
    case = fixture.MppFixture()
    edge = dict(
        before=[dict(local_balance=hex(100 * fixture.CKB)), dict(local_balance="0x0")],
        sent=10 * fixture.CKB,
    )
    case.channels, case.sender, case.receiver, case.payment_hash = (
        [edge],
        Mock(),
        Mock(),
        "0x01",
    )
    case.wait_for_chain_settlement = Mock()
    case.assert_payment_success = Mock()
    case.wait_invoice_state = Mock()
    chain = dict(
        channel=edge,
        claimed=True,
        version=version,
        fee=46,
        net=[(90 + deposit) * fixture.CKB - 12, (10 + deposit) * fixture.CKB - 34],
    )
    case.assert_payment_fully_settled(chain)
    # 丢失 1 CKB 不得归为手续费容差而通过。
    chain["net"][0] -= fixture.CKB
    with pytest.raises(AssertionError):
        case.assert_payment_fully_settled(chain)


def test_history_rejects_repeated_page_hash():
    row = dict(
        payment_hash="0x01",
        payment_preimage="0x02",
        status="Success",
        failed_error=None,
    )
    client = Mock(list_payments=lambda args: {"payments": [row], "last_cursor": "0x01"})
    case = history.TestMppPaymentHistory()
    with pytest.raises(AssertionError):
        case._history(Mock(get_client=lambda: client))


def test_bootnode_edit_preserves_hex_and_unrelated_yaml(tmp_path):
    path = tmp_path / "config.yml"
    original = (
        "fiber:\n  bootnode_addrs:\n    - /ip4/127.0.0.1/tcp/1111\n"
        "  scripts:\n    - script:\n        code_hash: 0x0123456789abcdef\n"
        "  enabled: true\nckb:\n  rpc_url: http://127.0.0.1:8114\n"
    )
    path.write_text(original)
    clear_bootnodes(path)
    assert path.read_text() == original.replace(
        "  bootnode_addrs:\n    - /ip4/127.0.0.1/tcp/1111\n", "  bootnode_addrs: []\n"
    )
    once = path.read_bytes()
    clear_bootnodes(path)
    assert path.read_bytes() == once


def test_test_classes_do_not_inherit_other_test_cases():
    from framework.basic_fiber import FiberTest
    from test_cases.fiber.devnet.trampoline_routing.test_trampoline_timeout_refund import (
        TestTrampolineTimeoutRefund,
    )
    from test_cases.fiber.devnet.trampoline_routing.test_trampoline_timeout_refund_restart import (
        TestTrampolineTimeoutRefundRestart,
    )

    for cls in (
        fixture.MppFixture,
        TestTrampolineTimeoutRefund,
        TestTrampolineTimeoutRefundRestart,
    ):
        assert cls.__bases__ == (FiberTest,)


def test_restart_uses_same_data_and_only_selected_peers(tmp_path):
    from framework.helper.fiber_ops import restart_fiber

    config = tmp_path / "config.yml"
    config.write_text("fiber:\n  bootnode_addrs: []\n")
    fiber, peer = Mock(tmp_path=str(tmp_path)), Mock()
    restart_fiber(fiber, peers=(peer,), timeout=10)
    fiber.stop.assert_called_once_with()
    fiber.start.assert_called_once_with(fnn_log_level="info", timeout=10)
    fiber.connect_peer.assert_called_once_with(peer)
    fiber.clean.assert_not_called()
    assert config.is_file()


def test_spending_lookup_rejects_same_lock_without_exact_input():
    funding = dict(tx_hash="funding", index="0x0")
    unrelated = dict(tx_hash="unrelated", index="0x0")
    expected = dict(inputs=[dict(previous_output=funding)], outputs=[])
    transactions = {
        "funding": dict(transaction=dict(outputs=[dict(lock={"code_hash": "lock"})])),
        "wrong": dict(
            tx_status={"status": "committed"},
            transaction=dict(inputs=[dict(previous_output=unrelated)]),
        ),
        "right": dict(tx_status={"status": "committed"}, transaction=expected),
    }
    rpc = Mock()
    rpc.get_transaction.side_effect = transactions.__getitem__
    rpc.get_transactions.return_value = dict(
        objects=[dict(io_type="input", tx_hash=n) for n in ("wrong", "right")]
    )
    settlement = ChannelSettlement(rpc, "commitment")
    assert settlement.find_spending_transaction(funding) is expected


def test_net_amounts_subtract_external_fee_input_and_reject_unknown_owner():
    owner_a, owner_b = {"code_hash": "a"}, {"code_hash": "b"}
    funding = dict(tx_hash="funding", index="0x0")
    fee_input = dict(tx_hash="fee", index="0x0")

    def cell(amount, lock):
        return dict(capacity=hex(amount), lock=lock, type=None)

    settlement = ChannelSettlement(Mock(), "commitment")
    settlement.get_output = lambda outpoint: (
        cell(1000, {"code_hash": "commitment"})
        if outpoint == funding
        else cell(100, owner_a)
    )
    tx = dict(
        inputs=[dict(previous_output=p) for p in (funding, fee_input)],
        outputs=[cell(550, owner_a), cell(500, owner_b)],
    )
    assert settlement.net_amounts(tx, funding, [owner_a, owner_b]) == ([450, 500], 50)
    tx["outputs"][1]["lock"] = {"code_hash": "unknown"}
    with pytest.raises(AssertionError):
        settlement.net_amounts(tx, funding, [owner_a, owner_b])
