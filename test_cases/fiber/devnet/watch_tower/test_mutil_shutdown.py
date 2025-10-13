import time

from framework.basic_fiber import FiberTest


class TestMutilShutdown(FiberTest):

    def test_mutil_shutdown(self):
        """
        Test the shutdown process for multiple channels.
        Steps:
        1. Open CKB channels.
        2. Open UDT channels.
        3. Shutdown channels in order.
        4. Wait for shutdown commit.
        5. Check open_channel cell cost.
        6. node1 and node2 stop
        7. Generate epoch.
        8. node1 and node2 start
        9. Wait for channel split
        Returns:

        """
        ckb_channel_size = 4
        udt_channel_size = 4

        # 1. Open CKB channels.
        for i in range(ckb_channel_size):
            self.fiber1.get_client().open_channel(
                {
                    "peer_id": self.fiber2.get_peer_id(),
                    "funding_amount": hex(1000 * 100000000),
                    "public": True,
                    "shutdown_script": {
                        "code_hash": "0x9bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8",
                        "hash_type": "type",
                        "args": f"0x000{i}",
                    },
                }
            )
            self.wait_for_channel_state(
                self.fiber1.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY"
            )
            time.sleep(1)
        # 2. Open UDT channels.
        for i in range(udt_channel_size):
            self.fiber1.get_client().open_channel(
                {
                    "peer_id": self.fiber2.get_peer_id(),
                    "funding_amount": hex(200 * 100000000),
                    "public": True,
                    "funding_udt_type_script": self.get_account_udt_script(
                        self.fiber1.account_private
                    ),
                    "shutdown_script": {
                        "code_hash": "0x9bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8",
                        "hash_type": "type",
                        "args": f"0x010{i}",
                    },
                }
            )
            time.sleep(1)
            self.wait_for_channel_state(
                self.fiber1.get_client(), self.fiber2.get_peer_id(), "CHANNEL_READY"
            )

            # send tx

        # 3. Shutdown channels in order.
        channels = self.fiber1.get_client().list_channels({})
        isNode1 = True
        for channel in channels["channels"]:
            print(
                {
                    "channel_id": channel["channel_id"],
                }
            )
            if isNode1:
                self.fiber1.get_client().shutdown_channel(
                    {
                        "channel_id": channel["channel_id"],
                        "force": True,
                    }
                )
                isNode1 = False
                continue
            self.fiber2.get_client().shutdown_channel(
                {
                    "channel_id": channel["channel_id"],
                    "force": True,
                }
            )
            isNode1 = True

        # 4. Wait for shutdown commit.
        self.wait_tx_pool(1, 1000)
        time.sleep(5)
        for i in range(10):
            self.Miner.miner_with_version(self.node, "0x0")
        # 5. todo Check open_channel cell cost.
        time.sleep(5)

        # 6. node1 and node2 stop
        self.fiber1.stop()
        self.fiber2.stop()

        # 7. Generate epoch.
        self.node.getClient().generate_epochs("0xa")

        # 8. node1 and node2 start
        self.fiber1.start()
        self.fiber2.start()

        # 9. Wait for channel split
        for i in range(ckb_channel_size):
            self.wait_cell_len(
                {
                    "code_hash": "0x9bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8",
                    "hash_type": "type",
                    "args": f"0x000{i}",
                },
                1,
            )
            cells = self.node.getClient().get_cells(
                {
                    "script": {
                        "code_hash": "0x9bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8",
                        "hash_type": "type",
                        "args": f"0x000{i}",
                    },
                    "script_search_mode": "exact",
                    "script_type": "lock",
                },
                "asc",
                "0x64",
                None,
            )
            message = self.get_tx_message(cells["objects"][0]["out_point"]["tx_hash"])
            assert {
                "capacity": 99999999545,
                "args": f"0x000{i}",
            } in message["output_cells"]

        for i in range(udt_channel_size):
            self.wait_cell_len(
                {
                    "code_hash": "0x9bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8",
                    "hash_type": "type",
                    "args": f"0x010{i}",
                },
                1,
            )
            cells = self.node.getClient().get_cells(
                {
                    "script": {
                        "code_hash": "0x9bd7e06f3ecf4be0f2fcd2188b23f1b9fcc88e5d4b65a8637b17723bbda3cce8",
                        "hash_type": "type",
                        "args": f"0x010{i}",
                    },
                    "script_search_mode": "exact",
                    "script_type": "lock",
                },
                "asc",
                "0x64",
                None,
            )
            message = self.get_tx_message(cells["objects"][0]["out_point"]["tx_hash"])
            assert {
                "capacity": 12499999407,
                "args": f"0x010{i}",
                "udt_capacity": 20000000000,
                "udt_args": self.get_account_udt_script(self.fiber1.account_private)[
                    "args"
                ],
            } in message["output_cells"]

    def wait_cell_len(self, script, length, timeout=300):
        for i in range(timeout):
            cells = self.node.getClient().get_cells(
                {
                    "script": script,
                    "script_search_mode": "exact",
                    "script_type": "lock",
                },
                "asc",
                "0x64",
                None,
            )
            if len(cells["objects"]) < length:
                time.sleep(1)
                continue
            return
        raise Exception(f"timeout: {timeout}, scrit: {script}, length: {length}")
