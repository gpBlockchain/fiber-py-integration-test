import pytest

from framework import basic_fiber_with_cch, test_lnd
from framework.basic_fiber import FiberTest
from framework.test_lnd import LndNode


def test_open_channel_retries_while_lnd_wallet_syncs(monkeypatch):
    attempts = []
    sleeps = []
    monkeypatch.setattr(test_lnd.time, "sleep", sleeps.append)

    lnd = LndNode("tmp/lnd/node0", 9735, 10009, 8180)
    peer = LndNode("tmp/lnd/node1", 9736, 11010, 8181)
    monkeypatch.setattr(peer, "getinfo", lambda: {"identity_pubkey": "peer-key"})

    def open_after_wallet_sync(command):
        attempts.append(command)
        if len(attempts) < 3:
            raise Exception(
                "channels cannot be created before the wallet is fully synced"
            )
        return {"funding_txid": "tx-id"}

    monkeypatch.setattr(lnd, "ln_cli_with_cmd", open_after_wallet_sync)

    result = lnd.open_channel(peer, 1_000_000, 1, 0)

    assert result == {"funding_txid": "tx-id"}
    assert len(attempts) == 3
    assert sleeps == [1, 1]


def test_failed_cch_setup_cleans_started_services(monkeypatch):
    events = []

    class FakeCkbNode:
        def stop(self):
            events.append("ckb.stop")

        def clean(self):
            events.append("ckb.clean")

    class FakeBtcNode:
        def prepare(self):
            events.append("btc.prepare")

        def start(self):
            events.append("btc.start")

        def sendtoaddress(self, _address, _amount, _fee_rate):
            events.append("btc.sendtoaddress")

        def miner(self, _blocks):
            events.append("btc.miner")

        def stop(self):
            events.append("btc.stop")

        def clean(self):
            events.append("btc.clean")

    class FakeLndNode:
        instances = []

        def __init__(self, *_args):
            self.index = len(self.instances)
            self.instances.append(self)

        def prepare(self, _config):
            events.append(f"lnd{self.index}.prepare")

        def start(self):
            events.append(f"lnd{self.index}.start")

        def ln_cli_with_cmd(self, command):
            assert command == "newaddress p2tr"
            return {"address": "bcrt1-test"}

        def open_channel(self, *_args):
            raise RuntimeError("channel setup failed")

        def stop(self):
            events.append(f"lnd{self.index}.stop")

        def clean(self):
            events.append(f"lnd{self.index}.clean")

    def fake_fiber_setup(test_class):
        test_class.node = FakeCkbNode()

    monkeypatch.setattr(FiberTest, "setup_class", classmethod(fake_fiber_setup))
    monkeypatch.setattr(basic_fiber_with_cch, "BtcNode", FakeBtcNode)
    monkeypatch.setattr(basic_fiber_with_cch, "LndNode", FakeLndNode)

    class FailingCchTest(basic_fiber_with_cch.FiberCchTest):
        pass

    with pytest.raises(RuntimeError, match="channel setup failed"):
        FailingCchTest.setup_class()

    for cleanup_event in (
        "lnd0.stop",
        "lnd0.clean",
        "lnd1.stop",
        "lnd1.clean",
        "btc.stop",
        "btc.clean",
        "ckb.stop",
        "ckb.clean",
    ):
        assert cleanup_event in events
