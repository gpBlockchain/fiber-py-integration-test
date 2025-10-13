import time

import pytest

from framework.basic_fiber import FiberTest
from framework.test_wasm_fiber import WasmFiber


class WasmRpcTest(FiberTest):
    """
    Test cases for the Wasm RPC interface.
    """

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
                "peer_id": self.fiber1.get_peer_id(),
                "funding_amount": hex(1000 * 100000000 + 62 * 100000000),
                "public": True,
            }
        )
        self.wait_for_channel_state(
            wasmFiber.get_client(), self.fiber1.get_peer_id(), "AWAITING_TX_SIGNATURES"
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
        self.wait_for_channel_state(
            wasmFiber.get_client(), self.fiber1.get_peer_id(), "CHANNEL_READY"
        )

        # accept_channel
        # get wasm fiber peer_id
        list_peers = self.fiber1.get_client().list_peers()
        wasm_node_id = wasmFiber.get_client().node_info()["node_id"]
        for peer in list_peers["peers"]:
            if peer["pubkey"] == wasm_node_id:
                wasm_fiber_peer_id = peer["peer_id"]
                break
        temporary_channel = self.fiber1.get_client().open_channel(
            {
                "peer_id": wasm_fiber_peer_id,
                "funding_amount": hex(100 + 62 * 100000000),
                "public": True,
            }
        )
        time.sleep(2)
        channel = wasmFiber.get_client().accept_channel(
            {
                "temporary_channel_id": temporary_channel["temporary_channel_id"],
                "funding_amount": hex(100 + 62 * 100000000),
            }
        )
        self.wait_for_channel_state(
            wasmFiber.get_client(), self.fiber1.get_peer_id(), "CHANNEL_READY"
        )
        # list_channels
        wasm_list_channel = wasmFiber.get_client().list_channels({})
        fiber1_list_channel = self.fiber1.get_client().list_channels({})
        print("wasm_list_channel:", wasm_list_channel)
        print("fiber1_list_channel:", fiber1_list_channel)
        assert (
            wasm_list_channel["channels"][0]["channel_id"]
            == fiber1_list_channel["channels"][0]["channel_id"]
        )

        # update_channel
        update_channel_id = wasmFiber.get_client().list_channels({})["channels"][0][
            "channel_id"
        ]
        wasmFiber.get_client().update_channel(
            {
                "channel_id": update_channel_id,
                "tlc_fee_proportional_millionths": hex(2000),
            }
        )
        time.sleep(1)
        channels = wasmFiber.get_client().list_channels({})
        assert channels["channels"][0]["tlc_fee_proportional_millionths"] == hex(2000)

        # shutdown_channel
        shutdown_channel_id = wasmFiber.get_client().list_channels({})["channels"][0][
            "channel_id"
        ]
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
            self.fiber1.get_peer_id(),
            "CLOSED",
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
                        "pubkey": self.fiber1.get_client().node_info()["node_id"],
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
        wasmFiber.get_client().disconnect_peer({"peer_id": self.fiber1.get_peer_id()})
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
