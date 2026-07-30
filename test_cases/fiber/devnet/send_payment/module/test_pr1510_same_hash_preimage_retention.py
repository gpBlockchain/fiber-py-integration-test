"""PR-1510: retain a preimage while an independent same-hash TLC is pending."""

import http.server
import json
import threading
import time

from framework.basic_fiber import FiberTest
from framework.util import ckb_hash


class WatchtowerRpcRecorder:
    """Accept Watchtower JSON-RPC calls and record their method and parameters."""

    def __init__(self):
        self._calls = []
        self._condition = threading.Condition()
        self._server = None
        self._thread = None
        self.url = None

    def start(self):
        recorder = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                content_length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(content_length))
                requests = payload if isinstance(payload, list) else [payload]

                with recorder._condition:
                    recorder._calls.extend(requests)
                    recorder._condition.notify_all()

                responses = [
                    {"jsonrpc": "2.0", "id": request.get("id"), "result": None}
                    for request in requests
                ]
                response = responses if isinstance(payload, list) else responses[0]
                body = json.dumps(response).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                pass

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        self._server = None
        self._thread = None

    def count(self, method, payment_hash):
        def contains_hash(value):
            if isinstance(value, dict):
                return any(contains_hash(item) for item in value.values())
            if isinstance(value, list):
                return any(contains_hash(item) for item in value)
            return value == payment_hash

        with self._condition:
            return sum(
                call.get("method") == method and contains_hash(call.get("params", []))
                for call in self._calls
            )

    def wait_for_count(self, method, payment_hash, count, timeout):
        deadline = time.time() + timeout
        with self._condition:
            while time.time() < deadline:
                if self.count(method, payment_hash) >= count:
                    return True
                self._condition.wait(deadline - time.time())
        return False


class TestSameHashPreimageRetention(FiberTest):
    def test_retains_preimage_until_all_offchain_tlcs_finish(self):
        recorder = WatchtowerRpcRecorder()
        recorder.start()

        try:
            # Observe fiber2's preimage lifecycle through the same Watchtower
            # interface used by a real standalone Watchtower.
            self.fiber2.stop()
            self.fiber2.prepare(
                {
                    "fiber_standalone_watchtower_rpc_url": recorder.url,
                    "fiber_disable_built_in_watchtower": "true",
                }
            )
            self.fiber2.start()

            payee_one = self.start_new_fiber(self.generate_account(10000))
            payer_two = self.start_new_fiber(self.generate_account(10000))
            payee_two = self.start_new_fiber(self.generate_account(10000))

            # Two independent payments cross fiber2. Their senders and payees
            # are different, but both invoices deliberately use the same hash.
            channel_payer_one_mid = self.open_channel(
                self.fiber1, self.fiber2, 1000 * 100000000, 0
            )
            channel_mid_payee_one = self.open_channel(
                self.fiber2, payee_one, 1000 * 100000000, 0
            )
            channel_payer_two_mid = self.open_channel(
                payer_two, self.fiber2, 1000 * 100000000, 0
            )
            channel_mid_payee_two = self.open_channel(
                self.fiber2, payee_two, 1000 * 100000000, 0
            )
            self.wait_graph_channels_sync(self.fiber1, 4, timeout=120)
            self.wait_graph_channels_sync(payer_two, 4, timeout=120)

            preimage = self.generate_random_preimage()
            payment_hash = ckb_hash(preimage)
            invoice_one = payee_one.get_client().new_invoice(
                {
                    "amount": hex(10 * 100000000),
                    "currency": "Fibd",
                    "description": "PR-1510 same-hash payment one",
                    "payment_hash": payment_hash,
                    "hash_algorithm": "ckb_hash",
                    "expiry": "0xe10",
                    "final_cltv": "0x28",
                }
            )
            invoice_two = payee_two.get_client().new_invoice(
                {
                    "amount": hex(10 * 100000000),
                    "currency": "Fibd",
                    "description": "PR-1510 same-hash payment two",
                    "payment_hash": payment_hash,
                    "hash_algorithm": "ckb_hash",
                    "expiry": "0xe10",
                    "final_cltv": "0x28",
                }
            )
            payment_one = self.fiber1.get_client().send_payment(
                {"invoice": invoice_one["invoice_address"]}
            )
            payment_two = payer_two.get_client().send_payment(
                {"invoice": invoice_two["invoice_address"]}
            )
            assert payment_one["payment_hash"] == payment_hash
            assert payment_two["payment_hash"] == payment_hash
            self.wait_invoice_state(payee_one, payment_hash, "Received", timeout=120)
            self.wait_invoice_state(payee_two, payment_hash, "Received", timeout=120)

            all_channel_ids = (
                channel_payer_one_mid,
                channel_mid_payee_one,
                channel_payer_two_mid,
                channel_mid_payee_two,
            )
            deadline = time.time() + 30
            while time.time() < deadline:
                channels = {
                    channel["channel_id"]: channel
                    for channel in self.fiber2.get_client().list_channels({})[
                        "channels"
                    ]
                }
                channels_ready = all(
                    channels[channel_id]["state"]["state_name"] == "ChannelReady"
                    for channel_id in all_channel_ids
                )
                pending_by_channel = {
                    channel_id: [
                        tlc
                        for tlc in channels[channel_id].get("pending_tlcs", [])
                        if tlc["payment_hash"] == payment_hash
                    ]
                    for channel_id in all_channel_ids
                }
                all_tlcs_committed = all(
                    len(pending_by_channel[channel_id]) == 1
                    and "Committed"
                    in pending_by_channel[channel_id][0]["status"].values()
                    for channel_id in all_channel_ids
                )
                if all_tlcs_committed and channels_ready:
                    break
                time.sleep(0.5)
            else:
                assert False, "same-hash TLCs were not pending on all four channels"

            payee_one.get_client().settle_invoice(
                {"payment_hash": payment_hash, "payment_preimage": preimage}
            )

            first_payment_channels = (
                channel_payer_one_mid,
                channel_mid_payee_one,
            )
            second_payment_channels = (
                channel_payer_two_mid,
                channel_mid_payee_two,
            )
            deadline = time.time() + 30
            while time.time() < deadline:
                channels = {
                    channel["channel_id"]: channel
                    for channel in self.fiber2.get_client().list_channels({})[
                        "channels"
                    ]
                }
                pending_by_channel = {
                    channel_id: [
                        tlc
                        for tlc in channels[channel_id].get("pending_tlcs", [])
                        if tlc["payment_hash"] == payment_hash
                    ]
                    for channel_id in all_channel_ids
                }
                first_payment_cleared = all(
                    not pending_by_channel[channel_id]
                    for channel_id in first_payment_channels
                )
                second_payment_still_pending = all(
                    len(pending_by_channel[channel_id]) == 1
                    and "Committed"
                    in pending_by_channel[channel_id][0]["status"].values()
                    for channel_id in second_payment_channels
                )
                channels_ready = all(
                    channels[channel_id]["state"]["state_name"] == "ChannelReady"
                    for channel_id in all_channel_ids
                )
                if (
                    first_payment_cleared
                    and second_payment_still_pending
                    and channels_ready
                ):
                    break
                time.sleep(0.5)
            else:
                assert False, (
                    "first payment did not settle while the independent "
                    "same-hash payment stayed pending"
                )

            assert recorder.wait_for_count(
                "create_preimage", payment_hash, 1, timeout=5
            ), "fiber2 did not publish the fulfilled TLC preimage"
            assert not recorder.wait_for_count(
                "remove_preimage", payment_hash, 1, timeout=2
            ), (
                "fiber2 removed the preimage while another off-chain channel "
                "still had a pending TLC with the same payment hash"
            )

            payee_two.get_client().settle_invoice(
                {"payment_hash": payment_hash, "payment_preimage": preimage}
            )

            deadline = time.time() + 30
            while time.time() < deadline:
                channels = self.fiber2.get_client().list_channels({})["channels"]
                if all(
                    payment_hash
                    not in [
                        tlc["payment_hash"] for tlc in channel.get("pending_tlcs", [])
                    ]
                    for channel in channels
                ):
                    break
                time.sleep(0.5)
            else:
                assert False, "second same-hash TLC did not settle"

            assert recorder.wait_for_count(
                "remove_preimage", payment_hash, 1, timeout=5
            ), "fiber2 did not remove the preimage after the last TLC finished"
            assert recorder.count("remove_preimage", payment_hash) == 1
            self.wait_payment_state(self.fiber1, payment_hash, "Success", timeout=60)
            self.wait_payment_state(payer_two, payment_hash, "Success", timeout=60)
        finally:
            recorder.stop()
