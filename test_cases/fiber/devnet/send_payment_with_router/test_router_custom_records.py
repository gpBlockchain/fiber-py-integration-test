"""Custom records on explicitly routed payments."""

import pytest

from framework.basic_share_fiber import SharedFiberTest


CKB = 100000000
AMOUNT = CKB
_OMITTED = object()


class TestRouterCustomRecords(SharedFiberTest):
    """Reuse one A-B-C topology; each payment uses a fresh keysend hash."""

    tmp_path_name = "tmp/router-custom-records"
    ckb_rpc_port = 8720
    ckb_p2p_port = 8721
    fiber1_rpc_port = 8722
    fiber1_p2p_port = 8723
    fiber2_rpc_port = 8724
    fiber2_p2p_port = 8725
    extra_fiber_rpc_port = 8726
    extra_fiber_p2p_port = 8727

    def setUp(self):
        if getattr(type(self), "_channels_ready", False):
            return

        self.__class__.fiber3 = self.start_new_fiber(self.generate_account(10000))
        self.open_channel(self.fiber1, self.fiber2, 1000 * CKB, 0)
        self.open_channel(self.fiber2, self.fiber3, 1000 * CKB, 0)
        self.wait_graph_channels_sync(self.fiber1, 2, timeout=120)

        self.__class__.outpoint_ab = self._channel(self.fiber1, self.fiber2)[
            "channel_outpoint"
        ]
        self.__class__.outpoint_bc = self._channel(self.fiber2, self.fiber3)[
            "channel_outpoint"
        ]
        self.__class__._channels_ready = True

    def _channel(self, source, target):
        channels = source.get_client().list_channels(
            {"pubkey": target.get_pubkey()}
        )["channels"]
        assert len(channels) == 1
        return channels[0]

    def _route(self):
        return self.fiber1.get_client().build_router(
            {
                "amount": hex(AMOUNT),
                "hops_info": [
                    {"pubkey": self.fiber2.get_pubkey(), "channel_outpoint": self.outpoint_ab},
                    {"pubkey": self.fiber3.get_pubkey(), "channel_outpoint": self.outpoint_bc},
                ],
            }
        )["router_hops"]

    def _keysend(self, records=_OMITTED, dry_run=False, route=None):
        params = {
            "router": self._route() if route is None else route,
            "keysend": True,
            "dry_run": dry_run,
        }
        if records is not _OMITTED:
            params["custom_records"] = records
        return self.fiber1.get_client().send_payment_with_router(params)

    def _snapshot(self):
        # The RPC returns only 15 rows by default. Keep this isolated class below
        # the explicit 500-row maximum so the hash set is a complete snapshot.
        payments = self.fiber1.get_client().list_payments({"limit": hex(500)})[
            "payments"
        ]
        assert len(payments) < 500
        channels = (
            self._channel(self.fiber1, self.fiber2),
            self._channel(self.fiber2, self.fiber3),
        )
        return (
            {payment["payment_hash"] for payment in payments},
            tuple(
                (
                    channel["local_balance"],
                    channel["remote_balance"],
                    channel["offered_tlc_balance"],
                    channel["received_tlc_balance"],
                    channel.get("pending_tlcs"),
                )
                for channel in channels
            ),
        )

    # TEST-MAP: PR1675-09
    def test_keysend_response_and_query_keep_records(self):
        records = {"0x1": "0x68656c6c6f", "0x2": "0x776f726c64"}
        payment = self._keysend(records)
        assert payment["custom_records"] == records

        self.wait_payment_state(self.fiber1, payment["payment_hash"], "Success", timeout=120)
        stored = self.fiber1.get_client().get_payment(
            {"payment_hash": payment["payment_hash"]}
        )
        assert stored["custom_records"] == records

    # TEST-MAP: PR1675-03
    def test_encoded_size_exactly_2048_succeeds(self):
        # One record has 36 bytes of Molecule overhead: 2012 + 36 = 2048.
        records = {"0x12": "0x" + "ab" * 2012}
        assert len(bytes.fromhex(records["0x12"][2:])) + 36 == 2048
        payment = self._keysend(records)
        self.wait_payment_state(self.fiber1, payment["payment_hash"], "Success", timeout=120)
        stored = self.fiber1.get_client().get_payment(
            {"payment_hash": payment["payment_hash"]}
        )
        assert stored["custom_records"] == records

    # TEST-MAP: PR1675-04
    def test_encoded_size_2049_is_rejected_before_dispatch(self):
        # This separates the RPC comment's value-byte limit from the encoded limit.
        records = {"0x12": "0x" + "ab" * 2013}
        route = self._route()
        control = self._keysend({"0x12": "0x01"}, dry_run=True, route=route)
        assert control["custom_records"] == {"0x12": "0x01"}
        for dry_run in (False, True):
            before = self._snapshot()
            with pytest.raises(Exception) as error:
                self._keysend(records, dry_run=dry_run, route=route)
            assert "InvalidParameter" in str(error.value)
            assert "custom_records" in str(error.value)
            print(f"encoded-size rejection with dry_run={dry_run}: {str(error.value)[:180]}")
            assert self._snapshot() == before

    # TEST-MAP: PR1675-05
    def test_reserved_custom_record_key_is_rejected(self):
        route = self._route()
        control = self._keysend({"0xffff": "0x01"}, dry_run=True, route=route)
        assert control["custom_records"] == {"0xffff": "0x01"}
        before = self._snapshot()
        with pytest.raises(Exception) as error:
            self._keysend({"0x10000": "0x01"}, route=route)
        assert "InvalidParameter" in str(error.value)
        assert "custom_records" in str(error.value)
        assert self._snapshot() == before

    # TEST-MAP: PR1675-06
    def test_omitted_custom_records_remain_null(self):
        payment = self._keysend()
        assert payment["custom_records"] is None

        self.wait_payment_state(self.fiber1, payment["payment_hash"], "Success", timeout=120)
        stored = self.fiber1.get_client().get_payment(
            {"payment_hash": payment["payment_hash"]}
        )
        assert stored["custom_records"] is None

    # TEST-MAP: PR1675-07
    def test_empty_custom_records_remain_empty(self):
        payment = self._keysend({})
        self.wait_payment_state(self.fiber1, payment["payment_hash"], "Success", timeout=120)
        stored = self.fiber1.get_client().get_payment(
            {"payment_hash": payment["payment_hash"]}
        )
        print(
            "empty-record result:",
            payment["custom_records"],
            stored["custom_records"],
        )
        assert payment["custom_records"] == {}
        assert stored["custom_records"] == {}

    # TEST-MAP: PR1675-08
    def test_dry_run_keeps_records_without_storing_payment(self):
        records = {"0x1": "0x01020304"}
        before = self._snapshot()
        payment = self._keysend(records, dry_run=True)
        assert payment["custom_records"] == records
        assert payment["payment_hash"]

        with pytest.raises(Exception) as error:
            self.fiber1.get_client().get_payment(
                {"payment_hash": payment["payment_hash"]}
            )
        assert "Payment session not found" in str(error.value)
        assert self._snapshot() == before
