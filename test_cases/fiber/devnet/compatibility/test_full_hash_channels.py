"""H32: current/old nodes, ordinary/external funding and real settlement.

New nodes use the framework default `FiberConfigPath.CURRENT_DEV`，无需额外指定版本；
旧节点用例固定用 `FiberConfigPath.V091_DEV`。Shared nodes are never restarted by upgrade
cases. Each subtest owns a fresh channel.
"""

import hashlib
import secrets
import socket
import subprocess
import time

from framework.basic_fiber import COMMIT_LOCK_CODE_HASH
from framework.config import (
    DEFAULT_MIN_DEPOSIT_CKB,
    DEFAULT_MIN_LEDGER_DEPOSIT_CKB,
)
from framework.test_fiber import FiberConfigPath
from framework.util import ckb_hash
from framework.helper.settlement_witness import (
    SettlementWitness,
    assert_commitment_args,
    assert_commitment_args_prefix,
    assert_commitment_delay_epoch,
    witness_size,
)
from framework.onchain_tlc_query import onchain_tlc_query_enabled
from test_cases.fiber.devnet.compatibility.contract_upgrade_support import (
    CKB,
    ROOT,
    NEW_CONTRACT,
    OLD_CONTRACT,
    ContractUpgradeSupport,
)


def udt_amount(output, data, udt):
    """Read one xUDT amount; never mix it with the CKB capacity of the same cell."""
    if output.get("type") != udt:
        return 0
    raw = bytes.fromhex(data.removeprefix("0x"))
    assert len(raw) == 16, "xUDT amount must be a 16-byte little-endian integer"
    return int.from_bytes(raw, "little")


class TestFullHashChannels(ContractUpgradeSupport):
    tmp_path_name = f"report/h32-full-channels-{time.time_ns()}"
    ckb_rpc_port, ckb_p2p_port = 20014, 20015
    fiber1_rpc_port, fiber1_p2p_port = 20028, 20027
    fiber2_rpc_port, fiber2_p2p_port = 20029, 20030
    extra_fiber_rpc_port, extra_fiber_p2p_port = 20100, 20200
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 2}

    @classmethod
    def setup_class(cls):
        for port in (
            20014,
            20015,
            20028,
            20027,
            20029,
            20030,
            20100,
            20101,
            20102,
            20200,
            20201,
            20202,
        ):
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", port))
        # 旧节点二进制必须固定（混合版本用例依赖它是 PR base）；新节点用框架默认 CURRENT_DEV。
        version = subprocess.check_output(
            [ROOT / FiberConfigPath.V091_DEV.fiber_bin_path, "--version"], text=True
        )
        assert "9a561b3" in version, version
        super().setup_class()
        cls.new1, cls.new2 = cls.fiber1, cls.fiber2
        cls.ckb = cls.node.getClient()
        cls.processes = None

    def setUp(self):
        cls = self.__class__
        if not getattr(cls, "_peers_ready", False):
            cls.old1 = self.start_new_fiber(
                self.generate_account(5000), fiber_version=FiberConfigPath.V091_DEV
            )
            cls.old2 = self.start_new_fiber(
                self.generate_account(5000), fiber_version=FiberConfigPath.V091_DEV
            )
            cls.manual = self.start_new_fiber(
                self.generate_account(5000),
                config={
                    "ckb_rpc_url": self.node.rpcUrl,
                    "fiber_auto_accept_channel_ckb_funding_amount": 0,
                },
            )
            cls.processes = cls.node_processes()
            cls._peers_ready = True

    @classmethod
    def node_processes(cls):
        processes = []
        for port in (
            cls.ckb_rpc_port,
            cls.fiber1_rpc_port,
            cls.fiber2_rpc_port,
            20100,
            20101,
            20102,
        ):
            pid = subprocess.check_output(
                ["lsof", "-nP", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"], text=True
            ).strip()
            start = subprocess.check_output(
                ["ps", "-p", pid, "-o", "lstart="], text=True
            ).strip()
            processes.append((pid, start))
        return processes

    def select_peers(self, sender, receiver, version):
        # Instance-only aliases; class-level SharedFiberTest cleanup still owns all five nodes.
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
                        "funding_amount": hex(
                            1000 * CKB + DEFAULT_MIN_LEDGER_DEPOSIT_CKB
                        ),
                        "public": True,
                        "shutdown_script": funding_lock,
                        "funding_lock_script": funding_lock,
                    }
                ],
            )
            # 签名是外部钱包操作：用 ckb-cli 在 Python 侧签 unsigned funding tx，不调用 dev 模块的
            # sign_external_funding_tx（release 构建不注册 dev 模块，调用只会得到 Method not found）。
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

    def record_channel(self, udt=None):
        channels = [self.channel(f) for f in self.fibers]
        outpoint = bytes.fromhex(channels[0]["channel_outpoint"][2:])
        assert outpoint[32:] == bytes(4)
        self.funding_tx = "0x" + outpoint[:32].hex()
        self.signed_hashes = [c["latest_commitment_transaction_hash"] for c in channels]
        if udt is None:
            reserve = (
                DEFAULT_MIN_DEPOSIT_CKB
                if self.commitment_version == "v1"
                else DEFAULT_MIN_LEDGER_DEPOSIT_CKB
            )
            self.principals = [int(c["local_balance"], 16) + reserve for c in channels]
            self.wallet_before = self.wallet_balances()
            return
        # xUDT 通道：capacity 预留只适用于 CKB 通道，代币数额只读 output_data 的 16 字节小额端。
        assert all(c["funding_udt_type_script"] == udt for c in channels)
        self.udt_principals = [int(c["local_balance"], 16) for c in channels]
        funding = self.ckb.get_transaction(self.funding_tx)["transaction"]
        assert sum(self.udt_principals) == udt_amount(
            funding["outputs"][0], funding["outputs_data"][0], udt
        )
        self.wallet_before = self.wallet_balances()
        self.tokens_before = self.token_balances()

    def udt_script(self):
        """new1 的 xUDT type script：默认快照只给共享 fiber1 账户发过 xUDT。"""
        owner = self.new1.get_account()["lock_arg"]
        return {
            "code_hash": self.udtContract.get_code_hash(True, self.node.rpcUrl),
            "hash_type": "type",
            "args": self.udtContract.get_owner_arg_by_lock_arg(owner),
        }

    def token_balances(self):
        # owner arg 定位代币；query arg 是谁的锁当前持有它。
        owner = self.new1.get_account()["lock_arg"]
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

    def open_ready_udt(self, udt, funding_units=None):
        """用原始 RPC 开通 xUDT 通道。

        框架 `open_channel` 的 udt 分支会把 DEFAULT_MIN_DEPOSIT_CKB（CKB 量级）加到 UDT
        单位上，所以这里显式发 funding_udt_type_script，接收方按 UDT 规则自动接受（0 出资）。
        """
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
        self.record_channel(udt)

    def assert_tlc_settlement_udt(self, previous, tx, code_tx, pending, preimage, udt):
        """xUDT 版 TLC 结算断言；CKB 版 assert_tlc_settlement 保持不变。"""
        assert {
            "out_point": {"tx_hash": code_tx, "index": "0x0"},
            "dep_type": "code",
        } in tx["cell_deps"]
        # 转移 xUDT 必须带上已部署 xUDT 代码 cell，否则资产类型无法通过验证。
        assert {
            "out_point": {"tx_hash": self.udtContract.contract_hash, "index": "0x0"},
            "dep_type": "code",
        } in tx["cell_deps"]
        version = self.commitment_version
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
            assert_commitment_args(args, self.commitment_version)
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

    def hold_one_payment(self, amount=CKB, algorithm="ckb_hash", udt=None):
        preimage = "0x" + secrets.token_hex(32)
        payment_hash = (
            ckb_hash(preimage)
            if algorithm == "ckb_hash"
            else "0x" + hashlib.sha256(bytes.fromhex(preimage[2:])).hexdigest()
        )
        invoice_params = {
            "amount": hex(amount),
            "currency": "Fibd",
            "payment_hash": payment_hash,
            "hash_algorithm": algorithm,
            # Common supported bound: old nodes require at least 160 minutes.
            "final_expiry_delta": hex(9_600_000),
            "expiry": "0xe10",
        }
        if udt is not None:
            invoice_params["udt_type_script"] = udt
        invoice = self.fiber2.get_client().new_invoice(invoice_params)
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
                return payment_hash, preimage
            time.sleep(1)
        self.fail(f"Pending TLC was not committed on selected channel: {channels}")

    def settle_held_channel(
        self, code_tx, payment_hash, preimage, amount=CKB, closer=None, udt=None
    ):
        commitment = self.force_close(closer or self.fiber2)
        args = bytes.fromhex(commitment["outputs"][0]["lock"]["args"][2:])
        # 首次承诺：状态标志 args[56] 必须为 0；V1 末尾另有 feature 字节。
        assert_commitment_args(args, self.commitment_version, derived=False)
        self.ckb.generate_epochs("0x1")
        self.fiber2.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )
        settled = self.wait_for_spend(commitment["hash"])
        if udt is None:
            self.assert_tlc_settlement(
                commitment, settled, code_tx, [(payment_hash, amount)], preimage
            )
            self.principals[0] -= amount
            self.principals[1] += amount
            self.assert_settled(
                settled, code_tx, self.get_tx_message(commitment["hash"])["fee"]
            )
        else:
            self.assert_tlc_settlement_udt(
                commitment, settled, code_tx, [(payment_hash, amount)], preimage, udt
            )
            self.udt_principals[0] -= amount
            self.udt_principals[1] += amount
            self.assert_settled_udt(
                settled, code_tx, udt, self.get_tx_message(commitment["hash"])["fee"]
            )
        # 链上到账与本端付款终态分别核对，不能用前一笔链下付款替代这笔 TLC。
        # 上链产出的付款查询终态由 FIBER_ASSERT_ONCHAIN_TLC_QUERY 门控；关时只核对没有被判成失败。
        if onchain_tlc_query_enabled():
            self.wait_payment_state(self.fiber1, payment_hash, "Success")
            result = self.fiber1.get_client().get_payment(
                {"payment_hash": payment_hash}
            )
            assert result["payment_preimage"] == preimage
        else:
            result = self.fiber1.get_client().get_payment(
                {"payment_hash": payment_hash}
            )
            assert result["status"] != "Failed", result
        return SettlementWitness.from_hex(
            settled["witnesses"][0], version=self.commitment_version
        )

    # TEST-MAP: H32V2-06
    # TEST-EVIDENCE-BEGIN: H32V2-06
    # Evidence | covered | V1 58/97 plus digest recomputed according to witness algorithm; all witnessed
    # types exactly {0,1,2,3}; actual committed payout and Success/correct preimage.
    # TEST-EVIDENCE-END: H32V2-06
    # H32-06 证明链：对端 V1 承诺含可兑现 TLC，以完整哈希匹配的原像结算，覆盖 Offered/Received
    # 与 CKB Blake2b / SHA256 的组合。
    # 1) 矩阵：algorithm ∈ {ckb_hash, sha256} × 强关端 ∈ {对端, 本端}，共 4 个组合；每个组合独立
    #    开通通道并保留一笔已承诺 TLC（hold_one_payment 按算法生成 invoice 与 payment_hash）。
    # 2) 实际是 V1：select_peers 只声明期望，真正的判据是 settle_held_channel 内对承诺锁 args 的
    #    58 字节 + 末位 0x01 断言；若协商回落 Legacy，会因 57 字节而失败。
    # 3) 完整哈希与正确算法分支：assert_tlc_settlement 用 SettlementWitness(version="v1") 解析
    #    97 字节条目，assert_single_tlc_claim 按 tlc_type 的算法位重算摘要（& 2 → SHA256，
    #    否则 Blake2b）并比对完整 32 字节哈希；金额与收款人净增量同时核对。
    # 4) 方向覆盖：witnessed_types 收集链上实际出现的 tlc_type，最后断言恰好是 {0,1,2,3}，
    #    即 Offered/Received 两个方向与两种算法都真实结算过，而不只是参数循环跑到过。
    # 5) 本端记录：付款方状态为 Success 且 get_payment 返回的原像与本次公布的一致，说明只对实际
    #    兑现的项记成功并保留正确原像。
    def test_v1_valid_preimage_each_algorithm_and_commitment_direction(self):
        # 默认快照已部署升级后的 commitment-lock，直接用当前代码 cell。
        code_tx = self.current_contract_code_tx()
        # 收集链上实际兑现的 tlc_type（bit0 = 方向，bit1 = 是否 SHA256），用于证明矩阵真的跑全。
        witnessed_types = set()
        for algorithm in ("ckb_hash", "sha256"):
            for close_at_sender in (False, True):
                with self.subTest(algorithm=algorithm, close_at_sender=close_at_sender):
                    # 每个组合都在自己的新新通道上验证，避免前一个组合的状态污染。
                    self.select_peers(self.new1, self.new2, "v1")
                    self.open_ready()
                    # hold_one_payment 按算法算出对应 payment_hash（Blake2b 或 SHA256），
                    # 并等两端都进入 Committed。
                    payment_hash, preimage = self.hold_one_payment(algorithm=algorithm)
                    # 对端或本端强关 → 公布原像 → 派生 cell 结算；返回链上解析出的 witness。
                    witness = self.settle_held_channel(
                        code_tx,
                        payment_hash,
                        preimage,
                        closer=self.fiber1 if close_at_sender else self.fiber2,
                    )
                    witnessed_types.add(witness.tlcs[0].tlc_type)
        # 四种 tlc_type 全部出现 → 方向与算法两个分支都被真实链上结算覆盖。
        # 0 = Offered + Blake2b
        # 1 = Received + Blake2b
        # 2 = Offered + SHA256
        # 3 = Received + SHA256
        assert witnessed_types == {0, 1, 2, 3}

    # TEST-MAP: H32V2-01
    # TEST-EVIDENCE-BEGIN: H32V2-01
    # Evidence | covered | Committed exact-outpoint spend; commitment 58/feature=1 and strict 97-byte V1
    # parser; expected hash/preimage/amount, recipient net payout, conservation, payment Success and
    # matching preimage.
    # TEST-EVIDENCE-END: H32V2-01
    # H32-01 证明链：双方支持完整哈希，分别经普通开通和外部注资开通，完成付款后由对端强关。
    # 1) 双方使用 V1：本测试不配置节点版本，select_peers 只声明期望；实际版本由链上锁参数证明，
    #    若协商回落 Legacy，settle_held_channel 的 58/57 字节断言会失败。
    # 2) 普通与外部注资两条开通路径：open_ready(False) 走 open_channel，open_ready(True) 走
    #    open_channel_with_external_funding + sign/submit_signed_funding_tx，两者都必须 ChannelReady。
    # 3) 完成付款：send_payment 完成一笔链下正常付款，hold_one_payment 再保留一笔已承诺 TLC，
    #    使强关时的承诺交易携带真实 TLC。
    # 4) 对端强关与结算：settle_held_channel 默认 closer=fiber2，由对端强关；其中
    #    - force_close 断言广播交易去掉 deps 后等于该端已签名承诺 → 链上承诺与节点存储一致；
    #    - 承诺锁 args 必须 58 字节且末字节 0x01 → 实际承诺锁确为 V1 格式；
    #    - settle_invoice 公布原像后 wait_for_spend 要求 tx_status == committed → 交易确认；
    #    - assert_tlc_settlement 以 SettlementWitness(version="v1") 解析并往返重建 97 字节条目
    #      (1+16+32+20+20+8)，核对金额与收款人到账 → 含 TLC 的结算使用 V1 布局；
    #    - assert_settled 继续花费剩余承诺 cell，核对双方钱包净增与总矿工费 → 正确收尾。
    def test_new_nodes_use_v1_normal_and_external_funding(self):
        # 默认快照已部署升级后的 commitment-lock（无需再升级）；节点全程不重启，V1 才可能被协商选中。
        code_tx = self.current_contract_code_tx()
        for external in (False, True):
            with self.subTest(external=external):
                # 两端均为固定 head 的新节点，期望协商结果为 V1（由下方链上断言验证，而非假定）。
                self.select_peers(self.new1, self.new2, "v1")
                # external=False 普通开通；external=True 外部注资开通（由外部钱包私钥签 funding tx）。
                self.open_ready(external)
                # 先完成一笔普通链下付款，证明 V1 通道不只是能开通，还能正常付款。
                self.send_payment(self.fiber1, self.fiber2, CKB)
                self.record_channel()
                # 再保留一笔已承诺 TLC，作为强关承诺中“含 TLC”的样本。
                payment_hash, preimage = self.hold_one_payment()
                # 对端强关 → 公布原像 → 派生 cell 结算 → 核对 58/0x01 锁与 97 字节条目及到账。
                self.settle_held_channel(code_tx, payment_hash, preimage)

    # TEST-MAP: H32V2-02
    # TEST-EVIDENCE-BEGIN: H32V2-02
    # Evidence | covered | Committed exact-outpoint spend; 57-byte Legacy commitment and strict
    # 85-byte/20-byte-prefix witness; recipient payout, conservation, payment Success and matching
    # preimage.
    # TEST-EVIDENCE-END: H32V2-02
    # H32-02 证明链：仅一方支持或对端未宣告完整哈希特性时，两端都必须退回 Legacy 并保持互通。
    # 1) 混合版本：receiver 固定为旧节点 old1（9a561b3），不宣告 ONCHAIN_FULL_PAYMENT_HASH；
    #    循环交换发起方，new1→old1 与 old1→new1 两个方向都要走到 ChannelReady，
    #    证明单边升级不会因 optional 特性宣告差异断开连接或开通失败。
    # 2) 选 Legacy：select_peers 只声明期望，实际版本由链上断言证明——
    #    settle_held_channel 要求承诺锁 args 恰为 57 字节（非 V1 的 58），且不检查末字节 0x01；
    #    record_channel 也按 Legacy 的 99 CKB 预留（V1 为 100 CKB）计算本金供后续到账核对。
    # 3) Legacy 布局而非完整哈希保护：assert_tlc_settlement 以 SettlementWitness(version="legacy")
    #    解析 85 字节条目 (1+16+20+20+20+8)，并断言 TLC 的 payment_hash 只承诺完整哈希前 20 字节，
    #    防止把旧格式误当作完整哈希保护。
    # 4) 正常付款与结算成功：send_payment 一笔链下付款 + hold_one_payment 一笔已承诺 TLC，
    #    对端 fiber2（即 receiver）强关后 settle_invoice 公布原像，wait_for_spend 要求
    #    tx_status == committed，assert_settled 核对双方钱包净增与总矿工费 → 交易确认且正确收尾。
    def test_mixed_nodes_keep_legacy_both_initiators(self):
        # 默认快照已部署升级后的 commitment-lock；旧节点仍在原进程内跑旧二进制，故只能协商出 Legacy。
        code_tx = self.current_contract_code_tx()
        for sender, receiver in ((self.new1, self.old1), (self.old1, self.new1)):
            with self.subTest(sender=sender.rpc_port):
                # 两种发起方组合都声明 Legacy 期望；真实版本由下方 57/85 字节断言确认。
                self.select_peers(sender, receiver, "legacy")
                # 普通开通；旧节点必须接受新节点的开通请求并到达 ChannelReady。
                self.open_ready()
                # 混合版本通道上完成一笔普通链下付款，证明互通不止于开通。
                self.send_payment(self.fiber1, self.fiber2, CKB)
                self.record_channel()
                # 保留一笔已承诺 TLC，作为 Legacy 85 字节结算条目的样本。
                payment_hash, preimage = self.hold_one_payment()
                # receiver 强关 → 公布原像 → 核对 57 字节锁、85 字节条目、到账与正确收尾。
                self.settle_held_channel(code_tx, payment_hash, preimage)

    # TEST-MAP: H32V2-20
    # TEST-EVIDENCE-BEGIN: H32V2-20
    # Evidence | covered | node_info default 100 CKB/explicit zero, actual funding capacity+fee spending,
    # receiver contribution bound; pending zero lacks outpoint/Ready, manual acceptance followed by
    # version-checked committed close and payouts.
    # TEST-EVIDENCE-END: H32V2-20
    # H32-19 证明链：新节点默认自动接受、短 shutdown lock、充足 CKB；同一新接收方分别收到 V1 与
    # Legacy 的自动接受请求，再以显式配置 0 作人工接受对照。
    # 1) 默认值：接收方 node_info 的 auto_accept_channel_ckb_funding_amount 必须是 100 CKB
    #    （V1 预留值；仍是 99 CKB 会让 V1 开通失败）。
    # 2) 两种版本都走自动接受：new1→new2 为 V1、old1→new2 为 Legacy，都要到达 ChannelReady；
    #    版本由链上证据确认——record_channel 按版本取 100/99 CKB 预留，assert_settled 核对承诺锁
    #    58 字节 + 末位 0x01（V1）或 57 字节（Legacy）以及双方到账。
    # 3) 出资与费用核对：比较开通前后的钱包余额，接收方（account2）实际支出必须落在
    #    [100 CKB, 100 CKB + 实际矿工费]，且双方支出合计等于 funding 输出容量加矿工费。
    # 4) 显式 0 的对照：另起一个 fiber_auto_accept_channel_ckb_funding_amount=0 的节点，其
    #    node_info 必须为 0；向它发起满足默认最小金额的开通请求后，3 秒内保持 pending
    #    （无 channel_outpoint、未 ChannelReady），确认没有自动接受。
    # 5) 人工路径未被破坏：随后用 temporary_channel_id 手工 accept_channel 到达 ChannelReady，
    #    并按同一套链上判据完成强关结算。
    def test_default_auto_accept_and_explicit_zero(self):
        # 默认快照已部署升级后的 commitment-lock，直接用当前代码 cell。
        code_tx = self.current_contract_code_tx()
        # 两种协商版本共用同一个新接收方 new2（自动接受开启）：new1→v1、old1→legacy。
        for sender, version in ((self.new1, "v1"), (self.old1, "legacy")):
            with self.subTest(version=version):
                self.select_peers(sender, self.new2, version)
                info = self.fiber2.get_client().node_info()
                # 默认自动出资必须是 100 CKB；仍是 99 CKB 会让 V1 开通失败。
                assert (
                    int(info["auto_accept_channel_ckb_funding_amount"], 16) == 100 * CKB
                )
                before = self.wallet_balances()
                self.open_ready()
                after = self.wallet_balances()
                spent = [a - b for a, b in zip(before, after)]
                funding = self.ckb.get_transaction(self.funding_tx)["transaction"]
                fee = self.get_tx_message(self.funding_tx)["fee"]
                # 链上支出按实际出资与矿工费核对：接收方应支出默认出资 100 CKB + 实际矿工费。
                assert sum(spent) == int(funding["outputs"][0]["capacity"], 16) + fee
                assert 100 * CKB <= spent[1] <= 100 * CKB + fee
                # 自动接受出来的通道仍按版本核对承诺锁布局与到账，确认版本各自保持。
                self.assert_settled(self.force_close(self.fiber1), code_tx)

        # 对照：显式配置 auto_accept_channel_ckb_funding_amount=0 的节点必须关闭自动接受。
        self.select_peers(self.new1, self.manual, "v1")
        assert (
            int(
                self.fiber2.get_client().node_info()[
                    "auto_accept_channel_ckb_funding_amount"
                ],
                16,
            )
            == 0
        )
        self.fiber1.connect_peer(self.fiber2)
        previous = {
            c["channel_id"]
            for c in self.fiber1.get_client().list_channels({})["channels"]
        }
        # 发起一笔满足默认最小金额的开通请求，接收方不应自动接受。
        request = self.fiber1.get_client().open_channel(
            {
                "pubkey": self.fiber2.get_pubkey(),
                "funding_amount": hex(1000 * CKB + DEFAULT_MIN_LEDGER_DEPOSIT_CKB),
                "public": True,
            }
        )
        temporary_id = request["temporary_channel_id"]
        for _ in range(10):
            pending = self.fiber2.get_client().list_channels({"only_pending": True})[
                "channels"
            ]
            if any(c["channel_id"] == temporary_id for c in pending):
                break
            time.sleep(1)
        else:
            self.fail(f"Manual accept request missing: {pending}")
        # 3 秒内保持 pending：没有 channel_outpoint、也未 ChannelReady → 确认未走自动接受。
        for _ in range(3):
            pending = self.fiber2.get_client().list_channels({"only_pending": True})[
                "channels"
            ]
            channel = next(c for c in pending if c["channel_id"] == temporary_id)
            assert channel["channel_outpoint"] is None
            assert channel["state"]["state_name"] != "ChannelReady"
            time.sleep(1)
        # 人工接受路径仍可用；随后按同一套链上判据结算。
        self.fiber2.get_client().accept_channel(
            {"temporary_channel_id": temporary_id, "funding_amount": hex(100 * CKB)}
        )
        self.channel_id = self.wait_for_new_channel_state(
            self.fiber1.get_client(), self.fiber2.get_pubkey(), "ChannelReady", previous
        )
        self.record_channel()
        self.assert_settled(self.force_close(self.fiber1), code_tx)

    # TEST-MAP: H32V2-21
    # TEST-EVIDENCE-BEGIN: H32V2-21
    # Evidence | partial | Real in-place OLD->NEW upgrade, then the shared matrix (old-old/new-old/old-new
    # legacy plus new-new V1) x normal/external funding, all force-closed and settled by the new bytes;
    # no-TLC only in that matrix. A separate method now covers the committed-TLC branch (one held TLC
    # settled by the upgraded code for both V1 and Legacy) and another covers the xUDT committed-TLC
    # branch; still missing: the expiry-refund path.
    # TEST-EVIDENCE-END: H32V2-21
    def test_upgrade_then_open_each_node_pair(self):
        self.upgrade_contract(OLD_CONTRACT)
        code_tx = self.upgrade_contract(NEW_CONTRACT)
        for sender, receiver, version in (
            (self.old1, self.old2, "legacy"),
            (self.new1, self.old1, "legacy"),
            (self.old1, self.new1, "legacy"),
            (self.new1, self.new2, "v1"),
        ):
            for external in (False, True):
                with self.subTest(
                    sender=sender.rpc_port,
                    receiver=receiver.rpc_port,
                    external=external,
                ):
                    self.select_peers(sender, receiver, version)
                    self.open_ready(external)
                    commitment = self.force_close(receiver)
                    self.assert_settled(commitment, code_tx)

    # TEST-MAP: H32V2-21
    # TEST-MAP: H32V2-19
    # TEST-EVIDENCE-BEGIN: H32V2-21
    # Evidence | partial | After a real OLD->NEW type-id upgrade, a channel with one committed TLC is
    # force-closed and settled by the new code for both V1 (new-new) and Legacy (old-old) peers, with the
    # code-dep outpoint, 58/97 vs 57/85 layout, payout and Success/preimage asserted. Not covered here: the
    # expiry-refund branch (the refund path is exercised by H32V2-10/25); the xUDT committed-TLC branch is
    # covered by test_upgrade_then_settle_committed_xudt_tlc_with_new_code. This method also proves the
    # reserve and witness-width parts of H32V2-19: the short-lock CKB versioned reserve (100/99 CKB on both
    # ends right after ordinary funding) and the committed settlement witness width on the chain
    # (90 + 97*1 + 99 = 286 for V1, 90 + 85*1 + 99 = 274 for Legacy, V1 exactly 12 bytes wider). The
    # H32V2-19 fee-boundary and xUDT-width parts stay in
    # test_cases/fiber/devnet/compatibility/test_full_hash_layout_fee.py.
    # TEST-EVIDENCE-END: H32V2-21
    def test_upgrade_then_settle_committed_tlc_with_new_code(self):
        self.upgrade_contract(OLD_CONTRACT)
        code_tx = self.upgrade_contract(NEW_CONTRACT)
        widths = {}
        for sender, receiver, version in (
            (self.new1, self.new2, "v1"),
            (self.old1, self.old2, "legacy"),
        ):
            with self.subTest(version=version):
                # 每个组合独立开通；含一笔已承诺未完成 TLC，确保结算真的走新版 commitment-lock 的
                # TLC 分支（升级矩阵中的无 TLC 组合只覆盖余额分支）。
                self.select_peers(sender, receiver, version)
                self.open_ready()
                # H32V2-19：普通开通（短锁 CKB）后，链上 funding 容量减去两端可结算余额，必须
                # 恰好等于两端各自预留的一份（occupied capacity + shutdown fee）。
                # 本拓扑两端都会出资/自动接受（发起方 1099 CKB = 1000 +
                # DEFAULT_MIN_LEDGER_DEPOSIT_CKB，接收方自动出资 100 CKB），
                # 因此预留共 2 份；若将来有一端不出资，这里需按实际端数改。
                reserve = (
                    DEFAULT_MIN_DEPOSIT_CKB
                    if version == "v1"
                    else DEFAULT_MIN_LEDGER_DEPOSIT_CKB
                )
                funding_capacity = int(
                    self.ckb.get_transaction(self.funding_tx)["transaction"]["outputs"][
                        0
                    ]["capacity"],
                    16,
                )
                locals_ = {
                    fiber.rpc_port: int(self.channel(fiber)["local_balance"], 16)
                    for fiber in self.fibers
                }
                locked = funding_capacity - sum(locals_.values())
                assert locked == 2 * reserve, (
                    f"{version}: funding {funding_capacity} - Σlocal {sum(locals_.values())} = "
                    f"{locked}，期望 2×{reserve}（两端各一份 "
                    f"{DEFAULT_MIN_DEPOSIT_CKB if version == 'v1' else DEFAULT_MIN_LEDGER_DEPOSIT_CKB} CKB 预留）; "
                    f"local_balances={locals_}"
                )
                payment_hash, preimage = self.hold_one_payment()
                self.settle_held_channel(code_tx, payment_hash, preimage)
                # H32V2-19：链上已承诺 TLC 的结算 witness 宽度按版本核对（V1 比 Legacy 宽 12 字节）。
                commitment = self.wait_for_spend(self.funding_tx)
                settled = self.wait_for_spend(commitment["hash"])
                raw = bytes.fromhex(settled["witnesses"][0][2:])
                expected = witness_size(version, 1)
                assert len(raw) == expected, (
                    f"{version}: 1 笔已承诺 TLC 的 witness 宽度应为 {expected}，"
                    f"实测 {len(raw)}: {settled['witnesses'][0]}"
                )
                widths[version] = len(raw)
        # 1 字节 feature + 每笔 TLC 多出的 12 字节完整哈希。
        assert widths["v1"] - widths["legacy"] == 12, widths

    def cooperative_close_and_check_balances(self, closer, udt=None):
        for _ in range(60):
            if all(not self.channel(peer)["pending_tlcs"] for peer in self.fibers):
                break
            time.sleep(1)
        else:
            self.fail("Cooperative shutdown still has pending TLCs")
        self.record_channel(udt)
        closer.get_client().shutdown_channel(
            {"channel_id": self.channel_id, "force": False, "fee_rate": hex(1000)}
        )
        tx = self.wait_for_spend(self.funding_tx)
        # Cooperative close spends FundingLock, not the upgraded CommitmentLock.
        assert all(
            o["lock"]["code_hash"] != COMMIT_LOCK_CODE_HASH for o in tx["outputs"]
        )
        fees = self.get_tx_message(tx["hash"])["fee"]
        delta = [a - b for a, b in zip(self.wallet_balances(), self.wallet_before)]
        funding = self.ckb.get_transaction(self.funding_tx)["transaction"]["outputs"][0]
        assert sum(delta) == int(funding["capacity"], 16) - fees
        assert 0 <= fees < CKB // 100
        if udt is None:
            for received, principal in zip(delta, self.principals):
                assert principal - fees <= received <= principal
        else:
            token_delta = [
                a - b for a, b in zip(self.token_balances(), self.tokens_before)
            ]
            # UDT 通道的 shutdown fee 只从 CKB capacity 扣（channel.rs build_shutdown_tx），
            # 代币按 to_local/to_remote 数额原样返还，所以 token_delta 恰为记录的 local_balance。
            assert token_delta == self.udt_principals, (
                token_delta,
                self.udt_principals,
            )
        for _ in range(120):
            self.assert_nodes_running()
            states = [self.channel(peer)["state"] for peer in self.fibers]
            if all(s["state_name"] == "Closed" for s in states):
                return
            time.sleep(1)
        self.fail(
            f"Cooperative shutdown not finalized by both original processes: {states}"
        )

    # TEST-MAP: H32V2-27
    # TEST-EVIDENCE-BEGIN: H32V2-27
    # Evidence | partial | CKB only in this method; the xUDT cooperative-close matrix is covered by
    # test_cooperative_close_xudt_old_and_new_channels_after_upgrade. Cooperative close spends FundingLock
    # and never consumes commitment-lock, so the negotiated 57/58 layout cannot be observed through this
    # path and is not asserted here; the layout is asserted by force-close in H32V2-21/22/23/26. This row
    # stays an adjacent regression and is not commitment-lock execution evidence.
    # TEST-EVIDENCE-END: H32V2-27
    def test_cooperative_close_old_and_new_channels_after_upgrade(self):
        for closer_index in (0, 1):
            self.upgrade_contract(OLD_CONTRACT)
            self.select_peers(self.old1, self.new1, "legacy")
            self.open_ready()
            self.upgrade_contract(NEW_CONTRACT)
            self.send_payment(self.fiber1, self.fiber2, CKB)
            self.cooperative_close_and_check_balances(self.fibers[closer_index])
            for sender, receiver, version in (
                (self.old1, self.old2, "legacy"),
                (self.new1, self.new2, "v1"),
            ):
                with self.subTest(version=version, closer=closer_index):
                    self.select_peers(sender, receiver, version)
                    self.open_ready()
                    self.send_payment(self.fiber1, self.fiber2, CKB)
                    self.cooperative_close_and_check_balances(self.fibers[closer_index])

    # TEST-MAP: H32V2-21
    # TEST-EVIDENCE-BEGIN: H32V2-21
    # Evidence | partial | xUDT: after a real in-place OLD->NEW type-id upgrade, an xUDT channel with one
    # committed TLC is force-closed and settled by the new code, once for V1 (new-new) and once for Legacy
    # (new1-old1). Asserts the 58/0x01 vs 57 commitment layout, the 97 vs 85 per-TLC witness entry, the
    # upgraded commitment-lock code dep plus the xUDT code dep, the xUDT amount delta/recipient net payout
    # and Success with the matching preimage. Not covered: the expiry-refund branch, external funding and the
    # post-upgrade V1 control (H32V2-22) and xUDT fee accounting (xUDT transfers carry no fee here).
    # TEST-EVIDENCE-END: H32V2-21
    def test_upgrade_then_settle_committed_xudt_tlc_with_new_code(self):
        self.upgrade_contract(OLD_CONTRACT)
        code_tx = self.upgrade_contract(NEW_CONTRACT)
        udt = self.udt_script()
        for sender, receiver, version in (
            (self.new1, self.new2, "v1"),
            (self.new1, self.old1, "legacy"),
        ):
            with self.subTest(version=version):
                # 只有共享 new1 账户持有 xUDT，所以 xUDT 通道一律由 new1 出资；
                # 对端用 new2（V1）或 old1（Legacy）覆盖两种承诺布局。
                self.select_peers(sender, receiver, version)
                self.open_ready_udt(udt)
                payment_hash, preimage = self.hold_one_payment(amount=100, udt=udt)
                self.settle_held_channel(
                    code_tx, payment_hash, preimage, amount=100, udt=udt
                )

    # TEST-MAP: H32V2-27
    # TEST-EVIDENCE-BEGIN: H32V2-27
    # Evidence | partial | xUDT: after the same OLD->NEW in-place upgrade, a pre-upgrade Legacy xUDT channel
    # and post-upgrade Legacy/V1 xUDT channels are paid and then cooperatively closed by each side. Asserts
    # the cooperative tx spends FundingLock (no commitment-lock output), funding capacity minus the real CKB
    # fee returns to the wallets, and the xUDT delta equals the recorded local balances (the shutdown fee is
    # deducted from CKB capacity, not from UDT units). Not covered: the negotiated 57/58 layout (this path
    # never consumes commitment-lock) and xUDT fee accounting.
    # TEST-EVIDENCE-END: H32V2-27
    def test_cooperative_close_xudt_old_and_new_channels_after_upgrade(self):
        udt = self.udt_script()
        for closer_index in (0, 1):
            self.upgrade_contract(OLD_CONTRACT)
            # 升级前只有 Legacy xUDT 通道；出资方固定为持有 xUDT 的 new1。
            self.select_peers(self.new1, self.old1, "legacy")
            self.open_ready_udt(udt)
            self.upgrade_contract(NEW_CONTRACT)
            self.send_payment(self.fiber1, self.fiber2, 100, udt=udt)
            self.cooperative_close_and_check_balances(
                self.fibers[closer_index], udt=udt
            )
            for sender, receiver, version in (
                (self.new1, self.old1, "legacy"),
                (self.new1, self.new2, "v1"),
            ):
                with self.subTest(version=version, closer=closer_index):
                    self.select_peers(sender, receiver, version)
                    self.open_ready_udt(udt)
                    self.send_payment(self.fiber1, self.fiber2, 100, udt=udt)
                    self.cooperative_close_and_check_balances(
                        self.fibers[closer_index], udt=udt
                    )
