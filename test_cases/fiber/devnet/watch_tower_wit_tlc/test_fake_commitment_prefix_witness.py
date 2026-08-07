# import tempfile
# import time
# from decimal import Decimal
#
# from framework.basic_fiber import COMMIT_LOCK_CODE_HASH, FiberTest
#
#
# CKB = 100000000
# FAKE_INPUT_CAPACITY = 300 * CKB
# FAKE_COMMITMENT_CAPACITY = 100 * CKB
# TX_FEE = 100000
# XUDT_COMPATIBLE_WITNESS = bytes([16, 0, 0, 0, 16, 0, 0, 0, 16, 0, 0, 0, 16, 0, 0, 0])
#
#
# class TestFakeCommitmentPrefixWitness(FiberTest):
#     """Regression for malicious prefix-matching commitment-like cells.
#
#     A fake live cell using the CommitmentLock code hash and the first 36 bytes
#     of a real commitment lock args used to make watchtower parse the fake
#     transaction witness as settlement data.  The malicious witness below has
#     one pending HTLC but two unlocks for unlock_type 0.  Fixed watchtower code
#     must reject/ignore that witness without panicking.
#     """
#
#     start_fiber_config = {"fiber_watchtower_check_interval_seconds": 2}
#
#     def _capacity_to_shannons(self, capacity_text):
#         capacity = capacity_text.replace("(CKB)", "").strip()
#         return int(Decimal(capacity) * CKB)
#
#     def _send_account_transaction(self, private_key, outputs):
#         account = self.Ckb_cli.util_key_info_by_private_key(private_key)
#         account_address = account["address"]["testnet"]
#         live_cells = self.Ckb_cli.wallet_get_live_cells(
#             account_address, api_url=self.node.rpcUrl
#         )["live_cells"]
#
#         required_capacity = sum(int(output["capacity"], 16) for output in outputs)
#         input_capacity = 0
#         inputs = []
#         for live_cell in live_cells:
#             inputs.append(
#                 {
#                     "tx_hash": live_cell["tx_hash"],
#                     "index": live_cell["output_index"],
#                 }
#             )
#             input_capacity += self._capacity_to_shannons(live_cell["capacity"])
#             if input_capacity >= required_capacity + TX_FEE:
#                 break
#
#         assert input_capacity >= required_capacity + TX_FEE
#
#         change_capacity = input_capacity - required_capacity - TX_FEE
#         if change_capacity > 61 * CKB:
#             outputs.append(
#                 {
#                     "capacity": hex(change_capacity),
#                     "lock": self.get_account_script(private_key),
#                 }
#             )
#
#         with tempfile.NamedTemporaryFile(suffix=".json") as tx_file:
#             self.Ckb_cli.tx_init(tx_file.name, self.node.rpcUrl)
#             self.Ckb_cli.tx_add_multisig_config(
#                 account_address, tx_file.name, self.node.rpcUrl
#             )
#             for tx_input in inputs:
#                 self.Ckb_cli.tx_add_input(
#                     tx_input["tx_hash"],
#                     tx_input["index"],
#                     tx_file.name,
#                     self.node.rpcUrl,
#                 )
#             for output in outputs:
#                 self.Ckb_cli.tx_add_output(output, "0x", tx_file.name)
#
#             sign_data = self.Ckb_cli.tx_sign_inputs(
#                 private_key, tx_file.name, self.node.rpcUrl
#             )
#             self.Ckb_cli.tx_add_signature(
#                 sign_data[0]["lock-arg"],
#                 sign_data[0]["signature"],
#                 tx_file.name,
#                 self.node.rpcUrl,
#             )
#             return self.Ckb_cli.tx_send(tx_file.name, self.node.rpcUrl).strip()
#
#     def _deploy_always_success(self, private_key):
#         tx_hash = self.Contract.deploy_ckb_contract(
#             private_key,
#             self.Config.ALWAYS_SUCCESS_CONTRACT_PATH,
#             2000,
#             True,
#             self.node.rpcUrl,
#         )
#         self.Miner.miner_until_tx_committed(self.node, tx_hash)
#         code_hash = self.Contract.get_ckb_contract_codehash(
#             tx_hash, 0, True, self.node.rpcUrl
#         )
#         return tx_hash, code_hash
#
#     def _create_always_success_cell(self, private_key, always_success_code_hash):
#         tx_hash = self._send_account_transaction(
#             private_key,
#             [
#                 {
#                     "capacity": hex(FAKE_INPUT_CAPACITY),
#                     "lock": {
#                         "code_hash": always_success_code_hash,
#                         "hash_type": "type",
#                         "args": "0x",
#                     },
#                 }
#             ],
#         )
#         self.Miner.miner_until_tx_committed(self.node, tx_hash)
#         return {"tx_hash": tx_hash, "index": "0x0"}
#
#     def _commitment_args_from_tx(self, tx_hash):
#         tx = self.node.getClient().get_transaction(tx_hash)
#         for output in tx["transaction"]["outputs"]:
#             lock = output["lock"]
#             if (
#                 lock["code_hash"] == COMMIT_LOCK_CODE_HASH
#                 and lock["hash_type"] == "type"
#             ):
#                 return lock["args"]
#         assert False, f"no CommitmentLock output found in {tx_hash}"
#
#     def _malicious_settlement_witness(self):
#         # XUDT prefix, witness index/marker, pending_htlc_count = 1.
#         witness = bytearray(XUDT_COMPATIBLE_WITNESS)
#         witness.extend([0x00, 0x01])
#         witness.extend(bytes(85))
#         witness.extend(bytes(72))
#
#         duplicated_unlock = bytes([0x00, 0x00]) + bytes(65)
#         witness.extend(duplicated_unlock)
#         witness.extend(duplicated_unlock)
#         return "0x" + witness.hex()
#
#     def _send_fake_commitment_cell(
#         self,
#         attacker_private_key,
#         always_success_dep_tx,
#         always_success_input,
#         real_commitment_args,
#     ):
#         fake_args = real_commitment_args[: 2 + 36 * 2] + "11" * 21
#         change_capacity = FAKE_INPUT_CAPACITY - FAKE_COMMITMENT_CAPACITY - TX_FEE
#         tx = {
#             "version": "0x0",
#             "cell_deps": [
#                 {
#                     "out_point": {"tx_hash": always_success_dep_tx, "index": "0x0"},
#                     "dep_type": "code",
#                 }
#             ],
#             "header_deps": [],
#             "inputs": [
#                 {
#                     "previous_output": always_success_input,
#                     "since": "0x0",
#                 }
#             ],
#             "outputs": [
#                 {
#                     "capacity": hex(FAKE_COMMITMENT_CAPACITY),
#                     "lock": {
#                         "code_hash": COMMIT_LOCK_CODE_HASH,
#                         "hash_type": "type",
#                         "args": fake_args,
#                     },
#                 },
#                 {
#                     "capacity": hex(change_capacity),
#                     "lock": self.get_account_script(attacker_private_key),
#                 },
#             ],
#             "outputs_data": ["0x", "0x"],
#             "witnesses": [self._malicious_settlement_witness()],
#         }
#         fake_tx_hash = self.node.getClient().send_transaction(tx)
#         self.Miner.miner_until_tx_committed(self.node, fake_tx_hash)
#         return fake_tx_hash
#
#     def _assert_fibers_alive(self, timeout=20):
#         deadline = time.time() + timeout
#         while time.time() < deadline:
#             self.fiber1.get_client().node_info()
#             self.fiber2.get_client().node_info()
#             time.sleep(1)
#
#     def test_fake_commitment_prefix_witness_does_not_panic_watchtower(self):
#         attacker_private_key = self.generate_account(1000)
#         always_success_tx, always_success_code_hash = self._deploy_always_success(
#             attacker_private_key
#         )
#         always_success_input = self._create_always_success_cell(
#             attacker_private_key, always_success_code_hash
#         )
#
#         self.open_channel(self.fiber1, self.fiber2, 200 * CKB, 100 * CKB)
#         channel_id = self.fiber1.get_client().list_channels({})["channels"][0][
#             "channel_id"
#         ]
#         self.fiber1.get_client().shutdown_channel(
#             {"channel_id": channel_id, "force": True}
#         )
#         force_close_tx = self.wait_and_check_tx_pool_fee(1000, False)
#         self.Miner.miner_until_tx_committed(self.node, force_close_tx)
#
#         real_commitment_args = self._commitment_args_from_tx(force_close_tx)
#         fake_tx_hash = self._send_fake_commitment_cell(
#             attacker_private_key,
#             always_success_tx,
#             always_success_input,
#             real_commitment_args,
#         )
#         print("fake commitment-like tx:", fake_tx_hash)
