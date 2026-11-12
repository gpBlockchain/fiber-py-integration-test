"""MPP 证据辅助的离线负向测试；不计为真实节点回归结果。"""

import copy
from types import SimpleNamespace

import pytest

from test_cases.fiber.devnet.send_payment.mpp import mpp_fixture as fixture
from test_cases.fiber.devnet.send_payment.mpp import test_mpp_startup_history as perf


def test_sha256_uses_standard_known_vector():
    assert fixture.payment_hash("0x" + "00" * 32, "sha256") == (
        "0x66687aadf862bd776c8fc18b8e9f8e20089714856ee233b3902a591d0d5f2925"
    )


def cleared_fixture():
    case = fixture.MppFixture()
    case.hash = "0x" + "01" * 32
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
    case._open = lambda edge: rows
    return case, dict(before=before, sent=10), rows


def test_open_settlement_requires_exact_two_sided_balances():
    case, edge, rows = cleared_fixture()
    assert case._cleared(edge)
    rows[0]["local_balance"] = "0x50"
    with pytest.raises(AssertionError):
        case._cleared(edge)


def test_pending_target_tlc_is_not_cleared_even_with_zero_aggregate():
    case, edge, rows = cleared_fixture()
    rows[0]["pending_tlcs"] = [
        dict(payment_hash=case.hash, status={"Outbound": "Committed"})
    ]
    assert case._cleared(edge) is False
    rows[0]["pending_tlcs"][0]["status"] = {"Outbound": "RemoveAckConfirmed"}
    assert case._cleared(edge)


def test_refund_does_not_accept_a_fee_deduction():
    case, edge, rows = cleared_fixture()
    for i in range(2):
        rows[i].update(edge["before"][i])
    assert case._cleared(edge, success=False)
    rows[0]["local_balance"] = "0x63"
    with pytest.raises(AssertionError):
        case._cleared(edge, success=False)


def test_history_rejects_repeated_page_hash():
    row = dict(
        payment_hash="0x01",
        payment_preimage="0x02",
        status="Success",
        failed_error=None,
    )
    client = SimpleNamespace(
        list_payments=lambda args: {"payments": [row], "last_cursor": "0x01"}
    )
    case = perf.TestMppStartupHistory()
    with pytest.raises(AssertionError):
        case._history(SimpleNamespace(get_client=lambda: client))


def test_performance_keeps_failed_samples_and_pending_acceptance(tmp_path):
    case = perf.TestMppStartupHistory()
    rows = []
    for label in ("base", "head"):
        for backend in ("rocksdb", "sqlite"):
            for size in (0, 1000, 8000):
                for trial in range(10):
                    rows.append(
                        dict(
                            label=label,
                            backend=backend,
                            size=size,
                            trial=trial,
                            status="passed",
                            node_info_seconds=1,
                            first_payment_seconds=2,
                            sampled_peak_rss_kib=300,
                        )
                    )
    rows[0]["status"] = "failed"
    before = copy.deepcopy(rows)
    output = tmp_path / "samples.jsonl"
    case._distributions(output, rows, None)
    assert rows == before
    text = output.read_text()
    assert '"failed_samples": 1' in text and '"acceptance": "pending"' in text
    with pytest.raises(AssertionError):
        case._distributions(
            output,
            rows,
            dict(
                max_node_info_seconds=0.5,
                max_first_payment_seconds=2,
                max_sampled_peak_rss_kib=300,
                max_head_base_ratio=1,
            ),
        )


def test_bootnode_edit_preserves_hex_and_unrelated_yaml(tmp_path):
    path = tmp_path / "config.yml"
    original = (
        "fiber:\n  bootnode_addrs:\n    - /ip4/127.0.0.1/tcp/1111\n"
        "  scripts:\n    - script:\n        code_hash: 0x0123456789abcdef\n"
        "  enabled: true\nckb:\n  rpc_url: http://127.0.0.1:8114\n"
    )
    path.write_text(original)
    fixture.clear_bootnodes(path)
    assert path.read_text() == original.replace(
        "  bootnode_addrs:\n    - /ip4/127.0.0.1/tcp/1111\n", "  bootnode_addrs: []\n"
    )
    once = path.read_bytes()
    fixture.clear_bootnodes(path)
    assert path.read_bytes() == once
