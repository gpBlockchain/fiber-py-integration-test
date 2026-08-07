import time

from framework.basic_fiber import FiberTest
from framework.util import ckb_hash


class TestForceCloseFulfill(FiberTest):
    """
    Regression tests for nervosnetwork/fiber PR #1254.

    When a channel is force-closed and the channel actor is gone, a later
    RemoveTlc(Fulfill) from the peer should still be handled through persisted
    channel state, so the payer payment does not stay Inflight forever.
    """

    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 3}

    def _wait_force_close_unlock(self, timeout=600):
        if len(self.get_commit_cells()) == 0:
            raise Exception("No commit cells found")
        self.node.getClient().generate_epochs("0x1", wait_time=0)
        for _ in range(timeout // 10):
            if len(self.get_commit_cells()) == 0:
                return
            time.sleep(10)
        assert len(self.get_commit_cells()) == 0

    def _get_channel_id(self, local, remote, udt=None, timeout=120):
        for _ in range(timeout):
            channels = local.get_client().list_channels(
                {"pubkey": remote.get_pubkey()}
            )["channels"]
            for channel in channels:
                if channel.get("funding_udt_type_script") != udt:
                    continue
                if channel["state"]["state_name"] == "ChannelReady":
                    return channel["channel_id"]
            time.sleep(1)
        raise TimeoutError(f"ChannelReady not found, udt={udt}")

    def _get_tlc_status(self, fiber, remote_pubkey, channel_id, payment_hash):
        channels = fiber.get_client().list_channels(
            {
                "pubkey": remote_pubkey,
                "include_closed": True,
            }
        )["channels"]
        for channel in channels:
            if channel["channel_id"] != channel_id:
                continue
            for tlc in channel.get("pending_tlcs", []):
                if tlc["payment_hash"] == payment_hash:
                    return tlc["status"]
        raise AssertionError(
            f"TLC {payment_hash} not found in channel {channel_id} on {fiber.get_pubkey()}"
        )

    def _assert_sender_remote_removed(self, sender, receiver, channel_id, payment_hash):
        status = self._get_tlc_status(
            sender,
            receiver.get_pubkey(),
            channel_id,
            payment_hash,
        )
        assert status == {"Outbound": "RemoteRemoved"}

    def _assert_receiver_local_removed(
        self, receiver, sender, channel_id, payment_hash
    ):
        status = self._get_tlc_status(
            receiver,
            sender.get_pubkey(),
            channel_id,
            payment_hash,
        )
        assert status == {"Inbound": "LocalRemoved"}

    def _new_hold_invoice(self, payee, payment_hash, description, udt=None):
        params = {
            "amount": hex(1 * 100000000),
            "currency": "Fibd",
            "description": description,
            "payment_hash": payment_hash,
            # "allow_mpp": True,
            "allow_trampoline_routing": True,
        }
        if udt is not None:
            params["udt_type_script"] = udt
        return payee.get_client().new_invoice(params)

    def _send_two_hop(self, payee, description, udt=None):
        preimage = self.generate_random_preimage()
        payment_hash = ckb_hash(preimage)
        invoice = self._new_hold_invoice(payee, payment_hash, description, udt)
        payment = self.fiber1.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "trampoline_hops": [
                    self.fiber2.get_client().node_info()["pubkey"],
                ],
            }
        )
        assert payment["payment_hash"] == payment_hash
        self.wait_payment_state(self.fiber1, payment_hash, "Inflight")
        self.wait_invoice_state(payee, payment_hash, "Received")
        return payment_hash, preimage

    def _send_one_hop(self, payee, description, udt=None):
        preimage = self.generate_random_preimage()
        payment_hash = ckb_hash(preimage)
        invoice = self._new_hold_invoice(payee, payment_hash, description, udt)
        payment = self.fiber1.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "max_fee_rate": hex(1000000000000000),
            }
        )
        assert payment["payment_hash"] == payment_hash
        self.wait_payment_state(self.fiber1, payment_hash, "Inflight")
        self.wait_invoice_state(payee, payment_hash, "Received")
        return payment_hash, preimage

    def test_force_close_payee_settle_invoice_ckb_and_udt(self):
        """
        Open CKB and UDT A-B/B-C channels first.

        Then send both two-hop payments, send both one-hop payments, force-close
        all affected channels, settle all invoices, and assert final states after
        one shared on-chain unlock wait.
        """
        udt_script = self.get_account_udt_script(self.fiber1.account_private)
        fiber3 = self.start_new_fiber(
            self.generate_account(
                10000,
                self.fiber1.account_private,
                10000 * 100000000,
            )
        )
        self.faucet(
            self.fiber2.account_private,
            0,
            self.fiber1.account_private,
            10000 * 100000000,
        )
        self.faucet(
            self.fiber1.account_private,
            0,
            self.fiber1.account_private,
            10000 * 100000000,
        )

        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0)
        self.open_channel(self.fiber2, fiber3, 1000 * 100000000, 0)
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0, udt=udt_script)
        self.open_channel(self.fiber2, fiber3, 1000 * 100000000, 0, udt=udt_script)

        ckb_bc = self._get_channel_id(self.fiber2, fiber3)
        ckb_ab = self._get_channel_id(self.fiber1, self.fiber2)
        udt_bc = self._get_channel_id(self.fiber2, fiber3, udt_script)
        udt_ab = self._get_channel_id(self.fiber1, self.fiber2, udt_script)

        time.sleep(10)
        ckb_two_hop, ckb_two_hop_preimage = self._send_two_hop(
            fiber3,
            "pr1254 CKB two-hop hold invoice",
        )
        udt_two_hop, udt_two_hop_preimage = self._send_two_hop(
            fiber3,
            "pr1254 UDT two-hop hold invoice",
            udt_script,
        )

        self.fiber2.get_client().shutdown_channel({"channel_id": ckb_bc, "force": True})
        self.fiber2.get_client().shutdown_channel({"channel_id": udt_bc, "force": True})

        ckb_one_hop, ckb_one_hop_preimage = self._send_one_hop(
            self.fiber2,
            "pr1254 CKB one-hop hold invoice",
        )
        udt_one_hop, udt_one_hop_preimage = self._send_one_hop(
            self.fiber2,
            "pr1254 UDT one-hop hold invoice",
            udt_script,
        )

        self.fiber1.get_client().shutdown_channel({"channel_id": ckb_ab, "force": True})
        self.fiber1.get_client().shutdown_channel({"channel_id": udt_ab, "force": True})

        time.sleep(10)
        fiber3.get_client().settle_invoice(
            {"payment_hash": ckb_two_hop, "payment_preimage": ckb_two_hop_preimage}
        )
        fiber3.get_client().settle_invoice(
            {"payment_hash": udt_two_hop, "payment_preimage": udt_two_hop_preimage}
        )
        self.fiber2.get_client().settle_invoice(
            {"payment_hash": ckb_one_hop, "payment_preimage": ckb_one_hop_preimage}
        )
        self.fiber2.get_client().settle_invoice(
            {"payment_hash": udt_one_hop, "payment_preimage": udt_one_hop_preimage}
        )
        self._wait_force_close_unlock()

        self.wait_payment_state(self.fiber1, ckb_two_hop, "Success", timeout=360)
        self.wait_invoice_state(fiber3, ckb_two_hop, "Paid", timeout=360)
        self.wait_payment_state(self.fiber1, udt_two_hop, "Success", timeout=360)
        self.wait_invoice_state(fiber3, udt_two_hop, "Paid", timeout=360)
        self.wait_payment_state(self.fiber1, ckb_one_hop, "Success", timeout=360)
        self.wait_invoice_state(self.fiber2, ckb_one_hop, "Paid", timeout=360)
        self.wait_payment_state(self.fiber1, udt_one_hop, "Success", timeout=360)
        self.wait_invoice_state(self.fiber2, udt_one_hop, "Paid", timeout=360)

        self._assert_sender_remote_removed(self.fiber2, fiber3, ckb_bc, ckb_two_hop)
        self._assert_receiver_local_removed(fiber3, self.fiber2, ckb_bc, ckb_two_hop)
        self._assert_sender_remote_removed(self.fiber2, fiber3, udt_bc, udt_two_hop)
        self._assert_receiver_local_removed(fiber3, self.fiber2, udt_bc, udt_two_hop)
        self._assert_sender_remote_removed(
            self.fiber1,
            self.fiber2,
            ckb_ab,
            ckb_one_hop,
        )
        self._assert_receiver_local_removed(
            self.fiber2,
            self.fiber1,
            ckb_ab,
            ckb_one_hop,
        )
        self._assert_sender_remote_removed(
            self.fiber1,
            self.fiber2,
            udt_ab,
            udt_one_hop,
        )
        self._assert_receiver_local_removed(
            self.fiber2,
            self.fiber1,
            udt_ab,
            udt_one_hop,
        )
