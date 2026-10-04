"""Shared chain assertions for H32 upgrade/channel tests; no independent test cases."""

import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time

from framework.basic_fiber import (
    COMMIT_LOCK_ARGS,
    COMMIT_LOCK_CODE_HASH,
    TYPE_CONTRACT_CODE_HASH,
)
from framework.basic_share_fiber import SharedFiberTest
from framework.helper.settlement_witness import SettlementWitness
from framework.util import ckb_hash, get_project_root

CKB = 100000000
ROOT = Path(get_project_root())
OLD_CONTRACT = ROOT / "source/contract/fiber/fixtures/commitment-lock.9a561b3"
NEW_CONTRACT = ROOT / "source/contract/fiber/commitment-lock"
# PR base 构建：不宣告完整哈希特性，只有它能协商出 Legacy 承诺布局（57 字节 args / 85 字节 TLC）。
LEGACY_FIBER_VERSION = "9a561b3"
LEGACY_COMMITMENT_ARGS_LENGTH = 57
# FundingLock 是 devnet 快照部署的另一个脚本（source/fiber/README.v2.md）：
# 通道 funding cell 的锁永远是 FundingLock 的 20 字节聚合公钥哈希，与 CommitmentLock 无关。
FUNDING_LOCK_CODE_HASH = (
    "0xe7576045a47bb4cc172ffd0306ce01e232dc43805becb356e58f8bda9df51e26"
)
FUNDING_LOCK_ARGS_LENGTH = 20


def tlc_is_terminal(tlc):
    # RPC pending_tlcs enumerates all persisted TLCs, including on-chain terminal records.
    status = tlc["status"]
    return status in (
        {"Outbound": "RemoteRemoved"},
        {"Inbound": "LocalRemoved"},
        {"Outbound": "RemoveAckConfirmed"},
        {"Inbound": "RemoveAckConfirmed"},
    )


class ContractUpgradeSupport(SharedFiberTest):
    def sign_external_funding_tx(self, unsigned_funding_tx, private_key):
        """外部钱包签名 funding tx：走 ckb-cli，不依赖 dev RPC。

        `sign_external_funding_tx` 那个 RPC 属于 dev 模块，而 release 构建的节点根本不注册 dev
        （`rpc/mod.rs` 里该注册被 `#[cfg(debug_assertions)]` 门掉），调用只会得到 Method not found。
        这里与 open_channel_with_external_funding/external_funding_base.py 的
        `_sign_external_funding_tx` 同一做法：写临时 tx JSON → 加 multisig config → 对输入签名 →
        回填签名 → 读出 tx info，作为 submit_signed_funding_tx（channel 模块，release 可用）的入参。
        """
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
            tx_file = tmp.name
            json.dump(
                {
                    "transaction": unsigned_funding_tx,
                    "multisig_configs": {},
                    "signatures": {},
                },
                tmp,
            )
        try:
            account = self.Ckb_cli.util_key_info_by_private_key(private_key)
            self.Ckb_cli.tx_add_multisig_config(
                account["address"]["testnet"], tx_file, self.node.rpcUrl
            )
            for signature in self.Ckb_cli.tx_sign_inputs(
                private_key, tx_file, self.node.rpcUrl
            ):
                self.Ckb_cli.tx_add_signature(
                    signature["lock-arg"],
                    signature["signature"],
                    tx_file,
                    self.node.rpcUrl,
                )
            return self.Tx.build_tx_info(tx_file)
        finally:
            os.remove(tx_file)

    def wait_tlc_terminal(self, fiber, payment_hash, timeout=60):
        for _ in range(timeout):
            channels = fiber.get_client().list_channels({"include_closed": True})[
                "channels"
            ]
            matches = [
                t
                for c in channels
                for t in c["pending_tlcs"]
                if t["payment_hash"] == payment_hash
            ]
            if all(tlc_is_terminal(t) for t in matches):
                return
            time.sleep(1)
        self.fail(f"TLC not terminal after {timeout}s: {matches}")

    def assert_tlc_settlement(self, previous, tx, code_tx, pending, preimage):
        assert {
            "out_point": {"tx_hash": code_tx, "index": "0x0"},
            "dep_type": "code",
        } in tx["cell_deps"]
        version = getattr(self, "commitment_version", "legacy")
        witness = SettlementWitness.from_hex(tx["witnesses"][0], version=version)
        assert witness.to_hex() == tx["witnesses"][0]
        witness.assert_single_tlc_claim(pending, preimage)
        before, after = previous["outputs"][0], tx["outputs"][0]
        assert after["lock"]["code_hash"] == COMMIT_LOCK_CODE_HASH
        assert after["lock"]["hash_type"] == "type"
        args = bytes.fromhex(after["lock"]["args"][2:])
        assert (
            len(args) == (58 if version == "v1" else LEGACY_COMMITMENT_ARGS_LENGTH)
            and args[-1] == 1
        )
        assert args[:36] == bytes.fromhex(before["lock"]["args"][2:])[:36]
        # The witness parser already checked the selected entry against the full
        # expected hash and its encoded algorithm (Blake2b or SHA256).
        amount = witness.tlcs[witness.unlocks[0].unlock_type].amount
        assert int(before["capacity"], 16) - int(after["capacity"], 16) == amount
        # 同一交易中收款人的净增量扣除实际矿工费，避免误把钱包找零当 TLC 付款。
        message = self.get_tx_message(tx["hash"])
        received = sum(
            int(o["capacity"], 16)
            for o in tx["outputs"]
            if o["lock"]["args"] == self.account2["lock_arg"]
        )
        spent = 0
        for item in tx["inputs"]:
            point = item["previous_output"]
            cell = self.ckb.get_transaction(point["tx_hash"])["transaction"]["outputs"][
                int(point["index"], 16)
            ]
            if cell["lock"]["args"] == self.account2["lock_arg"]:
                spent += int(cell["capacity"], 16)
        assert received - spent == amount - message["fee"]
        self.assert_nodes_running()
        print(
            "TLC 结算:",
            tx["hash"],
            "合约:",
            code_tx,
            "金额:",
            amount,
            "剩余 TLC:",
            len(pending) - 1,
        )

    def contract_code_cell(self):
        """当前链上的 commitment-lock 代码 cell。

        默认 devnet 快照（`source/fiber/data.2026.0914.tar.gz`）已部署升级后的合约，
        因此普通行为用例不需要再提交一次升级交易，直接复用这个 cell 作 code dep 断言。
        """
        script = {
            "code_hash": TYPE_CONTRACT_CODE_HASH,
            "hash_type": "type",
            "args": COMMIT_LOCK_ARGS,
        }
        cells = self.ckb.get_cells(
            {
                "script": script,
                "script_type": "type",
                "script_search_mode": "exact",
                "with_data": True,
            },
            "asc",
            "0x10",
            None,
        )["objects"]
        assert len(cells) == 1, cells
        return cells[0]

    def current_contract_code_tx(self):
        """已部署代码 cell 的 tx hash，供结算交易的 code dep 断言使用。"""
        return self.contract_code_cell()["out_point"]["tx_hash"]

    def upgrade_contract(self, path):
        script = {
            "code_hash": TYPE_CONTRACT_CODE_HASH,
            "hash_type": "type",
            "args": COMMIT_LOCK_ARGS,
        }
        old = self.contract_code_cell()
        tx_hash = self.Contract.upgrade_ckb_type_contract(
            self.Config.MINER_PRIVATE_1,
            str(path),
            old["out_point"]["tx_hash"],
            int(old["out_point"]["index"], 16),
            fee=200000,
            api_url=self.node.rpcUrl,
        )
        self.Miner.miner_until_tx_committed(self.node, tx_hash)
        tx = self.ckb.get_transaction(tx_hash)["transaction"]
        assert tx["outputs"][0]["type"] == script
        assert tx["outputs"][0]["lock"] == old["output"]["lock"]
        assert any(i["previous_output"] == old["out_point"] for i in tx["inputs"])
        assert tx["outputs_data"][0] == "0x" + path.read_bytes().hex()
        # assert tx["outputs_data"][0] != old["output_data"]
        self.assert_nodes_running()
        print("合约升级:", old["out_point"], "->", tx_hash)
        return tx_hash

    def channel(self, fiber):
        channels = fiber.get_client().list_channels({"include_closed": True})[
            "channels"
        ]
        return next(c for c in channels if c["channel_id"] == self.channel_id)

    def assert_legacy_fiber_version(self):
        """通道两端必须是固定旧版本，否则无法协商出 Legacy 承诺。

        与样例同为 PR base 的旧库由 `OLD_CONTRACT` 的 `9a561b3` 产生；若不显式固定，
        类属性 `fiber_version` 一旦被换成支持完整哈希的构建，`open_legacy_channel` 会静默
        开出一条 V1 通道，H32V2-23/22/26 的“旧版本承诺语义”就不再成立。
        """
        for fiber in (self.fiber1, self.fiber2):
            path = ROOT / fiber.fiber_config_enum.fiber_bin_path
            version = subprocess.check_output([path, "--version"], text=True)
            assert (
                LEGACY_FIBER_VERSION in version
            ), f"{path} 不是固定旧版本 {LEGACY_FIBER_VERSION}，无法开通 Legacy 通道: {version}"

    def open_legacy_channel(self):
        # 先固定“两端都是旧版本”，再开通。PR base 不宣告完整哈希特性，协商结果必然是 Legacy；
        # 具体布局（57 字节承诺 args）只能在链上承诺交易里核对，因为 funding 输出锁的是
        # FundingLock，承诺锁要等强关/结算交易才上链（见 assert_settled / assert_tlc_settlement）。
        self.assert_legacy_fiber_version()
        self.channel_id = self.open_channel(self.fiber1, self.fiber2, 1000 * CKB, 0)
        channels = [self.channel(f) for f in self.fibers]
        assert all(not c["pending_tlcs"] for c in channels)
        raw = bytes.fromhex(channels[0]["channel_outpoint"][2:])
        assert len(raw) == 36 and raw[32:] == bytes(4)  # 本组 funding output 固定为 0。
        self.funding_tx = "0x" + raw[:32].hex()
        self.signed_hashes = [c["latest_commitment_transaction_hash"] for c in channels]
        # 直接读链上 funding 输出：它必须锁在 FundingLock 的 20 字节聚合公钥哈希上，确认
        # outpoint 指向的确实是本通道的 funding cell。这个输出不是承诺锁，因此不能拿
        # COMMIT_LOCK_CODE_HASH 判断通道协商成了哪种承诺布局。
        for fiber, channel in zip(self.fibers, channels):
            point = bytes.fromhex(channel["channel_outpoint"][2:])
            funding_output = self.ckb.get_transaction("0x" + point[:32].hex())[
                "transaction"
            ]["outputs"][int.from_bytes(point[32:], "little")]
            lock = funding_output["lock"]
            assert (lock["code_hash"], lock["hash_type"]) == (
                FUNDING_LOCK_CODE_HASH,
                "type",
            ), (
                fiber.tmp_path,
                lock,
            )
            args = bytes.fromhex(lock["args"].removeprefix("0x"))
            assert len(args) == FUNDING_LOCK_ARGS_LENGTH, (
                f"funding 输出必须锁在 FundingLock 的 {FUNDING_LOCK_ARGS_LENGTH} 字节聚合公钥哈希上: "
                f"{fiber.tmp_path} args={len(args)} lock={lock}"
            )
        # 57 字节 Legacy 布局对应 99 CKB 预留；布局本身由链上承诺交易核对。
        self.principals = [int(c["local_balance"], 16) + 99 * CKB for c in channels]
        self.wallet_before = self.wallet_balances()

    def force_close(self, fiber):
        fiber.get_client().shutdown_channel(
            {"channel_id": self.channel_id, "force": True}
        )
        tx = self.wait_for_spend(self.funding_tx)
        packed = self.ckb.get_transaction(tx["hash"], "0x0")["transaction"]
        assert (
            commitment_hash_without_deps(packed)
            == self.signed_hashes[self.fibers.index(fiber)]
        )
        assert self.ckb.get_live_cell("0x0", tx["hash"])["status"] == "live"
        return tx

    def wait_for_spend(self, tx_hash):
        for _ in range(150):
            self.assert_nodes_running()
            spent_by, _ = self.get_ln_cell_death_hash(tx_hash)
            if spent_by:
                result = self.ckb.get_transaction(spent_by)
                assert result["tx_status"]["status"] == "committed"
                tx = result["transaction"]
                assert any(
                    i["previous_output"] == {"tx_hash": tx_hash, "index": "0x0"}
                    for i in tx["inputs"]
                )
                return tx
            time.sleep(1)
        self.fail(f"等待 cell 被花费超时: {tx_hash}:0")

    def assert_settled(self, commitment, code_tx, prior_fees=0):
        tx = commitment
        fees = prior_fees + self.get_tx_message(tx["hash"])["fee"]
        # 无 TLC 也可能分两步返还双方余额，逐笔检查实际执行的新合约。
        for _ in range(3):
            locked = [
                o
                for o in tx["outputs"]
                if o["lock"]["code_hash"] == COMMIT_LOCK_CODE_HASH
            ]
            if not locked:
                break
            assert locked == [tx["outputs"][0]]
            lock = locked[0]["lock"]
            version = getattr(self, "commitment_version", "legacy")
            args = bytes.fromhex(lock["args"][2:])
            assert lock["hash_type"] == "type" and len(args) == (
                58 if version == "v1" else LEGACY_COMMITMENT_ARGS_LENGTH
            )
            if version == "v1":
                assert args[-1] == 1
            assert (
                int.from_bytes(bytes.fromhex(lock["args"][2:])[20:28], "little")
                == 0xA000010000000001
            )
            self.ckb.generate_epochs("0x2")
            tx = self.wait_for_spend(tx["hash"])
            assert {
                "out_point": {"tx_hash": code_tx, "index": "0x0"},
                "dep_type": "code",
            } in tx["cell_deps"]
            fees += self.get_tx_message(tx["hash"])["fee"]
            print("新合约结算交易:", tx["hash"])
        else:
            self.fail("仍有待结算 commitment cell")
        delta = [
            after - before
            for after, before in zip(self.wallet_balances(), self.wallet_before)
        ]
        funding = self.ckb.get_transaction(self.funding_tx)["transaction"]["outputs"][0]
        assert 0 <= fees < CKB // 100
        assert sum(delta) == int(funding["capacity"], 16) - fees
        for received, expected in zip(delta, self.principals):
            assert expected - CKB // 100 <= received <= expected
        self.assert_nodes_running()
        print("双方到账 / 总手续费:", delta, fees)

    def assert_local_closed(self, timeout=660):
        # 9a561b3 的 CheckChannelsShutdown 每 300 秒执行一次，与 Watchtower 的 2 秒无关。
        # 对端强关可能要先发现关闭，再在下一轮确认结算；覆盖两轮并留 60 秒余量。
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.assert_nodes_running()
            channel = self.channel(self.fiber1)
            if channel["state"][
                "state_name"
            ] == "Closed" and "WAITING_ONCHAIN_SETTLEMENT" not in str(channel["state"]):
                assert all(tlc_is_terminal(t) for t in channel["pending_tlcs"]), channel
                return
            time.sleep(1)
        self.fail(
            f"链上已结算，等待 {timeout} 秒后本端尚未完成关闭: {channel['state']}"
        )

    def wallet_balances(self):
        balances = []
        for account in (self.account1, self.account2):
            lock = {
                "code_hash": self.Config.CKB_DEFAULT_CONFIG[
                    "ckb_block_assembler_code_hash"
                ],
                "hash_type": "type",
                "args": account["lock_arg"],
            }
            result = self.ckb.get_cells_capacity(
                {"script": lock, "script_type": "lock", "script_search_mode": "exact"}
            )
            balances.append(int(result["capacity"], 16))
        return balances

    @classmethod
    def node_processes(self):
        processes = []
        for port in (self.ckb_rpc_port, self.fiber1_rpc_port, self.fiber2_rpc_port):
            pid = subprocess.check_output(
                ["lsof", "-nP", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"], text=True
            ).strip()
            started = subprocess.check_output(
                ["ps", "-p", pid, "-o", "lstart="], text=True
            ).strip()
            processes.append((pid, started))
        return processes

    def assert_nodes_running(self):
        # 只有显式捕获过进程基线的用例才做“未重启”断言；未设置时不做检查，
        # 避免故意重启节点的用例（如 13/18）因缺少基线而报 AttributeError。
        processes = getattr(self, "processes", None)
        if processes is not None:
            assert self.node_processes() == processes, "升级期间节点退出或重启"


class FullHashChannelSupport(ContractUpgradeSupport):
    """Shared setup for the H32V2 layout tests; contains no test methods.

    The helpers mirror the ones used by the already mapped H32V2 suites. They
    live here so new files do not copy the channel/commitment choreography;
    `test_full_hash_channels.py` keeps its local copies untouched.
    """

    def select_peers(self, sender, receiver, version):
        self.fiber1, self.fiber2 = sender, receiver
        self.fibers = [sender, receiver]
        self.account1, self.account2 = sender.get_account(), receiver.get_account()
        self.commitment_version = version

    def open_ready(self, external=False):
        self.fiber1.connect_peer(self.fiber2)
        previous = {
            c["channel_id"]
            for c in self.fiber1.get_client().list_channels({})["channels"]
        }
        if not external:
            self.channel_id = self.open_channel(self.fiber1, self.fiber2, 1000 * CKB, 0)
        else:
            client = self.fiber1.get_client()
            funding_lock = client.node_info()["default_funding_lock_script"]
            opened = client.call(
                "open_channel_with_external_funding",
                [
                    {
                        "pubkey": self.fiber2.get_pubkey(),
                        "funding_amount": hex(1099 * CKB),
                        "public": True,
                        "shutdown_script": funding_lock,
                        "funding_lock_script": funding_lock,
                    }
                ],
            )
            signed_funding_tx = self.sign_external_funding_tx(
                opened["unsigned_funding_tx"], self.fiber1.account_private
            )
            client.call(
                "submit_signed_funding_tx",
                [
                    {
                        "channel_id": opened["channel_id"],
                        "signed_funding_tx": signed_funding_tx,
                    }
                ],
            )
            self.channel_id = self.wait_for_new_channel_state(
                client, self.fiber2.get_pubkey(), "ChannelReady", previous
            )
        for _ in range(60):
            if all(
                self.channel(f)["state"]["state_name"] == "ChannelReady"
                for f in self.fibers
            ):
                break
            time.sleep(1)
        else:
            self.fail("Both channel ends must be ready")
        self.record_channel()

    def record_channel(self):
        channels = [self.channel(f) for f in self.fibers]
        outpoint = bytes.fromhex(channels[0]["channel_outpoint"][2:])
        assert outpoint[32:] == bytes(4)
        self.funding_tx = "0x" + outpoint[:32].hex()
        self.signed_hashes = [c["latest_commitment_transaction_hash"] for c in channels]
        reserve = (100 if self.commitment_version == "v1" else 99) * CKB
        self.principals = [int(c["local_balance"], 16) + reserve for c in channels]
        self.wallet_before = self.wallet_balances()

    def hold_one_payment(self, amount=CKB, algorithm="ckb_hash", settle=False):
        """Keep one committed TLC (or settle it immediately when settle=True)."""
        preimage = "0x" + secrets.token_hex(32)
        payment_hash = (
            ckb_hash(preimage)
            if algorithm == "ckb_hash"
            else "0x" + hashlib.sha256(bytes.fromhex(preimage[2:])).hexdigest()
        )
        invoice = self.fiber2.get_client().new_invoice(
            {
                "amount": hex(amount),
                "currency": "Fibd",
                "payment_hash": payment_hash,
                "hash_algorithm": algorithm,
                # Common supported bound: old nodes require at least 160 minutes.
                "final_expiry_delta": hex(9_600_000),
                "expiry": "0xe10",
            }
        )
        self.fiber1.get_client().send_payment({"invoice": invoice["invoice_address"]})
        self.wait_payment_state(self.fiber1, payment_hash, "Inflight")
        self.wait_invoice_state(self.fiber2, payment_hash, "Received")
        for _ in range(60):
            channels = [self.channel(f) for f in self.fibers]
            if all(
                len(c["pending_tlcs"]) == 1
                and c["pending_tlcs"][0]["payment_hash"] == payment_hash
                and "Committed" in c["pending_tlcs"][0]["status"].values()
                for c in channels
            ):
                self.signed_hashes = [
                    c["latest_commitment_transaction_hash"] for c in channels
                ]
                if settle:
                    self.fiber2.get_client().settle_invoice(
                        {"payment_hash": payment_hash, "payment_preimage": preimage}
                    )
                    self.wait_payment_state(self.fiber1, payment_hash, "Success")
                return payment_hash, preimage
            time.sleep(1)
        self.fail(f"Pending TLC was not committed on selected channel: {channels}")

    def assert_commitment_layout(self, transaction):
        args = bytes.fromhex(transaction["outputs"][0]["lock"]["args"][2:])
        assert len(args) == (
            58 if self.commitment_version == "v1" else LEGACY_COMMITMENT_ARGS_LENGTH
        )
        if self.commitment_version == "v1":
            assert args[-1] == 1
        return args

    def settle_held_channel(
        self, code_tx, payment_hash, preimage, amount=CKB, closer=None
    ):
        commitment = self.force_close(closer or self.fiber2)
        self.assert_commitment_layout(commitment)
        self.ckb.generate_epochs("0x1")
        self.fiber2.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )
        settled = self.wait_for_spend(commitment["hash"])
        self.assert_tlc_settlement(
            commitment, settled, code_tx, [(payment_hash, amount)], preimage
        )
        self.principals[0] -= amount
        self.principals[1] += amount
        self.assert_settled(
            settled, code_tx, self.get_tx_message(commitment["hash"])["fee"]
        )
        self.wait_payment_state(self.fiber1, payment_hash, "Success")
        result = self.fiber1.get_client().get_payment({"payment_hash": payment_hash})
        assert result["payment_preimage"] == preimage
        return SettlementWitness.from_hex(
            settled["witnesses"][0], version=self.commitment_version
        )


def commitment_hash_without_deps(packed):
    """存储的承诺不含 deps，广播时才补齐；比较其余字段，防止悄悄替换原承诺。"""

    def fields(raw):
        assert int.from_bytes(raw[:4], "little") == len(raw)
        first = int.from_bytes(raw[4:8], "little")
        offsets = [
            int.from_bytes(raw[i : i + 4], "little") for i in range(4, first, 4)
        ] + [len(raw)]
        return [raw[a:b] for a, b in zip(offsets, offsets[1:])]

    raw_fields = fields(fields(bytes.fromhex(packed[2:]))[0])
    assert len(raw_fields) == 6
    raw_fields[1] = bytes(4)
    offset, offsets = 28, []
    for value in raw_fields:
        offsets.append(offset.to_bytes(4, "little"))
        offset += len(value)
    raw = offset.to_bytes(4, "little") + b"".join(offsets + raw_fields)
    return (
        "0x"
        + hashlib.blake2b(raw, digest_size=32, person=b"ckb-default-hash").hexdigest()
    )
