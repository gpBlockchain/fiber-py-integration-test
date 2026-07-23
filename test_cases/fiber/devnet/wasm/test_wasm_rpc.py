import time

import pytest

from framework.basic_fiber import FiberTest
from framework.config import DEFAULT_MIN_DEPOSIT_CKB
from framework.test_wasm_fiber import WasmFiber


class WasmRpcTest(FiberTest):
    """
    Test cases for the Wasm RPC interface.
    """

    def _channel_by_id(self, client, channel_id):
        channels = client.list_channels({})["channels"]
        for channel in channels:
            if channel["channel_id"] == channel_id:
                return channel
        raise AssertionError(f"channel {channel_id} not found in {channels}")

    def test_wasm_rpc(self):
        """
        Test the Wasm RPC interface.
        """
        account_private = self.generate_account(
            10000, self.Config.ACCOUNT_PRIVATE_1, 10000 * 100000000
        )
        WasmFiber.reset()
        wasmFiber = WasmFiber(
            account_private,
            "0201010101010101010101010101010101010101010101010101010101010101",
            "devnet",
        )
        time.sleep(1)
        wasmFiber.connect_peer(self.fiber1)
        time.sleep(1)
        # abandon_channel
        wasmFiber.get_client().open_channel(
            {
                "pubkey": self.fiber1.get_pubkey(),
                "funding_amount": hex(1000 * 100000000 + DEFAULT_MIN_DEPOSIT_CKB),
                "public": True,
            }
        )
        self.wait_for_channel_state(
            wasmFiber.get_client(), self.fiber1.get_pubkey(), "AwaitingTxSignatures"
        )
        pending_channel_id = wasmFiber.get_client().list_channels({})["channels"][0][
            "channel_id"
        ]
        with pytest.raises(Exception) as exc_info:
            wasmFiber.get_client().abandon_channel({"channel_id": pending_channel_id})
        expected_error_message = " our signature has been sent. It cannot be abandoned"
        assert expected_error_message in exc_info.value.args[0], (
            f"Expected substring '{expected_error_message}' "
            f"not found in actual string '{exc_info.value.args[0]}'"
        )
        time.sleep(1)
        first_channel_id = self.wait_for_channel_state(
            wasmFiber.get_client(), self.fiber1.get_pubkey(), "ChannelReady"
        )

        # accept_channel
        wasm_node_pubkey = wasmFiber.get_client().node_info()["pubkey"]
        existing_channel_ids = {
            channel["channel_id"]
            for channel in wasmFiber.get_client().list_channels({})["channels"]
        }
        temporary_channel = self.fiber1.get_client().open_channel(
            {
                "pubkey": wasm_node_pubkey,
                "funding_amount": hex(100 + DEFAULT_MIN_DEPOSIT_CKB),
                "public": True,
            }
        )
        time.sleep(2)
        channel = wasmFiber.get_client().accept_channel(
            {
                "temporary_channel_id": temporary_channel["temporary_channel_id"],
                "funding_amount": hex(100 + DEFAULT_MIN_DEPOSIT_CKB),
            }
        )
        self.wait_for_channel_state(
            wasmFiber.get_client(),
            self.fiber1.get_pubkey(),
            "ChannelReady",
            channel_id=first_channel_id,
        )
        accepted_channel_id = self.wait_for_new_channel_state(
            wasmFiber.get_client(),
            self.fiber1.get_pubkey(),
            "ChannelReady",
            existing_channel_ids,
        )
        # list_channels
        wasm_channel = self._channel_by_id(wasmFiber.get_client(), accepted_channel_id)
        fiber1_channel = self._channel_by_id(
            self.fiber1.get_client(), accepted_channel_id
        )
        print("wasm_channel:", wasm_channel)
        print("fiber1_channel:", fiber1_channel)
        assert wasm_channel["channel_id"] == fiber1_channel["channel_id"]

        # update_channel
        update_channel_id = accepted_channel_id
        wasmFiber.get_client().update_channel(
            {
                "channel_id": update_channel_id,
                "tlc_fee_proportional_millionths": hex(2000),
            }
        )
        time.sleep(1)
        channel = self._channel_by_id(wasmFiber.get_client(), update_channel_id)
        assert channel["tlc_fee_proportional_millionths"] == hex(2000)

        # shutdown_channel
        shutdown_channel_id = accepted_channel_id
        wasmFiber.get_client().shutdown_channel(
            {
                "channel_id": shutdown_channel_id,
                "close_script": self.get_account_script(account_private),
                "fee_rate": "0x3FC",
            }
        )
        tx_hash = self.wait_and_check_tx_pool_fee(1000, False, 120)
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        tx_message = self.get_tx_message(tx_hash)
        print("tx_message:", tx_message)
        self.wait_for_channel_state(
            wasmFiber.get_client(),
            self.fiber1.get_pubkey(),
            "Closed",
            include_closed=True,
            channel_id=shutdown_channel_id,
        )
        # graph_nodes
        wasm_fiber_graph_nodes = wasmFiber.get_client().graph_nodes({})
        fiber1_graph_nodes = self.fiber1.get_client().graph_nodes({})
        print("fiber1_graph_nodes:", fiber1_graph_nodes)
        print("wasm_fiber_graph_nodes:", wasm_fiber_graph_nodes)
        # graph_channels
        wasm_fiber_graph_channels = wasmFiber.get_client().graph_channels({})
        fiber1_graph_channels = self.fiber1.get_client().graph_channels({})
        print("wasm_fiber_graph_channels:", wasm_fiber_graph_channels)
        print("fiber1_graph_channels:", fiber1_graph_channels)
        # node_info
        node_info = wasmFiber.get_client().node_info()
        print("node_info:", node_info)
        # new_invoice
        self.send_invoice_payment(wasmFiber, self.fiber1, 1 * 100000000, True)

        self.send_invoice_payment(self.fiber1, wasmFiber, 1, True)
        # parse_invoice
        invoice = self.fiber1.get_client().new_invoice(
            {
                "amount": hex(1),
                "currency": "Fibd",
                "description": "test invoice generated by node2",
                "expiry": "0xe10",
                "final_cltv": "0x28",
                "payment_preimage": self.generate_random_preimage(),
                "hash_algorithm": "sha256",
            }
        )
        wasm_parse_invoice = wasmFiber.get_client().parse_invoice(
            {
                "invoice": invoice["invoice_address"],
            }
        )
        print("invoice:", invoice)
        print("wasm_parse_invoice:", wasm_parse_invoice)
        # get_invoice
        wasm_invoice = wasmFiber.get_client().new_invoice(
            {
                "amount": hex(1),
                "currency": "Fibd",
                "description": "test invoice generated by node2",
                "expiry": "0xe10",
                "final_cltv": "0x28",
                "payment_preimage": self.generate_random_preimage(),
                "hash_algorithm": "sha256",
            }
        )
        wasm_get_invoice = wasmFiber.get_client().get_invoice(
            {"payment_hash": wasm_invoice["invoice"]["data"]["payment_hash"]}
        )
        print("wasm_get_invoice:", wasm_get_invoice)
        # cancel_invoice
        wasmFiber.get_client().cancel_invoice(
            {"payment_hash": wasm_get_invoice["invoice"]["data"]["payment_hash"]}
        )
        wasm_get_cancel_invoice = wasmFiber.get_client().get_invoice(
            {"payment_hash": wasm_invoice["invoice"]["data"]["payment_hash"]}
        )
        print("wasm_get_cancel_invoice:", wasm_get_cancel_invoice)
        assert wasm_get_cancel_invoice["status"] == "Cancelled"
        # send_payment
        self.send_payment(wasmFiber, self.fiber1, 1)
        # get_payment

        # build_router
        # wasmFiber.get_client().build_router({})
        channel_outpoint = wasmFiber.get_client().list_channels({})["channels"][0][
            "channel_outpoint"
        ]
        router = wasmFiber.get_client().build_router(
            {
                "amount": hex(1 * 100000000),  # 超过通道余额
                "udt_type_script": None,
                "hops_info": [
                    {
                        "pubkey": self.fiber1.get_client().node_info()["pubkey"],
                        "channel_outpoint": channel_outpoint,
                    },
                ],
                "final_tlc_expiry_delta": None,
            }
        )
        print("router:", router)
        # send_payment_with_router
        payment = wasmFiber.get_client().send_payment_with_router(
            {
                "keysend": True,
                "dry_run": False,
                "router": router["router_hops"],
            }
        )
        self.wait_payment_state(wasmFiber, payment["payment_hash"], "Success")
        # connect_peer

        # disconnect_peer
        wasmFiber.get_client().disconnect_peer({"pubkey": self.fiber1.get_pubkey()})
        # list_peers
        time.sleep(1)
        peers = wasmFiber.get_client().list_peers()
        assert len(peers["peers"]) == 0
        wasmFiber.connect_peer(self.fiber1)
        time.sleep(1)
        peers = wasmFiber.get_client().list_peers()
        assert len(peers["peers"]) == 1
        # Watchtower
        # todo
