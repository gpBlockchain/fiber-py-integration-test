"""H32V2-23/26/22：旧库合约下的承诺与两笔 TLC 结算；升级时节点不重启。"""

import secrets
import socket
import subprocess
import time

from framework.basic_fiber import COMMIT_LOCK_CODE_HASH
from framework.helper.settlement_witness import (
    SettlementWitness,
    assert_commitment_args,
    assert_commitment_args_prefix,
    assert_commitment_delay_epoch,
    witness_size,
)
from framework.onchain_tlc_query import onchain_tlc_query_enabled
from framework.test_fiber import FiberConfigPath
from framework.util import ckb_hash
from test_cases.fiber.devnet.compatibility.contract_upgrade_support import (
    ContractUpgradeSupport,
    CKB,
    ROOT,
    OLD_CONTRACT,
    NEW_CONTRACT,
    # 该名字被 test_cases/framework/test_contract_upgrade_oracle.py 从这里再导出使用。
    commitment_hash_without_deps,
)


def udt_amount(output, data, udt):
    """Read one xUDT amount; never mix it with the CKB capacity of the same cell."""
    if output.get("type") != udt:
        return 0
    raw = bytes.fromhex(data.removeprefix("0x"))
    assert len(raw) == 16, "xUDT amount must be a 16-byte little-endian integer"
    return int.from_bytes(raw, "little")


class TestContractUpgradeOnchain(ContractUpgradeSupport):
    fiber_version = FiberConfigPath.V091_DEV
    tmp_path_name = f"report/h32-simple-{time.time_ns()}"
    ckb_rpc_port, ckb_p2p_port = 19814, 19815
    fiber1_rpc_port, fiber1_p2p_port = 19828, 19827
    fiber2_rpc_port, fiber2_p2p_port = 19829, 19830
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 2}

    @classmethod
    def setup_class(cls):
        for port in (
            cls.ckb_rpc_port,
            cls.ckb_p2p_port,
            cls.fiber1_rpc_port,
            cls.fiber1_p2p_port,
            cls.fiber2_rpc_port,
            cls.fiber2_p2p_port,
        ):
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", port))  # 有其他环境占用时，启动前失败。
        version = subprocess.check_output(
            [ROOT / cls.fiber_version.fiber_bin_path, "--version"], text=True
        )
        assert "9a561b3" in version, version
        super().setup_class()
        cls.ckb = cls.node.getClient()
        cls.processes = cls.node_processes()
        print("共享节点 PID / 启动时间:", cls.processes)

    #
    # def setup_method(self, method):
    #     super().setup_method(method)
    #     # 每条用例恢复旧代码、另建通道；共享节点始终不重启。
    #     self.upgrade_contract(OLD_CONTRACT)
    #
    # def teardown_method(self, method):
    #     logs = ROOT / self.tmp_path_name / method.__name__
    #     logs.mkdir(parents=True, exist_ok=True)
    #     shutil.copy2(Path(self.node.ckb_dir) / "node.log", logs / "ckb.log")
    #     for i, fiber in enumerate(self.fibers):
    #         shutil.copy2(Path(fiber.tmp_path) / "node.log", logs / f"fiber{i}.log")
    #     print("节点日志:", logs)

    # ---- xUDT helpers (CKB assert_tlc_settlement / assert_settled stay untouched) ----

    def udt_script(self):
        """fiber1 的 xUDT type script：默认快照只给共享 fiber1 账户发过 xUDT。"""
        owner = self.fiber1.get_account()["lock_arg"]
        return {
            "code_hash": self.udtContract.get_code_hash(True, self.node.rpcUrl),
            "hash_type": "type",
            "args": self.udtContract.get_owner_arg_by_lock_arg(owner),
        }

    def token_balances(self):
        # owner arg 定位代币；query arg 是谁的锁当前持有它。
        owner = self.fiber1.get_account()["lock_arg"]
        return [
            self.udtContract.balance(self.ckb, owner, fiber.get_account()["lock_arg"])
            for fiber in self.fibers
        ]

    def udt_funding_units(self):
        """接收方只在 funding_amount >= udt_cfg_infos.auto_accept_amount 时自动接受 UDT 开通。

        dev 模板默认 1_000_000_000，所以 UDT 通道请求必须用这个量级的 UDT 单位。
        """
        infos = self.fiber2.get_client().node_info().get("udt_cfg_infos") or []
        amounts = [
            int(info["auto_accept_amount"], 16)
            for info in infos
            if info.get("auto_accept_amount")
        ]
        return (max(amounts) if amounts else 1_000_000_000) + 1

    def open_udt_channel(self, udt, funding_units=None):
        """用原始 RPC 开通 xUDT 通道（框架 open_channel 的 udt 分支会混入 CKB 量级常量）。"""
        self.fiber1.connect_peer(self.fiber2)
        previous = {
            c["channel_id"]
            for c in self.fiber1.get_client().list_channels({})["channels"]
        }
        self.fiber1.get_client().open_channel(
            {
                "pubkey": self.fiber2.get_pubkey(),
                "public": True,
                "funding_amount": hex(
                    self.udt_funding_units() if funding_units is None else funding_units
                ),
                "funding_udt_type_script": udt,
            }
        )
        self.channel_id = self.wait_for_new_channel_state(
            self.fiber1.get_client(), self.fiber2.get_pubkey(), "ChannelReady", previous
        )
        for _ in range(60):
            if all(
                self.channel(f)["state"]["state_name"] == "ChannelReady"
                for f in self.fibers
            ):
                break
            time.sleep(1)
        else:
            self.fail(
                f"Both UDT channel ends must be ready: {[self.channel(f) for f in self.fibers]}"
            )
        channels = [self.channel(f) for f in self.fibers]
        assert all(
            c["funding_udt_type_script"] == udt and not c["pending_tlcs"]
            for c in channels
        )
        raw = bytes.fromhex(channels[0]["channel_outpoint"][2:])
        assert len(raw) == 36 and raw[32:] == bytes(4)
        self.funding_tx = "0x" + raw[:32].hex()
        self.signed_hashes = [c["latest_commitment_transaction_hash"] for c in channels]
        self.udt_principals = [int(c["local_balance"], 16) for c in channels]
        funding = self.ckb.get_transaction(self.funding_tx)["transaction"]
        assert sum(self.udt_principals) == udt_amount(
            funding["outputs"][0], funding["outputs_data"][0], udt
        )
        self.wallet_before = self.wallet_balances()
        self.tokens_before = self.token_balances()

    def assert_tlc_settlement_udt(self, previous, tx, code_tx, pending, preimage, udt):
        """xUDT 版 TLC 结算断言；CKB 版 assert_tlc_settlement 保持不变。"""
        version = getattr(self, "commitment_version", "legacy")
        assert {
            "out_point": {"tx_hash": code_tx, "index": "0x0"},
            "dep_type": "code",
        } in tx["cell_deps"]
        # 转移 xUDT 必须带上已部署 xUDT 代码 cell。
        assert {
            "out_point": {"tx_hash": self.udtContract.contract_hash, "index": "0x0"},
            "dep_type": "code",
        } in tx["cell_deps"]
        witness = SettlementWitness.from_hex(tx["witnesses"][0], version=version)
        assert witness.to_hex() == tx["witnesses"][0]
        witness.assert_single_tlc_claim(pending, preimage)
        assert len(bytes.fromhex(tx["witnesses"][0][2:])) == witness_size(
            version, len(pending)
        )
        amount = witness.tlcs[witness.unlocks[0].unlock_type].amount
        before, after = previous["outputs"][0], tx["outputs"][0]
        assert after["lock"]["code_hash"] == COMMIT_LOCK_CODE_HASH
        assert after["lock"]["hash_type"] == "type"
        args = bytes.fromhex(after["lock"]["args"][2:])
        # 派生输出：args[56] 状态标志必须为 1（两版相同）；V1 末尾另有 feature 字节。
        assert_commitment_args(args, version, derived=True)
        assert_commitment_args_prefix(args, bytes.fromhex(before["lock"]["args"][2:]))
        # 只比较本资产：xUDT 读 output_data，不用 capacity。
        assert before["type"] == after["type"] == udt
        assert (
            udt_amount(before, previous["outputs_data"][0], udt)
            - udt_amount(after, tx["outputs_data"][0], udt)
            == amount
        )
        net = [0, 0]
        locks = [self.get_account_script(f.account_private) for f in self.fibers]
        for sign, transaction, indices in [(1, tx, range(len(tx["outputs"])))] + [
            (
                -1,
                self.ckb.get_transaction(i["previous_output"]["tx_hash"])[
                    "transaction"
                ],
                [int(i["previous_output"]["index"], 16)],
            )
            for i in tx["inputs"]
        ]:
            for index in indices:
                output = transaction["outputs"][index]
                for owner, lock in enumerate(locks):
                    if output["lock"] == lock:
                        net[owner] += sign * udt_amount(
                            output, transaction["outputs_data"][index], udt
                        )
        # xUDT 转账本身不计费，收款人净增量恰为 TLC 金额。
        assert net == [0, amount], net
        # 派生 cell 里还有未结算 TLC 时节点不能 sweep 它，必然 live；收尾那笔（本次结算后
        # 不再有待结算 TLC）的派生 cell 会被随即 sweep，CKB 0.202 对已花费的承诺 cell 返回
        # "unknown" 而不是 "dead"，其去向由 assert_settled_udt 的 sweep 断言核对，不能在这里
        # 要求 live。注意 output 0 恒为 commitment-lock（见上方 assert），不能拿它的 code_hash
        # 当“是否收尾”的判断依据。
        if len(pending) > 1:
            assert self.ckb.get_live_cell("0x0", tx["hash"])["status"] == "live"
        self.assert_nodes_running()

    def assert_settled_udt(self, commitment, code_tx, udt, prior_fees=0):
        """xUDT 版收尾断言；不复用 CKB 的 capacity/钱包本金核算。"""
        version = getattr(self, "commitment_version", "legacy")
        tx = commitment
        fees = prior_fees + self.get_tx_message(tx["hash"])["fee"]
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
            args = bytes.fromhex(lock["args"][2:])
            assert lock["hash_type"] == "type"
            assert_commitment_args(args, version)
            assert_commitment_delay_epoch(args)
            assert locked[0]["type"] == udt
            self.ckb.generate_epochs("0x2")
            tx = self.wait_for_spend(tx["hash"])
            assert {
                "out_point": {"tx_hash": code_tx, "index": "0x0"},
                "dep_type": "code",
            } in tx["cell_deps"]
            assert {
                "out_point": {
                    "tx_hash": self.udtContract.contract_hash,
                    "index": "0x0",
                },
                "dep_type": "code",
            } in tx["cell_deps"]
            fees += self.get_tx_message(tx["hash"])["fee"]
        else:
            self.fail("仍有待结算 commitment cell")
        ckb_delta = [a - b for a, b in zip(self.wallet_balances(), self.wallet_before)]
        token_delta = [a - b for a, b in zip(self.token_balances(), self.tokens_before)]
        funding = self.ckb.get_transaction(self.funding_tx)["transaction"]["outputs"][0]
        assert 0 <= fees < CKB // 100
        # 通道 CKB capacity 扣真实矿工费后返还双方；xUDT 数额按 local_balance 归属且不计费。
        assert sum(ckb_delta) == int(funding["capacity"], 16) - fees
        assert token_delta == self.udt_principals, (token_delta, self.udt_principals)
        self.assert_nodes_running()

    # TEST-MAP: H32V2-26
    # TEST-EVIDENCE-BEGIN: H32V2-26
    # Evidence | partial | Real old->new code window: OLD_CONTRACT is installed while the node keeps
    # running, the commitment is broadcast under that live old code, only then NEW_CONTRACT replaces it
    # and the settlement consumes the new bytes. CKB/no-TLC here; the xUDT variant lives in
    # test_local_commitment_before_upgrade_xudt, the expiry-refund branch is covered by H32V2-10/25 and the
    # post-upgrade V1 control lives in H32V2-21 (this class runs PR-base nodes, which cannot negotiate V1).
    # TEST-EVIDENCE-END: H32V2-26
    # H32V2-26 评审行（reviews/full-payment-hash-settlement-v2.md）：
    #   场景：Legacy 承诺在旧合约下已上链且首次 settle 尚未发生，节点持续运行中升级合约再结算；
    #         覆盖本端/远端承诺、CKB/xUDT，并以升级后新建 V1 作对照。
    #   预期：首次及后续 settle 引用升级后的 live 代码并确认，布局保持原版本、金额正确，
    #         无遗留应结算 cell，且全程不重启。
    #   防止：只测升级后广播或中间派生状态，遗漏已上链原始承诺的升级窗口。
    # 三个方法的证明点一致，分别覆盖本端强关 + CKB（本方法）、远端强关 + CKB
    # （test_remote_commitment_before_upgrade）、本端强关 + xUDT
    # （test_local_commitment_before_upgrade_xudt）：
    #   证明点 1（窗口真实）：先真实安装 OLD_CONTRACT，并在旧代码仍 live 时广播承诺 —— 承诺上链
    #     时旧代码是 live 版本，才存在“已上链原始承诺升级前”这段窗口。
    #   证明点 2（版本前提）：open_legacy_channel 固定旧版本节点（无法协商 V1），并核对链上
    #     funding 输出锁在 FundingLock 的 20 字节聚合公钥哈希上。承诺布局无法在开通时读到
    #     （承诺锁只出现在强关/结算交易里），所以 57 字节 Legacy 承诺 args 由强关后的链上
    #     承诺交易核对（见证明点 7 的 assert_settled / assert_tlc_settlement）。
    #   证明点 3（未提前结算）：链上确认承诺 cell 仍 live，说明升级前没有发生第一次 settle。
    #   证明点 4（升级是真实替换）：OLD/NEW 字节不同，且 type-id 代码 cell 被换成新 tx；
    #     upgrade_contract 会核对新 cell 的 type/lock、花费旧 outpoint、output_data 等于文件字节。
    #   证明点 5（旧承诺未被改写）：不在升级前重签或换承诺；assert_settled 逐笔消费同一个承诺哈希，
    #     并由 force_close 断言链上交易去掉 deps 后仍等于升级前已签名承诺。
    #   证明点 6（首次 settle 执行新代码）：每笔消费的 cell_deps 必须含升级后代码 cell 的 outpoint。
    #   证明点 7（布局保持原版本）：派生输出的承诺 args 仍是 57 字节 Legacy，不降级也不切 V1。
    #   证明点 8（金额正确、无遗留）：本金按真实手续费守恒、到账方与录音本金相符，消费链结束后
    #     不再有 commitment-lock 输出。
    #   证明点 9（全程不重启）：assert_nodes_running 比较 (PID, 进程启动时间) 基线，升级与结算
    #     全程原进程运行。
    # 本类未覆盖（该类使用 PR base 9a561b3 节点，双方都无法协商 V1；见本类 TEST-EVIDENCE）：
    #   有 TLC 的有效原像/到期退款分支、升级后新建 V1 对照（由 H32V2-10/25 与 H32V2-21 覆盖），
    #   以及远端强关的 xUDT 变体。
    def test_local_commitment_before_upgrade(self):
        # 证明点 1：先装回 PR base 合约：承诺必须在旧代码仍 live 时上链，才存在“已上链原始承诺升级前”窗口。
        old_code = self.upgrade_contract(OLD_CONTRACT)
        # 证明点 2：open_legacy_channel 已确保两端是旧版本（无法协商 V1）；57 字节 Legacy 布局
        # 由 force_close 广播的链上承诺交易核对（assert_settled 的 args 断言）。
        self.open_legacy_channel()
        # 证明点 3/5：本端强关广播原承诺；force_close 顺带断言去掉 deps 后等于已签名承诺、cell 仍 live。
        commitment = self.force_close(self.fiber1)
        assert self.ckb.get_live_cell("0x0", commitment["hash"])["status"] == "live"
        # 证明点 4：真实替换 type-id 代码 cell（内容不同、tx 不同，升级函数内部已核对 output_data 等）。
        code_tx = self.upgrade_contract(NEW_CONTRACT)
        assert OLD_CONTRACT.read_bytes() != NEW_CONTRACT.read_bytes()
        assert code_tx != old_code
        # 不在升级前先结算：commitment 仍 live 时换成新代码，随后首次 settle 必须执行新字节。
        # 证明点 6-9：assert_settled 逐笔核对新代码 dep、57 字节布局、无遗留 cell、金额守恒与节点未重启。
        self.assert_settled(commitment, code_tx)

    # TEST-MAP: H32V2-26
    # TEST-EVIDENCE-BEGIN: H32V2-26
    # Evidence | partial | Same old->new window with the remote side as closer; see the local method above.
    # TEST-EVIDENCE-END: H32V2-26
    # H32V2-26 证明点 1-9 与本类 test_local_commitment_before_upgrade 完全相同，差别只有强关方：
    #   这里由对端（fiber2）强关，用来证明“本端/远端承诺”两种方向的已上链原始承诺都能跨升级结算，
    #   而不是只覆盖本端记录的那一份。布局仍是 57 字节 Legacy，仍要求首次 settle 执行升级后代码。
    def test_remote_commitment_before_upgrade(self):
        # 证明点 1：旧代码仍 live 时广播对端承诺。
        old_code = self.upgrade_contract(OLD_CONTRACT)
        self.open_legacy_channel()
        # 证明点 3/5：对端强关广播的是它自己那份已签名承诺（force_close 按 fibers.index 比对哈希）。
        commitment = self.force_close(self.fiber2)
        assert self.ckb.get_live_cell("0x0", commitment["hash"])["status"] == "live"
        # 证明点 4：升级前承诺仍未被 settle，此时才真实替换代码。
        code_tx = self.upgrade_contract(NEW_CONTRACT)
        assert OLD_CONTRACT.read_bytes() != NEW_CONTRACT.read_bytes()
        assert code_tx != old_code
        # 证明点 6-9：首次 settle 必须引用新代码 dep，并保持 Legacy 布局、金额正确且全程不重启。
        self.assert_settled(commitment, code_tx)

    # TEST-MAP: H32V2-22
    # TEST-EVIDENCE-BEGIN: H32V2-22
    # Evidence | partial | Signature-then-upgrade window is real: the stored commitment is signed while
    # OLD_CONTRACT is the live code, NEW_CONTRACT replaces it before the force close, and force_close
    # re-verifies that the broadcast tx minus deps is still the pre-upgrade signed commitment. CKB/no-TLC
    # here; the two committed-TLC, multi-step settlement and UDT parts live in H32V2-23 and are not
    # claimed by this method.
    # TEST-EVIDENCE-END: H32V2-22
    def test_local_force_close_after_upgrade(self):
        old_code = self.upgrade_contract(OLD_CONTRACT)
        self.open_legacy_channel()
        code_tx = self.upgrade_contract(NEW_CONTRACT)
        assert OLD_CONTRACT.read_bytes() != NEW_CONTRACT.read_bytes()
        assert code_tx != old_code
        # 升级不改写已签名承诺：force_close 用 deps 之外的哈希比对原承诺。
        commitment = self.force_close(self.fiber1)
        self.assert_settled(commitment, code_tx)
        self.assert_local_closed()

    # TEST-MAP: H32V2-22
    # TEST-EVIDENCE-BEGIN: H32V2-22
    # Evidence | partial | Same signature-then-upgrade window with the remote side as closer; see above.
    # TEST-EVIDENCE-END: H32V2-22
    def test_remote_force_close_after_upgrade(self):
        old_code = self.upgrade_contract(OLD_CONTRACT)
        self.open_legacy_channel()
        code_tx = self.upgrade_contract(NEW_CONTRACT)
        assert OLD_CONTRACT.read_bytes() != NEW_CONTRACT.read_bytes()
        assert code_tx != old_code
        commitment = self.force_close(self.fiber2)
        self.assert_settled(commitment, code_tx)
        self.assert_local_closed()

    # TEST-MAP: H32V2-23
    # TEST-EVIDENCE-BEGIN: H32V2-23
    # Evidence | partial | Legacy/CKB, local force-close, two valid preimages: the derived cell is spent
    # by the OLD code for the first TLC and by the NEW bytes for the second, with both code deps asserted,
    # final payments Success with the matching preimages, TLCs terminal and the channel Closed. The xUDT
    # variant lives in test_upgrade_between_two_xudt_tlc_settlements. Missing here: remaining-TLC expiry
    # refund, remote-closer and the post-upgrade V1 control.
    # TEST-EVIDENCE-END: H32V2-23
    def test_upgrade_between_two_tlc_settlements(self):
        old_code = self.upgrade_contract(OLD_CONTRACT)
        self.open_legacy_channel()
        preimages = ["0x" + secrets.token_hex(32) for _ in range(2)]
        payments = [
            (ckb_hash(p), amount) for p, amount in zip(preimages, (CKB, 2 * CKB))
        ]
        for payment_hash, amount in payments:
            invoice = self.fiber2.get_client().new_invoice(
                {
                    "amount": hex(amount),
                    "currency": "Fibd",
                    "payment_hash": payment_hash,
                    "hash_algorithm": "ckb_hash",
                    "expiry": "0xe10",
                }
            )
            self.fiber1.get_client().send_payment(
                {"invoice": invoice["invoice_address"]}
            )
            self.wait_payment_state(self.fiber1, payment_hash, "Inflight")
            self.wait_invoice_state(self.fiber2, payment_hash, "Received")

        # 发票 Received 不等于双方已签好包含两笔 TLC 的承诺。
        for _ in range(60):
            channels = [self.channel(f) for f in self.fibers]
            if all(
                len(c["pending_tlcs"]) == 2
                and all("Committed" in t["status"].values() for t in c["pending_tlcs"])
                for c in channels
            ):
                break
            time.sleep(1)
        else:
            self.fail(f"两笔 TLC 未完成承诺: {channels}")
        for channel in channels:
            assert sorted(
                (t["payment_hash"], int(t["amount"], 16))
                for t in channel["pending_tlcs"]
            ) == sorted(payments)
        self.signed_hashes = [c["latest_commitment_transaction_hash"] for c in channels]
        commitment = self.force_close(self.fiber1)
        self.ckb.generate_epochs("0x1")

        # 只公布第一笔原像：旧合约花费原承诺，留下仍锁定第二笔的派生 cell。
        self.fiber2.get_client().settle_invoice(
            {"payment_hash": payments[0][0], "payment_preimage": preimages[0]}
        )
        first = self.wait_for_spend(commitment["hash"])
        self.assert_tlc_settlement(commitment, first, old_code, payments, preimages[0])
        assert self.ckb.get_live_cell("0x0", first["hash"])["status"] == "live"

        # 在两次 TLC 花费之间原位升级；第二笔原像此前从未交给节点。
        new_code = self.upgrade_contract(NEW_CONTRACT)
        assert OLD_CONTRACT.read_bytes() != NEW_CONTRACT.read_bytes()
        self.fiber2.get_client().settle_invoice(
            {"payment_hash": payments[1][0], "payment_preimage": preimages[1]}
        )
        second = self.wait_for_spend(first["hash"])
        self.assert_tlc_settlement(first, second, new_code, payments[1:], preimages[1])

        self.principals[0] -= 3 * CKB
        self.principals[1] += 3 * CKB
        prior_fees = sum(
            self.get_tx_message(tx["hash"])["fee"] for tx in (commitment, first)
        )
        self.assert_settled(second, new_code, prior_fees)
        # 两笔都以原像兑现收尾，本端记录 Success 且持有正确原像；不留下未结算 TLC。
        # 上链产出的付款/TLC 终态查询由 FIBER_ASSERT_ONCHAIN_TLC_QUERY 门控；关时只核对没有被
        # 判成失败。
        if onchain_tlc_query_enabled():
            for preimage, (payment_hash, _) in zip(preimages, payments):
                self.wait_payment_state(
                    self.fiber1, payment_hash, "Success", timeout=660
                )
                assert (
                    self.fiber1.get_client().get_payment(
                        {"payment_hash": payment_hash}
                    )["payment_preimage"]
                    == preimage
                )
            for payment_hash, _ in payments:
                self.wait_tlc_terminal(self.fiber1, payment_hash)
        else:
            for _, (payment_hash, _) in zip(preimages, payments):
                assert (
                    self.fiber1.get_client().get_payment(
                        {"payment_hash": payment_hash}
                    )["status"]
                    != "Failed"
                )
        self.assert_local_closed()

    # TEST-MAP: H32V2-23
    # TEST-EVIDENCE-BEGIN: H32V2-23
    # Evidence | partial | xUDT/Legacy, local force-close, two valid preimages: the derived xUDT cell is
    # spent by the OLD code for the first TLC and by the NEW bytes for the second, with both code deps and
    # the xUDT code dep asserted, the per-TLC xUDT amount/recipient net delta checked, final payments
    # Success with matching preimages, TLCs terminal and the channel Closed. Not covered: remaining-TLC
    # expiry refund, remote-closer, the post-upgrade V1 control and xUDT fee accounting.
    # TEST-EVIDENCE-END: H32V2-23
    def test_upgrade_between_two_xudt_tlc_settlements(self):
        old_code = self.upgrade_contract(OLD_CONTRACT)
        udt = self.udt_script()
        self.open_udt_channel(udt)
        preimages = ["0x" + secrets.token_hex(32) for _ in range(2)]
        payments = [(ckb_hash(p), amount) for p, amount in zip(preimages, (100, 200))]
        for payment_hash, amount in payments:
            invoice = self.fiber2.get_client().new_invoice(
                {
                    "amount": hex(amount),
                    "currency": "Fibd",
                    "payment_hash": payment_hash,
                    "hash_algorithm": "ckb_hash",
                    "final_expiry_delta": hex(9_600_000),
                    "expiry": "0xe10",
                    "udt_type_script": udt,
                }
            )
            self.fiber1.get_client().send_payment(
                {"invoice": invoice["invoice_address"]}
            )
            self.wait_payment_state(self.fiber1, payment_hash, "Inflight")
            self.wait_invoice_state(self.fiber2, payment_hash, "Received")

        # 发票 Received 不等于双方已签好包含两笔 TLC 的承诺。
        for _ in range(60):
            channels = [self.channel(f) for f in self.fibers]
            if all(
                len(c["pending_tlcs"]) == 2
                and all("Committed" in t["status"].values() for t in c["pending_tlcs"])
                for c in channels
            ):
                break
            time.sleep(1)
        else:
            self.fail(f"两笔 xUDT TLC 未完成承诺: {channels}")
        for channel in channels:
            assert sorted(
                (t["payment_hash"], int(t["amount"], 16))
                for t in channel["pending_tlcs"]
            ) == sorted(payments)
        self.signed_hashes = [c["latest_commitment_transaction_hash"] for c in channels]
        commitment = self.force_close(self.fiber1)
        self.ckb.generate_epochs("0x1")

        # 只公布第一笔原像：旧合约花费原承诺，留下仍锁定第二笔的派生 xUDT cell。
        self.fiber2.get_client().settle_invoice(
            {"payment_hash": payments[0][0], "payment_preimage": preimages[0]}
        )
        first = self.wait_for_spend(commitment["hash"])
        self.assert_tlc_settlement_udt(
            commitment, first, old_code, payments, preimages[0], udt
        )
        assert self.ckb.get_live_cell("0x0", first["hash"])["status"] == "live"

        # 在两次 TLC 花费之间原位升级；第二笔原像此前从未交给节点。
        new_code = self.upgrade_contract(NEW_CONTRACT)
        assert OLD_CONTRACT.read_bytes() != NEW_CONTRACT.read_bytes()
        self.fiber2.get_client().settle_invoice(
            {"payment_hash": payments[1][0], "payment_preimage": preimages[1]}
        )
        second = self.wait_for_spend(first["hash"])
        self.assert_tlc_settlement_udt(
            first, second, new_code, payments[1:], preimages[1], udt
        )

        transferred = sum(amount for _, amount in payments)
        self.udt_principals[0] -= transferred
        self.udt_principals[1] += transferred
        prior_fees = sum(
            self.get_tx_message(tx["hash"])["fee"] for tx in (commitment, first)
        )
        self.assert_settled_udt(second, new_code, udt, prior_fees)
        # 两笔都以原像兑现收尾，本端记录 Success 且持有正确原像；不留下未结算 TLC。
        # 上链产出的付款/TLC 终态查询由 FIBER_ASSERT_ONCHAIN_TLC_QUERY 门控；关时只核对没有被
        # 判成失败。
        if onchain_tlc_query_enabled():
            for preimage, (payment_hash, _) in zip(preimages, payments):
                self.wait_payment_state(
                    self.fiber1, payment_hash, "Success", timeout=660
                )
                assert (
                    self.fiber1.get_client().get_payment(
                        {"payment_hash": payment_hash}
                    )["payment_preimage"]
                    == preimage
                )
            for payment_hash, _ in payments:
                self.wait_tlc_terminal(self.fiber1, payment_hash)
        else:
            for _, (payment_hash, _) in zip(preimages, payments):
                assert (
                    self.fiber1.get_client().get_payment(
                        {"payment_hash": payment_hash}
                    )["status"]
                    != "Failed"
                )
        self.assert_local_closed()

    # TEST-MAP: H32V2-26
    # TEST-EVIDENCE-BEGIN: H32V2-26
    # Evidence | partial | xUDT/Legacy: OLD_CONTRACT is live when the xUDT commitment is broadcast, the
    # node keeps running while NEW_CONTRACT replaces it, and the first settlement that consumes that
    # commitment carries the new code dep plus the xUDT code dep and returns the xUDT to the recorded
    # balances (CKB capacity minus the real fee). No TLC in this variant; the expiry-refund branch is
    # covered by H32V2-10/25, the remote-closer by test_remote_commitment_before_upgrade and the
    # post-upgrade V1 control by H32V2-21 (PR-base nodes cannot negotiate V1).
    # TEST-EVIDENCE-END: H32V2-26
    # H32V2-26 证明点 1-9 与本类 CKB 方法相同，本方法只把资产换成 xUDT（同一旧→新窗口、同一
    # 本端强关方向）：
    #   证明点 1：xUDT 承诺必须在旧代码仍 live 时上链，才存在“已上链原始承诺升级前”窗口。
    #   证明点 2：open_udt_channel 用的仍是同两个旧版本节点（无法协商 V1）；57 字节 Legacy
    #     布局由强关后的链上承诺/派生 xUDT 输出核对（assert_settled_udt）。
    #   证明点 3：链上确认承诺 cell 仍 live，升级前没有发生首次 settle。
    #   证明点 4：真实替换 type-id 代码 cell。
    #   证明点 6-8：assert_settled_udt 要求首次消费同时带升级后代码 dep 与 xUDT 代码 dep，
    #     派生输出保持 57 字节 Legacy 布局与相同 xUDT type，xUDT 数额按记录的双方本金归还
    #     （按归属方核对，不与同一 cell 的 CKB capacity 混算），CKB capacity 只扣真实矿工费。
    #   证明点 9：升级与结算全程原进程运行。
    # 未覆盖：xUDT 的远端强关方向、含 TLC 的有效原像/到期退款分支、升级后新建 V1 对照
    # （分别由 test_remote_commitment_before_upgrade、H32V2-10/25、H32V2-21 覆盖）。
    def test_local_commitment_before_upgrade_xudt(self):
        # 证明点 1：先装回 PR base 合约：xUDT 承诺必须在旧代码仍 live 时上链，才存在“已上链原始承诺升级前”窗口。
        old_code = self.upgrade_contract(OLD_CONTRACT)
        udt = self.udt_script()
        # 证明点 2：xUDT 通道仍由旧版本对开（无法协商 V1）；57 字节 Legacy 布局由强关后的
        # 链上承诺交易核对（assert_settled_udt 的 args 断言）。
        self.open_udt_channel(udt)
        # 证明点 3：本端强关广播原 xUDT 承诺，且确认后 cell 仍 live（未提前 settle）。
        commitment = self.force_close(self.fiber1)
        assert self.ckb.get_live_cell("0x0", commitment["hash"])["status"] == "live"
        # 证明点 4：真实替换代码 cell。
        code_tx = self.upgrade_contract(NEW_CONTRACT)
        assert OLD_CONTRACT.read_bytes() != NEW_CONTRACT.read_bytes()
        assert code_tx != old_code
        # 不在升级前先结算：commitment 仍 live 时换成新代码，随后首次 settle 必须执行新字节。
        # 证明点 6-9：新代码 dep + xUDT dep、原版本布局、xUDT 数额正确归还、全程不重启。
        self.assert_settled_udt(commitment, code_tx, udt)
