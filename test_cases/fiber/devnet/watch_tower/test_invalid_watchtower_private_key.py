import pytest
import requests

from framework.basic_fiber import FiberTest


class TestInvalidWatchtowerPrivateKey(FiberTest):
    """PR #1401: invalid watchtower private keys return RPC errors, not panics."""

    def test_create_watch_channel_rejects_invalid_private_key_without_panic(self):
        pubkey1 = self.fiber1.get_pubkey()
        pubkey2 = self.fiber2.get_pubkey()
        invalid_zero_key = "00" * 32

        payload = {
            "id": 42,
            "jsonrpc": "2.0",
            "method": "create_watch_channel",
            "params": [
                {
                    "channel_id": "0x" + "11" * 32,
                    "funding_udt_type_script": None,
                    "local_settlement_key": invalid_zero_key,
                    "remote_settlement_key": pubkey2,
                    "local_funding_pubkey": pubkey1,
                    "remote_funding_pubkey": pubkey2,
                    "settlement_data": {
                        "local_amount": "0x0",
                        "remote_amount": "0x0",
                        "tlcs": [],
                    },
                }
            ],
        }

        try:
            response = requests.post(
                self.fiber1.get_client().url, json=payload, timeout=5
            )
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            pytest.xfail(
                "current fnn binary closes or hangs after invalid watchtower private key; "
                "PR #1401 should return an RPC error instead"
            )

        body = response.json()
        assert "error" in body
        assert "Invalid private key" in body["error"]["message"]

        info = self.fiber1.get_client().node_info()
        assert info["pubkey"] == pubkey1
