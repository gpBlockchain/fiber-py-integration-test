import time

from framework.basic_fiber import FiberTest
from framework.util import ckb_hash


class TestForceCloseMpp(FiberTest):
    """
    Regression coverage for nervosnetwork/fiber PR #1335.

    MPP payments can create multiple TLCs with the same payment_hash across
    channels. If one split goes on-chain during force close and another split is
    fulfilled off-chain, the forwarding node must keep the preimage until the
    on-chain split is settled.
    """

    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 3}

    def _wait_until(self, predicate, description, timeout=120, interval=1):
        for _ in range(timeout):
            if predicate():
                return
            time.sleep(interval)
        raise TimeoutError(f"Timed out waiting for {description}")

    def _wait_force_close_unlock(self, timeout=600):
        self._wait_until(
            lambda: len(self.get_commit_cells()) > 0,
            "force-close commit cells",
            timeout=120,
        )
        self.node.getClient().generate_epochs("0x1", wait_time=0)
        self._wait_until(
            lambda: len(self.get_commit_cells()) == 0,
            "force-close commit cells to be consumed",
            timeout=timeout // 10,
            interval=10,
        )

    def _wait_ready_channel_ids(self, local, remote, count, timeout=120):
        ready_channel_ids = []
        for _ in range(timeout):
            channels = local.get_client().list_channels(
                {"pubkey": remote.get_pubkey()}
            )["channels"]
            ready_channel_ids = [
                channel["channel_id"]
                for channel in channels
                if channel["state"]["state_name"] == "ChannelReady"
            ]
            if len(ready_channel_ids) == count:
                return ready_channel_ids
            time.sleep(1)
        raise TimeoutError(
            f"Expected {count} ready channels, got {len(ready_channel_ids)}"
        )

    def _restart_fiber(self, fiber, peers):
        fiber.stop()
        time.sleep(2)
        fiber.start(fnn_log_level=self.fnn_log_level)
        for peer in peers:
            fiber.connect_peer(peer)
        time.sleep(3)

    def _get_tlc_status(self, fiber, remote_pubkey, channel_id, payment_hash):
        status = self._find_tlc_status(fiber, remote_pubkey, channel_id, payment_hash)
        if status is not None:
            return status
        raise AssertionError(f"TLC {payment_hash} not found in channel {channel_id}")

    def _find_tlc_status(self, fiber, remote_pubkey, channel_id, payment_hash):
        channels = fiber.get_client().list_channels(
            {"pubkey": remote_pubkey, "include_closed": True}
        )["channels"]
        for channel in channels:
            if channel["channel_id"] != channel_id:
                continue
            for tlc in channel.get("pending_tlcs", []):
                if tlc["payment_hash"] == payment_hash:
                    return tlc["status"]
        return None

    def _prepare_mpp_payment(self):
        fiber3 = self.start_new_fiber(self.generate_account(10000))
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0)
        self.open_channel(self.fiber1, self.fiber2, 1000 * 100000000, 0)
        self.open_channel(self.fiber2, fiber3, 3000 * 100000000, 0)
        channel_ids = self._wait_ready_channel_ids(self.fiber1, self.fiber2, 2)

        self.wait_graph_channels_sync(self.fiber1, 3)
        self.wait_graph_channels_sync(self.fiber2, 3)
        self.wait_graph_channels_sync(fiber3, 3)
        time.sleep(2)

        preimage = self.generate_random_preimage()
        payment_hash = ckb_hash(preimage)
        invoice = fiber3.get_client().new_invoice(
            {
                "amount": hex(1500 * 100000000),
                "currency": "Fibd",
                "description": "mpp force close hold invoice",
                "payment_hash": payment_hash,
                "allow_mpp": True,
            }
        )
        payment = self.fiber1.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "max_parts": hex(2),
                "max_fee_rate": hex(1000000000000000),
            }
        )
        assert payment["payment_hash"] == payment_hash
        self.wait_payment_state(self.fiber1, payment_hash, "Inflight")
        self.wait_invoice_state(fiber3, payment_hash, "Received")
        self._wait_until(
            lambda: all(
                self._find_tlc_status(
                    self.fiber2,
                    self.fiber1.get_pubkey(),
                    channel_id,
                    payment_hash,
                )
                is not None
                for channel_id in channel_ids
            ),
            "both MPP split TLCs to reach the forwarding node",
            timeout=120,
        )
        return fiber3, payment_hash, preimage, channel_ids

    def test_mpp_force_close_multiple_channels(self):
        fiber3, payment_hash, preimage, channel_ids = self._prepare_mpp_payment()
        for channel_id in channel_ids:
            self.fiber1.get_client().shutdown_channel(
                {"channel_id": channel_id, "force": True}
            )

        time.sleep(10)
        fiber3.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )
        self._wait_force_close_unlock()

        self.wait_payment_state(self.fiber1, payment_hash, "Success", timeout=360)
        self.wait_invoice_state(fiber3, payment_hash, "Paid", timeout=360)

        sender_removed = 0
        receiver_removed = 0
        for channel_id in channel_ids:
            if self._get_tlc_status(
                self.fiber1,
                self.fiber2.get_pubkey(),
                channel_id,
                payment_hash,
            ) == {"Outbound": "RemoteRemoved"}:
                sender_removed += 1
            if self._get_tlc_status(
                self.fiber2,
                self.fiber1.get_pubkey(),
                channel_id,
                payment_hash,
            ) == {"Inbound": "LocalRemoved"}:
                receiver_removed += 1

        assert sender_removed == 2
        assert receiver_removed == 2

    def test_mpp_force_close_one_channel_only_one_tlc_consumed(self):
        fiber3, payment_hash, preimage, channel_ids = self._prepare_mpp_payment()
        force_closed_channel = channel_ids[0]
        offchain_channel = channel_ids[1]
        self.fiber2.get_client().shutdown_channel(
            {"channel_id": force_closed_channel, "force": True}
        )
        pending_tx_hash = self.wait_and_check_tx_pool_fee(1000, False)
        self.Miner.miner_until_tx_committed(self.node, pending_tx_hash)
        fiber3.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )
        self._wait_force_close_unlock()
        self.wait_invoice_state(fiber3, payment_hash, "Paid", timeout=360)
        self.wait_payment_state(self.fiber1, payment_hash, "Success", timeout=360)

        sender_removed = 0
        receiver_removed = 0
        for channel_id in channel_ids:
            if self._find_tlc_status(
                self.fiber1,
                self.fiber2.get_pubkey(),
                channel_id,
                payment_hash,
            ) == {"Outbound": "RemoteRemoved"}:
                sender_removed += 1
            if self._find_tlc_status(
                self.fiber2,
                self.fiber1.get_pubkey(),
                channel_id,
                payment_hash,
            ) == {"Inbound": "LocalRemoved"}:
                receiver_removed += 1

        assert sender_removed == 1
        assert receiver_removed == 1
        assert (
            self._find_tlc_status(
                self.fiber1,
                self.fiber2.get_pubkey(),
                offchain_channel,
                payment_hash,
            )
            is None
        )
        assert (
            self._find_tlc_status(
                self.fiber2,
                self.fiber1.get_pubkey(),
                offchain_channel,
                payment_hash,
            )
            is None
        )

    def test_mpp_force_close_one_channel_intermediate_restart_after_settle(self):
        fiber3, payment_hash, preimage, channel_ids = self._prepare_mpp_payment()
        force_closed_channel = channel_ids[0]
        offchain_channel = channel_ids[1]
        self.fiber1.get_client().shutdown_channel(
            {"channel_id": force_closed_channel, "force": True}
        )

        time.sleep(10)
        fiber3.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )
        self._wait_until(
            lambda: self._find_tlc_status(
                self.fiber2,
                self.fiber1.get_pubkey(),
                offchain_channel,
                payment_hash,
            )
            is None,
            "the off-chain split to be removed before restart",
            timeout=120,
        )
        self._restart_fiber(self.fiber2, [self.fiber1, fiber3])
        self._wait_force_close_unlock()

        self.wait_invoice_state(fiber3, payment_hash, "Paid", timeout=360)
        self.wait_payment_state(self.fiber1, payment_hash, "Success", timeout=360)
        assert self._get_tlc_status(
            self.fiber1,
            self.fiber2.get_pubkey(),
            force_closed_channel,
            payment_hash,
        ) == {"Outbound": "RemoteRemoved"}
        assert self._get_tlc_status(
            self.fiber2,
            self.fiber1.get_pubkey(),
            force_closed_channel,
            payment_hash,
        ) == {"Inbound": "LocalRemoved"}
