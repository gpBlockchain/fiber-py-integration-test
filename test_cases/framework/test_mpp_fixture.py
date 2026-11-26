"""MPP fixture 的金额、分页和配置编辑辅助测试。"""

from unittest.mock import Mock

import pytest

from test_cases.fiber.devnet.send_payment.mpp import mpp_fixture as fixture
from framework.helper.fiber_ops import clear_bootnodes, calculate_payment_hash
from framework.helper.channel_settlement import ChannelSettlement
from framework.helper.settlement_witness import (
    SettlementTlc,
    SettlementUnlock,
    SettlementWitness,
)
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


def test_closed_channel_wait_does_not_treat_a_missing_channel_as_finished():
    client = Mock()
    fiber = Mock(get_client=lambda: client)
    state = {"state_name": "Closed", "state_flags": "UNCOOPERATIVE_LOCAL"}
    other = dict(channel_id="other", state=dict(state))
    target = dict(channel_id="original", state=state)
    client.list_channels.return_value = {"channels": [other, target]}
    waiting = ChannelSettlement.closed_channel_waits_onchain

    state["state_flags"] += " | WAITING_ONCHAIN_SETTLEMENT"
    assert waiting(fiber, "original") is True
    state["state_flags"] = "UNCOOPERATIVE_LOCAL"
    assert waiting(fiber, "original") is False
    client.list_channels.assert_called_with({"include_closed": True})
    state["state_name"] = "ShuttingDown"
    with pytest.raises(AssertionError):
        waiting(fiber, "original")
    client.list_channels.return_value = {"channels": [other]}
    with pytest.raises(AssertionError, match="未返回原通道 original"):
        waiting(fiber, "original")


@pytest.mark.parametrize("unlock_type", [0, 0xFE, 0xFF])
def test_chain_timeout_requires_the_target_tlc_unlock(unlock_type):
    # 同一个输入、TLC 和净到账；唯一变量是消费见证选择了 TLC 还是单方余额。
    target_hash = bytes.fromhex("ab" * 32)
    tlc = SettlementTlc(0, 100, target_hash, bytes(20), bytes(20), 1000)
    witness = SettlementWitness(
        "v1",
        bytes(16),
        [tlc],
        bytes(20),
        0,
        bytes(20),
        0,
        [SettlementUnlock(unlock_type, bytes(65))],
    )
    previous = dict(tx_hash="original", index="0x0")
    owner = {"code_hash": "owner"}
    output = dict(capacity="0x64", type=None, lock=owner)
    tx = dict(
        hash="spend",
        inputs=[dict(previous_output=previous)],
        outputs=[output],
        witnesses=[witness.to_hex()],
    )
    observer = ChannelSettlement(Mock(), "commitment")
    observer.find_spending_transaction = Mock(return_value=tx)
    observer.get_output = Mock(
        return_value=dict(output, lock={"code_hash": "commitment"})
    )
    chain = dict(
        done=False,
        version="v1",
        outpoint=previous,
        seen=False,
        claimed=False,
        timed_out=False,
        locks=[owner, {"code_hash": "peer"}],
        net=[0, 0],
        fee=0,
        funding=100,
        spends=[],
    )
    assert observer.advance(
        chain, "0x" + target_hash.hex(), 100, None, allow_claim=False
    )
    assert chain["timed_out"] is (unlock_type == 0)


@pytest.mark.parametrize(
    "source, changed_fields",
    [
        pytest.param("payment", {}, id="success-stays-success"),
        pytest.param("payment", {"status": "Failed"}, id="success-becomes-failed"),
        pytest.param("payment", {"status": "Inflight"}, id="success-becomes-inflight"),
        pytest.param("payment", {"payment_preimage": None}, id="preimage-lost"),
        pytest.param(
            "payment", {"payment_preimage": "0x" + "55" * 32}, id="wrong-preimage"
        ),
        pytest.param("invoice", {"status": "Received"}, id="invoice-not-paid"),
        pytest.param("invoice", {"invoice_address": "old-invoice"}, id="wrong-invoice"),
        pytest.param("payee", {"local_balance": "0x0"}, id="not-credited"),
        pytest.param("payee", {"local_balance": "0x14"}, id="credited-twice"),
        pytest.param("payer", {"offered_tlc_balance": "0xa"}, id="still-locked"),
    ],
)
def test_same_hash_paid_invoice_rechecks_both_nodes_and_balances(
    source, changed_fields
):
    # 两次读取之间只变一项公开结果；这里验证断言，不模拟节点或旧通道的实际结算。
    case, edge, rows = cleared_fixture()
    preimage = "0x" + "35" * 32
    payment_hash = calculate_payment_hash(preimage, "ckb_hash")
    payment = dict(
        payment_hash=payment_hash,
        status="Success",
        payment_preimage=preimage,
        failed_error=None,
    )
    invoice = dict(
        invoice_address="new-invoice",
        status="Paid",
        invoice=dict(data=dict(payment_hash=payment_hash), amount=hex(edge["sent"])),
    )
    sender_client, receiver_client = Mock(), Mock()
    sender_client.get_payment.side_effect = lambda query: dict(payment)
    receiver_client.get_invoice.side_effect = lambda query: dict(invoice)
    edge.update(
        left=Mock(get_client=lambda: sender_client),
        right=Mock(get_client=lambda: receiver_client),
    )

    def assert_paid_now():
        assert case.settlement.invoice_payment_is_settled(
            edge, "new-invoice", payment_hash, preimage, "ckb_hash"
        )

    assert_paid_now()
    {"payment": payment, "invoice": invoice, "payer": rows[0], "payee": rows[1]}[
        source
    ].update(changed_fields)
    if changed_fields:
        with pytest.raises(AssertionError):
            assert_paid_now()
    else:
        assert_paid_now()
    assert sender_client.get_payment.call_count == 2
    sender_client.get_payment.assert_called_with({"payment_hash": payment_hash})
    receiver_client.get_invoice.assert_called_with({"payment_hash": payment_hash})


def test_refunded_receiver_cannot_collect_the_same_hash_payment_again():
    # 多给 B 的旧持单金额由 A 的净额扣掉，手续费和总额仍完全守恒；必须按收款方拦下。
    settlement = ChannelSettlement(Mock(), "commitment")
    edge = dict(left=Mock(), channel_id="old", sent=100)
    chain = dict(
        funding=1000, net=[700, 0], fee=1, done=False, timed_out=True, claimed=False
    )

    def wrong_distribution(*args, **kwargs):
        chain.update(net=[600, 398], fee=2, done=True)

    settlement.wait = wrong_distribution
    with pytest.raises(AssertionError):
        settlement.wait_refunded_channel_close(
            edge, chain, "old-hash", None, receiver_capacity=299
        )
    assert sum(chain["net"]) + chain["fee"] == chain["funding"]


def test_timeout_refund_uses_cluster_expiry_and_real_poll(monkeypatch):
    from framework.helper import channel_settlement as module

    # 主机墙钟在 0，集群已越过 4 小时到期点；等待确认仍按真实秒轮询。
    timer = Mock()
    timer.monotonic.side_effect = [10, 11, 12, 1811]
    timer.time.return_value = 0
    monkeypatch.setattr(module, "time", timer)
    now_s = Mock(return_value=14_401)
    ckb = Mock()
    observer = ChannelSettlement(ckb, "commitment")
    deposit = module.DEFAULT_MIN_DEPOSIT_CKB
    edge = dict(
        before=[dict(local_balance="0x64"), dict(local_balance="0x32")],
        sent=20,
        tlcs=[dict(expiry=hex(14_400_000))],
    )
    cell = {"tx_hash": "pending", "index": "0x0"}
    chain = dict(
        version="v1",
        funding=2 * deposit + 150,
        fee=1,
        net=[0, 0],
        done=False,
        seen=False,
        claimed=False,
        timed_out=False,
        outpoint=cell,
    )

    def advance(chain, *args, **kwargs):
        if ckb.generate_epochs.called:
            chain.update(timed_out=True, seen=True, net=[deposit + 100, 0])

    monkeypatch.setattr(observer, "advance", advance)
    assert observer.wait_direct_timeout_refund(
        edge, chain, "hash", None, now_s=now_s
    ) == (cell, deposit + 49)
    timer.sleep.assert_called_once_with(5)
    timer.time.assert_not_called()
    ckb.generate_epochs.assert_called_once_with("0x1", wait_time=0)
