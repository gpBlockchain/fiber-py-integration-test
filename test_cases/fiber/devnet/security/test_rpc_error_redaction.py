import json
from pathlib import Path

import requests

from framework.basic_fiber import FiberTest


class TestRpcErrorRedaction(FiberTest):
    """
    PR-1405 regression: a settle_invoice RPC error must not echo the supplied
    payment_preimage back to the caller, and must not write it to node.log.

    Scope note: jsonrpsee's generic "Invalid params" (-32602) deserialization
    error DOES echo the caller's own malformed input back to that same caller
    (e.g. "failed to decode hex string <input>"). That is the caller's own data
    returned only to the caller and is outside PR-1405's scope, so this test
    deliberately uses a well-formed preimage and checks the handler-level error
    path plus the log, which is what PR-1405 actually hardened.
    """

    def test_rpc_error_omits_request_data_and_preimage(self):
        payment_hash = "0x" + "11" * 32
        payment_preimage = "0x" + "22" * 32
        payload = {
            "id": 1405,
            "jsonrpc": "2.0",
            "method": "settle_invoice",
            "params": [
                {
                    "payment_hash": payment_hash,
                    "payment_preimage": payment_preimage,
                }
            ],
        }

        response = requests.post(
            self.fiber1.get_client().url,
            json=payload,
            headers={"content-type": "application/json"},
            timeout=10,
        )
        response.raise_for_status()
        body = response.json()

        assert "error" in body
        error = body["error"]
        rendered = json.dumps(body)

        # The handler error must not echo the request params or the preimage.
        assert "data" not in error
        assert "params" not in rendered
        assert "payment_preimage" not in rendered
        assert payment_preimage not in rendered

        # The preimage must never be persisted to the log (the real PR-1405 risk).
        self.fiber1.stop()
        node_log = Path(self.fiber1.tmp_path) / "node.log"
        log_text = node_log.read_text(errors="replace")
        assert payment_preimage not in log_text
