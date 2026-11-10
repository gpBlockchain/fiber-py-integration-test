"""PR #1728：真实 Legacy 通道升级后，成功付款与强关到账保持一致。"""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import time

from framework.basic_fiber import FiberTest, COMMIT_LOCK_CODE_HASH
from framework.config import DEFAULT_MIN_LEDGER_DEPOSIT_CKB
from framework.helper.settlement_witness import SettlementWitness
from framework.test_fiber import FiberConfigPath
from framework.util import ckb_hash, get_project_root
from test_cases.fiber.devnet.compatibility.contract_upgrade_support import (
    ContractUpgradeSupport,
    commitment_hash_without_deps,
)
from test_cases.fiber.devnet.migration._helpers import start_with_confirm

ROOT = Path(get_project_root())
CKB = 100_000_000


class TestLegacySettlementBalances(FiberTest):
    fiber_version = FiberConfigPath.V091_DEV
    tmp_path_name = "tmp-pr1728"
    ckb_rpc_port, ckb_p2p_port = 27214, 27215
    fiber1_rpc_port, fiber1_p2p_port = 27228, 27227
    fiber2_rpc_port, fiber2_p2p_port = 27229, 27230
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 2}
    check_onchain_settlement = (
        os.getenv("FIBER_ASSERT_LEGACY_SETTLEMENT", "false").lower() == "true"
    )

    @classmethod
    def setup_class(cls):
        for port in (27214, 27215, 27228, 27227, 27229, 27230):
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", port))
        assert not (ROOT / cls.tmp_path_name).exists(), "测试目录已存在，请保留并检查"
        for version in (cls.fiber_version, FiberConfigPath.CURRENT_DEV):
            output = subprocess.check_output(
                [ROOT / version.fiber_bin_path, "--version"], text=True
            ).strip()
            print(f"binary: {version.fiber_bin_path}: {output}")
            if version == cls.fiber_version:
                assert "9a561b3" in output, output
        super().setup_class()

    def setup_method(self, method):
        super().setup_method(method)
        self.ckb = self.node.getClient()
        for fiber in self.fibers:
            fiber.get_client().try_count = 1
            fiber.get_client().timeout = 10

    def teardown_method(self, method):
        for fiber in self.fibers:
            fiber.stop()
            fiber.clean()

    @classmethod
    def teardown_class(cls):
        super().teardown_class()
        # Only this class owns the isolated directory and its stopped-store backups.
        shutil.rmtree(ROOT / cls.tmp_path_name, ignore_errors=True)

    # TEST-MAP: PR1728-01
    def test_fulfilled_legacy_ckb_after_upgrade(self):
        """
        1. 两个真实旧节点建立 CKB 通道并双向付款。
        2. 停机备份并升级双方，核对原通道身份与余额。
        3. 三轮并发结算持单及反向付款，核对终态、原像和独立余额账本。
        4. 探针付款后由 A 强关，核对链上逐方到账、手续费及最终关闭。
        """
        # 1. 旧节点开通并使用真实 CKB 通道。
        self._open_used_legacy_channel(udt=False)
        # 2. 通过正常 CLI 迁移恢复原通道。
        self._upgrade_original_channel()
        # 3. 成功付款必须逐笔符合预定结果，不能按实际失败缩小账本。
        self._fulfill_batches()
        # 4. 以真实链上消费和钱包净增核对资金。
        self._force_close_and_check_funds()

    # TEST-MAP: PR1728-01
    def test_fulfilled_legacy_udt_after_upgrade(self):
        """
        1. 两个真实旧节点建立 UDT 通道并双向付款。
        2. 停机备份并升级双方，核对原通道身份与余额。
        3. 三轮并发结算持单及反向付款，核对终态、原像和独立余额账本。
        4. 探针付款后由 A 强关，分别核对代币、CKB 预留及最终关闭。
        """
        # 1. 使用真实发行的 UDT，金额不混入 CKB 预留。
        self._open_used_legacy_channel(udt=True)
        # 2. 通过正常 CLI 迁移恢复原通道。
        self._upgrade_original_channel()
        # 3. 与 CKB 变体共用付款步骤和精确余额断言。
        self._fulfill_batches()
        # 4. UDT 数量与 CKB 手续费分别核算。
        self._force_close_and_check_funds()

    # TEST-MAP: PR1728-02
    def test_cancelled_legacy_ckb_after_upgrade(self):
        """
        1. 两个真实旧节点建立 CKB 通道并双向付款。
        2. 停机备份并升级双方，核对原通道身份与余额。
        3. 三轮并发取消已接收持单，同时反向付款，核对失败终态和精确退款。
        4. 探针付款后由 A 强关，核对链上逐方到账、手续费及最终关闭。
        """
        # 1. 旧节点开通并使用真实 CKB 通道。
        self._open_used_legacy_channel(udt=False)

        # 2. 通过正常 CLI 迁移恢复原通道。
        self._upgrade_original_channel()

        # 3. 原像保留在测试端，取消金额不计入成功付款账本。
        self._cancel_batches()

        # 4. 退款必须能在链上取回，而非只改变 RPC 余额。
        self._force_close_and_check_funds()

    # TEST-MAP: PR1728-02
    def test_cancelled_legacy_udt_after_upgrade(self):
        """
        1. 两个真实旧节点建立 UDT 通道并双向付款。
        2. 停机备份并升级双方，核对原通道身份与余额。
        3. 三轮并发取消已接收持单，同时反向付款，核对代币退款及在途余额清零。
        4. 探针付款后由 A 强关，分别核对代币、CKB 预留及最终关闭。
        """
        # 1. 使用真实发行的 UDT，金额不混入 CKB 预留。
        self._open_used_legacy_channel(udt=True)

        # 2. 通过正常 CLI 迁移恢复原通道。
        self._upgrade_original_channel()

        # 3. 与 CKB 变体共用取消步骤和精确退款断言。
        self._cancel_batches()

        # 4. UDT 数量与 CKB 手续费分别核算。
        self._force_close_and_check_funds()

    def _channels(self):
        return [
            next(
                c
                for c in f.get_client().list_channels({"include_closed": True})[
                    "channels"
                ]
                if c["channel_id"] == self.channel_id
            )
            for f in self.fibers
        ]

    def _wait(self, description, observe, accepted, timeout=60):
        deadline = time.monotonic() + timeout
        while True:
            value = observe()
            if accepted(value):
                return value
            if time.monotonic() >= deadline:
                self.fail(f"{description}，等待 {timeout}s 超时：{value}")
            time.sleep(0.5)

    def _assert_balances(self, cancelled=()):
        channels = self._wait(
            "付款后通道余额收敛且在途金额清零",
            self._channels,
            lambda rows: all(
                int(c["local_balance"], 16) == self.expected[i]
                and int(c["remote_balance"], 16) == self.expected[1 - i]
                and int(c["offered_tlc_balance"], 16) == 0
                and int(c["received_tlc_balance"], 16) == 0
                # RPC 也列出已确认移除的失败记录；只接受本轮已核验取消的 hash。
                and all(
                    tlc["payment_hash"] in cancelled
                    and tlc["status"]
                    in (
                        {"Inbound": "RemoveAckConfirmed"},
                        {"Outbound": "RemoveAckConfirmed"},
                    )
                    for tlc in c["pending_tlcs"]
                )
                for i, c in enumerate(rows)
            ),
        )
        assert all(c["state"]["state_name"] == "ChannelReady" for c in channels)
        return channels

    def _invoice_payment(self, sender_index, amount, hold=False):
        sender, receiver = self.fibers[sender_index], self.fibers[1 - sender_index]
        preimage = "0x" + secrets.token_hex(32)
        payment_hash = "0x" + hashlib.sha256(bytes.fromhex(preimage[2:])).hexdigest()
        params = {
            "amount": hex(amount),
            "currency": "Fibd",
            "hash_algorithm": "sha256",
            "final_expiry_delta": hex(9_600_000),
            "expiry": "0xe10",
            "payment_hash" if hold else "payment_preimage": (
                payment_hash if hold else preimage
            ),
        }
        if self.udt:
            params["udt_type_script"] = self.udt
        invoice = receiver.get_client().new_invoice(params)
        result = sender.get_client().send_payment(
            {"invoice": invoice["invoice_address"], "max_parts": "0x1"}
        )
        assert result["payment_hash"] == payment_hash
        return sender_index, amount, payment_hash, preimage

    def _assert_success(self, payments, cancelled=()):
        for sender_index, amount, payment_hash, preimage in payments:
            sender, receiver = self.fibers[sender_index], self.fibers[1 - sender_index]
            self.wait_payment_state(sender, payment_hash, "Success", timeout=60)
            self.wait_invoice_state(receiver, payment_hash, "Paid", timeout=60)
            actual = sender.get_client().get_payment({"payment_hash": payment_hash})
            assert actual["payment_preimage"] == preimage, actual
            self.expected[sender_index] -= amount
            self.expected[1 - sender_index] += amount
        self._assert_balances(cancelled=cancelled)

    def _open_used_legacy_channel(self, udt):
        self.udt = (
            self.get_account_udt_script(self.fiber1.account_private) if udt else None
        )
        self.locks = [self.get_account_script(f.account_private) for f in self.fibers]
        code = ContractUpgradeSupport.contract_code_cell(self)
        self.contract_point = code["out_point"]
        print(
            "commitment code:",
            self.contract_point,
            "data_hash:",
            ckb_hash(code["output_data"]),
        )
        if udt:
            # 旧版 UDT 自动接收为零出资，先由 A 出资，再通过真实付款给 B 流动性。
            self.fiber1.get_client().open_channel(
                {
                    "pubkey": self.fiber2.get_pubkey(),
                    "funding_amount": hex(500 * CKB),
                    "funding_udt_type_script": self.udt,
                    "public": True,
                }
            )
            self.channel_id = self.wait_for_new_channel_state(
                self.fiber1.get_client(),
                self.fiber2.get_pubkey(),
                "ChannelReady",
                set(),
                timeout=120,
            )
        else:
            self.channel_id = self.open_channel(self.fiber1, self.fiber2, 500 * CKB, 0)
        self._wait(
            "双方旧通道 Ready",
            self._channels,
            lambda rows: all(c["state"]["state_name"] == "ChannelReady" for c in rows),
            120,
        )
        self.expected = [int(c["local_balance"], 16) for c in self._channels()]
        self._assert_success([self._invoice_payment(0, 100 * CKB)])
        self._assert_success([self._invoice_payment(1, CKB)])
        old = self._assert_balances()
        raw_point = bytes.fromhex(old[0]["channel_outpoint"][2:])
        self.funding_point = {
            "tx_hash": "0x" + raw_point[:32].hex(),
            "index": hex(int.from_bytes(raw_point[32:], "little")),
        }
        assert self.funding_point["index"] == "0x0"
        self.funding = self.ckb.get_transaction(self.funding_point["tx_hash"])[
            "transaction"
        ]
        assert old[0]["channel_outpoint"] == old[1]["channel_outpoint"]
        assert all(c["funding_udt_type_script"] == self.udt for c in old)

    def _upgrade_original_channel(self):
        before = self._assert_balances()
        for index, fiber in enumerate(self.fibers):
            fiber.stop()
            backup = ROOT / self.tmp_path_name / f"old-store-{index}"
            if backup.exists():
                shutil.rmtree(backup)
            shutil.copytree(Path(fiber.tmp_path) / "fiber/store", backup)
            fiber.fiber_config_enum = FiberConfigPath.CURRENT_DEV
        for fiber in self.fibers:
            start_with_confirm(fiber, confirm="y", timeout=90)
        self.fiber1.connect_peer(self.fiber2)
        self._wait(
            "升级后原通道 Ready",
            self._channels,
            lambda rows: all(c["state"]["state_name"] == "ChannelReady" for c in rows),
            120,
        )
        after = self._assert_balances()
        for old, new in zip(before, after):
            for key in (
                "channel_id",
                "channel_outpoint",
                "local_balance",
                "remote_balance",
                "latest_commitment_transaction_hash",
            ):
                assert old[key] == new[key], (key, old[key], new[key])
        self._assert_success([self._invoice_payment(0, CKB)])

    def _fulfill_batches(self):
        for round_index in range(3):
            started = time.monotonic()
            held = [
                self._invoice_payment(0, (i + 1) * CKB + round_index + 1, hold=True)
                for i in range(4)
            ]
            for _, _, payment_hash, _ in held:
                self.wait_invoice_state(
                    self.fiber2, payment_hash, "Received", timeout=20
                )
                self.wait_payment_state(
                    self.fiber1, payment_hash, "Inflight", timeout=20
                )
            with ThreadPoolExecutor(max_workers=6) as pool:
                settles = [
                    pool.submit(
                        self.fiber2.get_client().settle_invoice,
                        {"payment_hash": p[2], "payment_preimage": p[3]},
                    )
                    for p in held
                ]
                reverse = [
                    pool.submit(self._invoice_payment, 1, CKB + i + 1) for i in range(2)
                ]
                for future in settles:
                    future.result(timeout=10)
                normal = [future.result(timeout=10) for future in reverse]
            assert time.monotonic() - started < 30, "持单操作超出 30 秒窗口"
            self._assert_success(held + normal)
            print("round:", round_index + 1, "expected balances:", self.expected)
        self._assert_success([self._invoice_payment(0, CKB + 17)])

    def _cancel_batches(self):
        for round_index in range(3):
            started = time.monotonic()
            held = [
                self._invoice_payment(0, (i + 1) * CKB + round_index + 1, hold=True)
                for i in range(4)
            ]
            for _, _, payment_hash, _ in held:
                self.wait_invoice_state(
                    self.fiber2, payment_hash, "Received", timeout=20
                )
                self.wait_payment_state(
                    self.fiber1, payment_hash, "Inflight", timeout=20
                )
            with ThreadPoolExecutor(max_workers=6) as pool:
                cancels = [
                    pool.submit(
                        self.fiber2.get_client().cancel_invoice,
                        {"payment_hash": p[2]},
                    )
                    for p in held
                ]
                reverse = [
                    pool.submit(self._invoice_payment, 1, CKB + i + 1) for i in range(2)
                ]
                for future in cancels:
                    future.result(timeout=10)
                normal = [future.result(timeout=10) for future in reverse]
            assert time.monotonic() - started < 30, "取消操作超出 30 秒窗口"
            for _, _, payment_hash, _ in held:
                self.wait_invoice_state(
                    self.fiber2, payment_hash, "Cancelled", timeout=60
                )
                self.wait_payment_state(self.fiber1, payment_hash, "Failed", timeout=60)
                payment = self.fiber1.get_client().get_payment(
                    {"payment_hash": payment_hash}
                )
                assert payment.get("payment_preimage") is None, payment
                assert payment["failed_error"] == "InvoiceCancelled", payment
            # 取消金额对账本贡献为零；只计反向成功付款，余额必须精确恢复。
            self._assert_success(normal, cancelled={p[2] for p in held})
            print(
                "cancel round:",
                round_index + 1,
                "cancelled payments:",
                len(held),
                "refunded amount:",
                sum(p[1] for p in held),
                "expected balances:",
                self.expected,
            )
        self._assert_success([self._invoice_payment(0, CKB + 17)])

    def _wallets(self):
        capacities = [
            int(
                self.ckb.get_cells_capacity(
                    {
                        "script": lock,
                        "script_type": "lock",
                        "script_search_mode": "exact",
                    }
                )["capacity"],
                16,
            )
            for lock in self.locks
        ]
        tokens = (
            [
                self.udtContract.balance(
                    self.ckb, self.account1["lock_arg"], lock["args"]
                )
                for lock in self.locks
            ]
            if self.udt
            else [0, 0]
        )
        return capacities, tokens

    def _spend(self, previous):
        def observe():
            tx_hash, _ = self.get_ln_cell_death_hash(previous["hash"])
            result = self.ckb.get_transaction(tx_hash) if tx_hash else None
            return (
                result["transaction"]
                if result and result["tx_status"]["status"] == "committed"
                else None
            )

        tx = self._wait(
            "链上 cell 消费确认", observe, lambda value: value is not None, 150
        )
        assert tx["inputs"][0]["previous_output"] == {
            "tx_hash": previous["hash"],
            "index": "0x0",
        }
        return tx

    def _force_close_and_check_funds(self):
        channels = self._assert_balances()
        wallets_before, tokens_before = self._wallets()
        self.fiber1.get_client().shutdown_channel(
            {"channel_id": self.channel_id, "force": True}
        )
        commitment = self._spend(self.funding)
        packed = self.ckb.get_transaction(commitment["hash"], "0x0")["transaction"]
        assert (
            commitment_hash_without_deps(packed)
            == channels[0]["latest_commitment_transaction_hash"]
        )
        assert len(bytes.fromhex(commitment["outputs"][0]["lock"]["args"][2:])) == 57
        commitment_fee = self.get_tx_message(commitment["hash"])["fee"]
        assert 0 < commitment_fee < 10_000
        funding_capacity = int(self.funding["outputs"][0]["capacity"], 16)
        expected_capacity = [0, 0]
        principals = [
            amount + DEFAULT_MIN_LEDGER_DEPOSIT_CKB for amount in self.expected
        ]
        if not self.udt:
            assert sum(principals) == funding_capacity
        seen = set()
        tx = commitment
        for step in range(2):
            self.ckb.generate_epochs("0x2")
            settled = self._spend(tx)
            assert {"out_point": self.contract_point, "dep_type": "code"} in settled[
                "cell_deps"
            ]
            witness = SettlementWitness.from_hex(
                settled["witnesses"][0], version="legacy"
            )
            assert not witness.tlcs and len(witness.unlocks) == 1
            owner = {0xFE: 0, 0xFF: 1}[witness.unlocks[0].unlock_type]
            assert owner not in seen
            seen.add(owner)
            if step == 0:
                expected_witness = self.expected if self.udt else principals
                assert [witness.remote_amount, witness.local_amount] == expected_witness
            inputs = [
                self.ckb.get_transaction(item["previous_output"]["tx_hash"])[
                    "transaction"
                ]["outputs"][int(item["previous_output"]["index"], 16)]
                for item in settled["inputs"]
            ]
            assert len(inputs) > 1 and all(
                c["lock"] == self.locks[owner] for c in inputs[1:]
            )
            fee = sum(int(c["capacity"], 16) for c in inputs) - sum(
                int(c["capacity"], 16) for c in settled["outputs"]
            )
            assert 0 < fee < 10_000
            locked = [
                c
                for c in settled["outputs"]
                if c["lock"]["code_hash"] == COMMIT_LOCK_CODE_HASH
            ]
            assert len(locked) == 1 - step
            if self.udt:
                # 占用容量按链上脚本字节数独立计算，首笔释放多余预留，末笔释放剩余容量。
                occupied = (
                    8
                    + 32
                    + 1
                    + 57
                    + 32
                    + 1
                    + len(bytes.fromhex(self.udt["args"][2:]))
                    + 16
                ) * CKB
                released = (
                    funding_capacity - commitment_fee - occupied
                    if step == 0
                    else occupied
                )
                if step == 0:
                    assert int(locked[0]["capacity"], 16) == occupied
            else:
                released = principals[owner] - (commitment_fee if step == 1 else 0)
            expected_capacity[owner] = released - fee
            net = [
                sum(
                    int(c["capacity"], 16)
                    for c in settled["outputs"]
                    if c["lock"] == lock
                )
                - sum(int(c["capacity"], 16) for c in inputs[1:] if c["lock"] == lock)
                for lock in self.locks
            ]
            assert net[owner] == released - fee and net[1 - owner] == 0, (
                net,
                released,
                fee,
            )
            assert all(
                c["lock"] in self.locks or c in locked for c in settled["outputs"]
            )
            print(
                "settlement:",
                settled["hash"],
                "owner:",
                owner,
                "fee:",
                fee,
                "net capacity:",
                net,
            )
            tx = settled
        assert seen == {0, 1}
        self._wait(
            "钱包最终到账",
            self._wallets,
            lambda value: [a - b for a, b in zip(value[0], wallets_before)]
            == expected_capacity
            and (
                not self.udt
                or [a - b for a, b in zip(value[1], tokens_before)] == self.expected
            ),
        )
        closed = self._wait(
            "双方通道最终关闭",
            self._channels,
            lambda rows: all(
                c["state"]["state_name"] == "Closed"
                and (
                    not self.check_onchain_settlement
                    or "WAITING_ONCHAIN_SETTLEMENT"
                    not in c["state"].get("state_flags", "")
                )
                for c in rows
            ),
            660,
        )
        assert all(not c["pending_tlcs"] for c in closed)
