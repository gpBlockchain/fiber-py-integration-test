from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest

from framework.helper import contract


@pytest.fixture
def upgrade_env(monkeypatch, tmp_path):
    binary = tmp_path / 'contract'
    binary.write_bytes(b'new code')
    lock = {
        'code_hash': '0x9bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8',
        'hash_type': 'type', 'args': '0x' + '11' * 20,
    }
    output = {
        'capacity': hex(200 * 100000000), 'lock': lock,
        'type': {'code_hash': '0x' + '00' * 25 + '545950455f4944',
                 'hash_type': 'type', 'args': '0x' + '22' * 32},
    }
    live = {'status': 'live', 'cell': {'output': output, 'data': {'content': '0x1234'}}}
    rpc = Mock()
    rpc.get_live_cell.return_value = live
    monkeypatch.setattr(contract, 'RPCClient', Mock(return_value=rpc))
    monkeypatch.setattr(contract, 'util_key_info_by_private_key', Mock(return_value={
        'address': {'testnet': 'address'}, 'lock_arg': lock['args'],
    }))
    wallet = Mock(return_value={'live_cells': []})
    monkeypatch.setattr(contract, 'wallet_get_live_cells', wallet)
    mocks = {}
    for name in ('tx_init', 'tx_add_input', 'tx_add_signature', 'tx_sign_inputs', 'tx_send'):
        mocks[name] = Mock()
        monkeypatch.setattr(contract, name, mocks[name])
    mocks['tx_init'].side_effect = lambda path, url: Path(path).write_text(
        '{"transaction": {"outputs": [], "outputs_data": []}}'
    )
    mocks['tx_sign_inputs'].return_value = [{'lock-arg': lock['args'], 'signature': 'sig'}]
    captured = {}

    def send(path, url):
        import json
        captured.update(json.loads(Path(path).read_text())['transaction'])
        return '0xupdated\n'

    mocks['tx_send'].side_effect = send
    return binary, rpc, live, wallet, mocks, captured


def test_upgrade_preserves_scripts_and_replaces_data(upgrade_env):
    binary, rpc, live, wallet, mocks, tx = upgrade_env
    original = deepcopy(live)
    result = contract.upgrade_ckb_type_contract('key', binary, 'old', '0x2', api_url='rpc')
    assert result == '0xupdated'
    rpc.get_live_cell.assert_called_once_with('0x2', 'old', True)
    wallet.assert_not_called()
    assert tx['outputs'] == [{**original['cell']['output'], 'capacity': hex(20000000000 - 1000)}]
    assert tx['outputs_data'] == ['0x' + b'new code'.hex()]
    assert live == original
    path = mocks['tx_init'].call_args.args[0]
    mocks['tx_add_input'].assert_called_once_with('old', 2, path, 'rpc')
    mocks['tx_add_signature'].assert_called_once_with('0x' + '11' * 20, 'sig', path, 'rpc')
    assert not Path(path).exists()


def test_upgrade_funds_growth_and_skips_protected_cells(upgrade_env):
    binary, rpc, live, wallet, mocks, tx = upgrade_env
    binary.write_bytes(b'x' * 100)
    wallet.return_value = {'live_cells': [
        {'tx_hash': 'old', 'output_index': 0},
        {'tx_hash': 'typed', 'output_index': 0},
        {'tx_hash': 'data', 'output_index': 0},
        {'tx_hash': 'foreign', 'output_index': 0},
        {'tx_hash': 'dead', 'output_index': 0},
        {'tx_hash': 'old', 'output_index': '0x1'},
    ]}
    typed = deepcopy(live)
    data_cell = deepcopy(live)
    data_cell['cell']['output']['type'] = None
    empty = deepcopy(data_cell)
    empty['cell']['data']['content'] = '0x'
    foreign = deepcopy(empty)
    foreign['cell']['output']['lock']['args'] = '0x' + '33' * 20
    rpc.get_live_cell.side_effect = [live, typed, data_cell, foreign, {'status': 'dead'}, empty]
    contract.upgrade_ckb_type_contract('key', binary, 'old')
    assert [call.args[:2] for call in mocks['tx_add_input'].call_args_list] == [('old', 0), ('old', 1)]
    assert int(tx['outputs'][0]['capacity'], 16) == 40000000000 - 1000
    assert tx['outputs_data'] == ['0x' + (b'x' * 100).hex()]


@pytest.mark.parametrize('failure', ['dead', 'no_type', 'wrong_type', 'wrong_owner', 'capacity', 'signature', 'send'])
def test_upgrade_failure(upgrade_env, failure):
    binary, rpc, live, wallet, mocks, tx = upgrade_env
    if failure == 'dead':
        live['status'] = 'dead'
    elif failure == 'no_type':
        live['cell']['output']['type'] = None
    elif failure == 'wrong_type':
        live['cell']['output']['type']['code_hash'] = '0x' + '00' * 32
    elif failure == 'wrong_owner':
        live['cell']['output']['lock']['args'] = '0x' + '33' * 20
    elif failure == 'capacity':
        binary.write_bytes(b'x' * 1000)
    elif failure == 'signature':
        mocks['tx_sign_inputs'].return_value = []
    else:
        mocks['tx_send'].side_effect = RuntimeError('send failed')
    with pytest.raises((ValueError, RuntimeError)):
        contract.upgrade_ckb_type_contract('key', binary, 'old')
    if failure != 'send':
        mocks['tx_send'].assert_not_called()
    if mocks['tx_init'].called:
        assert not Path(mocks['tx_init'].call_args.args[0]).exists()


@pytest.mark.parametrize('fee', [-1, 1.5, True])
def test_upgrade_invalid_fee(upgrade_env, fee):
    binary, rpc, *_ = upgrade_env
    with pytest.raises(ValueError):
        contract.upgrade_ckb_type_contract('key', binary, 'old', fee=fee)
    rpc.get_live_cell.assert_not_called()


def test_upgrade_exact_capacity(upgrade_env):
    binary, rpc, live, wallet, mocks, tx = upgrade_env
    live['cell']['output']['capacity'] = hex((126 + len(binary.read_bytes())) * 100000000 + 1000)
    contract.upgrade_ckb_type_contract('key', binary, 'old')
    wallet.assert_not_called()
    assert int(tx['outputs'][0]['capacity'], 16) == 13400000000
