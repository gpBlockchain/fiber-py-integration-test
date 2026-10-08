"""PR #1656: standalone Watchtower registration, bitmap validation and reload."""

import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import socket
import subprocess
import threading
import time

import requests

from framework.config import (
    DEFAULT_MIN_DEPOSIT_CKB,
    DEFAULT_MIN_LEDGER_DEPOSIT_CKB,
)
from framework.helper.settlement_witness import assert_commitment_args
from framework.onchain_tlc_query import onchain_tlc_query_enabled
from framework.test_fiber import FiberConfigPath
from framework.util import ckb_hash
from test_cases.fiber.devnet.compatibility.contract_upgrade_support import (
    CKB,
    ROOT,
    ContractUpgradeSupport,
)


class WatchtowerRpcRecorder(BaseHTTPRequestHandler):
    """Transparent localhost recorder: forward original bytes/headers, never alter fields."""

    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        try:
            request = json.loads(raw)
            suppress_registration = (
                request["method"] == "create_watch_channel"
                and self.server.suppress_next_registration
            )
            if suppress_registration:
                # Let the old client finish opening its real channel, but do
                # not install its omitted-field registration. The direct 0x0
                # RPC below is then the only registration in the tower store.
                self.server.suppress_next_registration = False
                result = {"jsonrpc": "2.0", "id": request["id"], "result": None}
                response_bytes = json.dumps(result).encode()
                status_code = 200
            else:
                response = requests.post(
                    "http://127.0.0.1:21300",
                    data=raw,
                    headers={
                        k: v
                        for k, v in self.headers.items()
                        if k.lower() not in ("host", "connection")
                    },
                    timeout=10,
                )
                result = response.json()
                response_bytes = response.content
                status_code = response.status_code
            # Retain the caller's authentication for same-node RPC controls.
            # Keep it in memory only; the forwarded wire request stays unchanged.
            request["_headers"] = {
                k: v
                for k, v in self.headers.items()
                if k.lower() not in ("host", "connection", "content-length")
            }
            request["_suppressed"] = suppress_registration
            self.server.records.append((request, result))
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)
        except requests.RequestException:
            self.send_error(502)

    def log_message(self, *_args):
        pass  # Channel/private settlement keys stay in memory, not HTTP access logs.


class TestFullHashStandaloneWatchtower(ContractUpgradeSupport):
    ckb_rpc_port, ckb_p2p_port = 21214, 21215
    fiber1_rpc_port, fiber1_p2p_port = 21228, 21227
    fiber2_rpc_port, fiber2_p2p_port = 21229, 21230
    extra_fiber_rpc_port, extra_fiber_p2p_port = 21300, 21400
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 2}
    shared_fiber2_extra_config = {
        "fiber_disable_built_in_watchtower": "true",
        "fiber_standalone_watchtower_rpc_url": "http://127.0.0.1:21500",
    }

    @classmethod
    def setup_class(cls):
        for port in (
            21214,
            21215,
            21228,
            21227,
            21229,
            21230,
            21300,
            21301,
            21302,
            21303,
            21304,
            21400,
            21401,
            21402,
            21403,
            21404,
            21500,
        ):
            with socket.socket() as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("127.0.0.1", port))
        candidate_version = subprocess.check_output(
            [ROOT / cls.fiber_version.fiber_bin_path, "--version"], text=True
        )
        assert "5d30078" in candidate_version, candidate_version
        # 旧节点二进制必须固定（独立 Watchtower 的 Legacy 注册对照组依赖它）。
        assert "9a561b3" in subprocess.check_output(
            [ROOT / FiberConfigPath.V091_DEV.fiber_bin_path, "--version"], text=True
        )
        cls.recorder = ThreadingHTTPServer(("127.0.0.1", 21500), WatchtowerRpcRecorder)
        cls.recorder.records = []
        cls.recorder.suppress_next_registration = False
        cls.recorder_thread = threading.Thread(
            target=cls.recorder.serve_forever, daemon=True
        )
        cls.recorder_thread.start()
        try:
            super().setup_class()
        except BaseException:
            cls.recorder.shutdown()
            cls.recorder.server_close()
            raise
        cls.sender, cls.new_receiver = cls.fiber1, cls.fiber2
        cls.ckb = cls.node.getClient()
        cls.processes = None

    def setUp(self):
        cls = self.__class__
        if not hasattr(cls, "tower"):
            cls.tower = self.start_new_fiber(
                self.generate_account(5000), fiber_version=self.fiber_version
            )
            cls.old_receiver = self.start_new_fiber(
                self.generate_account(5000),
                fiber_version=FiberConfigPath.V091_DEV,
                config=dict(
                    cls.shared_fiber2_extra_config, ckb_rpc_url=cls.node.rpcUrl
                ),
            )
            cls.old_sender = self.start_new_fiber(
                self.generate_account(5000),
                fiber_version=FiberConfigPath.V091_DEV,
            )
            cls.processes = cls.node_processes()

    @classmethod
    def node_processes(cls):
        records = super().node_processes()
        for fiber in cls.new_fibers:
            port = fiber.rpc_port
            pid = subprocess.check_output(
                ["lsof", "-nP", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"], text=True
            ).strip()
            started = subprocess.check_output(
                ["ps", "-p", pid, "-o", "lstart="], text=True
            ).strip()
            records.append((pid, started))
        return records

    def channel_calls(self, method):
        return [
            (r, result)
            for r, result in self.recorder.records
            if r["method"] == method
            and r["params"][0].get("channel_id") == self.channel_id
        ]

    def _post_recorded_rpc(self, recorded, method, params):
        """Replay a real registration under the same caller's authorization."""
        wire = {
            key: copy.deepcopy(value)
            for key, value in recorded.items()
            if key not in ("_headers", "_suppressed")
        }
        wire["method"] = method
        wire["params"] = [params]
        wire["id"] = secrets.randbelow(2**31)
        response = requests.post(
            "http://127.0.0.1:21500",
            json=wire,
            headers=recorded["_headers"],
            timeout=10,
        )
        response.raise_for_status()
        return response.json()

    def _run_registered_channel(self, sender, receiver, version, registration):
        """Exercise one real channel and two successive commitment-cell spends."""
        self.sender, self.fiber1, self.fiber2 = sender, sender, receiver
        self.fibers = [sender, receiver]
        self.commitment_version = version
        if registration == "explicit_zero":
            self.recorder.suppress_next_registration = True
        self.channel_id = self.open_channel(sender, receiver, 1000 * CKB, 0)
        assert not self.recorder.suppress_next_registration
        channels = [self.channel(fiber) for fiber in self.fibers]
        raw = bytes.fromhex(channels[0]["channel_outpoint"][2:])
        assert raw[32:] == bytes(4)
        self.funding_tx = "0x" + raw[:32].hex()
        reserve = (
            DEFAULT_MIN_DEPOSIT_CKB
            if version == "v1"
            else DEFAULT_MIN_LEDGER_DEPOSIT_CKB
        )
        self.principals = [
            int(channel["local_balance"], 16) + reserve for channel in channels
        ]
        # The standalone tower signs the watched receiver's payouts.
        self.account1, self.account2 = sender.get_account(), self.tower.get_account()
        self.wallet_before = self.wallet_balances()
        code_tx = self.current_contract_code_tx()

        for _ in range(30):
            registrations = self.channel_calls("create_watch_channel")
            if registrations:
                break
            time.sleep(1)
        else:
            self.fail("real channel was not registered with the standalone tower")
        assert all("error" not in result for _, result in registrations), registrations
        recorded = registrations[-1][0]
        params = recorded["params"][0]
        if version == "v1":
            assert params["commitment_contract_features"] == "0x1", params
        else:
            assert "commitment_contract_features" not in params, params
            assert "commitment_contract_version" not in params, params
            if registration == "explicit_zero":
                assert recorded["_suppressed"] is True
                explicit = copy.deepcopy(params)
                explicit["commitment_contract_features"] = "0x0"
                created = self._post_recorded_rpc(
                    recorded, "create_watch_channel", explicit
                )
                assert "error" not in created, created
                assert (
                    self.channel_calls("create_watch_channel")[-1][0]["params"][0][
                        "commitment_contract_features"
                    ]
                    == "0x0"
                )
            else:
                assert recorded["_suppressed"] is False

        if version == "v1":
            # H32V2-37: unknown bitmap must fail before replacing the same
            # authenticated node_id + channel_id. The subsequent real spend
            # proves the original V1 registration is still usable.
            invalid = copy.deepcopy(params)
            invalid["commitment_contract_features"] = "0x2"
            rejected = self._post_recorded_rpc(
                recorded, "create_watch_channel", invalid
            )
            assert "error" in rejected, rejected

        # Freeze the create-call count before snapshot updates. Otherwise an
        # unexpected later valid registration could mask a destructive reject
        # or overwrite the direct explicit-zero registration.
        registration_count = len(self.channel_calls("create_watch_channel"))

        payments = []
        for _ in range(2):
            preimage = "0x" + secrets.token_hex(32)
            payment_hash = ckb_hash(preimage)
            invoice = receiver.get_client().new_invoice(
                {
                    "amount": hex(CKB),
                    "currency": "Fibd",
                    "payment_hash": payment_hash,
                    "hash_algorithm": "ckb_hash",
                    "final_expiry_delta": hex(9_600_000),
                }
            )
            sender.get_client().send_payment({"invoice": invoice["invoice_address"]})
            self.wait_invoice_state(receiver, payment_hash, "Received")
            payments.append((payment_hash, preimage))

        expected_hashes = {payment_hash for payment_hash, _ in payments}
        for _ in range(90):
            channels = [self.channel(fiber) for fiber in self.fibers]
            updates = self.channel_calls(
                "update_pending_remote_settlement"
            ) + self.channel_calls("update_revocation")
            delivered = any(
                "error" not in result
                and expected_hashes.issubset(
                    {
                        tlc["payment_hash"]
                        for tlc in request["params"][0]["settlement_data"]["tlcs"]
                    }
                )
                for request, result in updates
            )
            committed = all(
                len(channel["pending_tlcs"]) == 2
                and all(
                    "Committed" in tlc["status"].values()
                    for tlc in channel["pending_tlcs"]
                )
                for channel in channels
            )
            if delivered and committed:
                break
            time.sleep(1)
        else:
            self.fail("two committed TLCs did not reach standalone tower via RPC")
        self.signed_hashes = [
            channel["latest_commitment_transaction_hash"] for channel in channels
        ]

        assert (
            len(self.channel_calls("create_watch_channel")) == registration_count
        ), "registration changed between validation and tower restart"
        before = self.processes
        self.tower.stop()
        self.tower.start(fnn_log_level=self.fnn_log_level)
        after = self.node_processes()
        assert after[:3] == before[:3] and after[4:] == before[4:]
        assert after[3] != before[3], "only the standalone tower must restart"
        self.__class__.processes = after

        commitment = self.force_close(sender)
        args = bytes.fromhex(commitment["outputs"][0]["lock"]["args"][2:])
        assert_commitment_args(args, version)
        self.ckb.generate_epochs("0x1")
        previous = commitment
        pending = [(payment_hash, CKB) for payment_hash, _ in payments]
        prior_fees = self.get_tx_message(commitment["hash"])["fee"]
        for payment_hash, preimage in payments:
            receiver.get_client().settle_invoice(
                {"payment_hash": payment_hash, "payment_preimage": preimage}
            )
            spent = self.wait_for_spend(previous["hash"])
            self.assert_tlc_settlement(previous, spent, code_tx, pending, preimage)
            self.principals[0] -= CKB
            self.principals[1] += CKB
            if len(pending) > 1:
                prior_fees += self.get_tx_message(spent["hash"])["fee"]
            pending = [item for item in pending if item[0] != payment_hash]
            previous = spent
        self.assert_settled(previous, code_tx, prior_fees)
        assert (
            len(self.channel_calls("create_watch_channel")) == registration_count
        ), "tower restart must reload registration rather than request it again"

        # The chain and balances are always checked. Query finality is opt-in
        # because node on-chain scans can take several minutes in CI.
        for payment_hash, preimage in payments:
            payment = sender.get_client().get_payment({"payment_hash": payment_hash})
            if onchain_tlc_query_enabled():
                self.wait_payment_state(sender, payment_hash, "Success", timeout=660)
                assert (
                    sender.get_client().get_payment({"payment_hash": payment_hash})[
                        "payment_preimage"
                    ]
                    == preimage
                )
            else:
                assert payment["status"] != "Failed", payment

    # TEST-MAP: H32V2-12
    # TEST-MAP: H32V2-37
    # TEST-EVIDENCE-BEGIN: H32V2-12
    # Evidence | partial when FIBER_ASSERT_ONCHAIN_TLC_QUERY is false: chain and
    # balances are proved, while final payment/TLC queries require the opt-in.
    # TEST-EVIDENCE-END: H32V2-12
    def test_v1_registration_reloads_and_rejects_unknown_bitmap(self):
        self._run_registered_channel(
            self.sender, self.new_receiver, "v1", registration="explicit_one"
        )

    # TEST-MAP: H32V2-39
    # TEST-EVIDENCE-BEGIN: H32V2-39
    # Evidence | partial when FIBER_ASSERT_ONCHAIN_TLC_QUERY is false: chain and
    # balances are proved, while final payment/TLC queries require the opt-in.
    # TEST-EVIDENCE-END: H32V2-39
    def test_v091_legacy_omitted_and_explicit_zero_reload(self):
        self._run_registered_channel(
            self.old_sender, self.old_receiver, "legacy", "omitted"
        )
        # A fresh old-old peer pair keeps the second invoice from selecting the
        # just-force-closed first channel as its first hop.
        explicit_receiver = self.start_new_fiber(
            self.generate_account(5000),
            fiber_version=FiberConfigPath.V091_DEV,
            config=dict(self.shared_fiber2_extra_config, ckb_rpc_url=self.node.rpcUrl),
        )
        explicit_sender = self.start_new_fiber(
            self.generate_account(5000), fiber_version=FiberConfigPath.V091_DEV
        )
        # The helper uses a per-test `self.fibers` list for channel assertions;
        # SharedFiberTest cleans only the class-level list at teardown.
        self.__class__.fibers.extend((explicit_receiver, explicit_sender))
        self.__class__.processes = self.node_processes()
        self._run_registered_channel(
            explicit_sender, explicit_receiver, "legacy", "explicit_zero"
        )

    @classmethod
    def teardown_class(cls):
        try:
            super().teardown_class()
        finally:
            cls.recorder.shutdown()
            cls.recorder.server_close()
            cls.recorder_thread.join(timeout=5)
