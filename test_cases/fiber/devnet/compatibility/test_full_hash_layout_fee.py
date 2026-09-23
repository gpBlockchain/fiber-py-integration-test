"""H32V2-19 版本化容量/费用边界（CKB/xUDT）。

本文件只保留 Python 集成测试能覆盖的场景：H32V2-19 的预留量（在
`test_full_hash_channels.py::test_upgrade_then_settle_committed_tlc_with_new_code` 中断言）、
承诺费率边界、CKB witness 宽度（同上前者）与 xUDT 布局/宽度/真实结算。

合法布局（57/85 与 58/97）本身由 H32V2-01/02/06/21 覆盖，本文件不再放无映射的合法结算对照。

不在本仓库覆盖（已按"只保留 Python 集成测试场景"移出评审）：非法 args（56/59 字节、58 字节未知
feature 掩码）、跨布局 witness、截断 witness 的拒绝，以及"在有效部分结算中篡改派生输出版本"。
原因是这些都需要可签名的合约测试：Python 无法为 commitment lock 签名，构造不出"非法布局 + 有效
签名"的花费；用 always-success 伪造"假承诺 cell"的路径既不可运行
（`TransactionFailedToResolve: Unknown(OutPoint)`），其"被拒绝"也无法归因到布局校验（全零签名必然
先被签名校验拒绝）。这些负例由 fiber-scripts 的 Rust 合约测试负责（SPEC-05 标为 not_tested）。

-------------------------------------------------------------------------------

H32V2-19: versioned reserved capacity and commitment-fee boundaries (CKB) plus xUDT layout.

证明链（对应 reviews/full-payment-hash-settlement-v2.md 的 H32V2-19）：
1) 预留量按合约真实占用计算：V1 承诺锁 args 58 字节、Legacy 57 字节，各自占用 102/101 CKB。
   `occupied_capacity` = 8(capacity) + 33(lock 头) + 57|58(承诺锁 args) + 1(type option)，
   `reserved_capacity` = occupied + `DEFAULT_MIN_SHUTDOWN_FEE`(1 CKB)。普通开通请求 1000 CKB 后，
   两端 `local_balance` 应分别少 100/99 CKB（fiber-lib `get_funding_and_reserved_amount` /
   `reserved_capacity` / `occupied_capacity`）。这些 CKB 预留数值只适用于 CKB 通道。
2) 费用边界：`check_commitment_reserved_fee` 要求 `commitment_fee * 2 <= reserved_fee`，
   其中 `commitment_fee = commitment_fee_rate * commitment_tx_size // 1000`（ckb-types
   `FeeRate::fee` 是 shannons/KB），`reserved_fee = reserved_capacity - occupied_capacity`。
   按 V1 分支（8b95af3）源码：V1 的 reserved_fee = 200_000_000 - 100_000_000 = 100_000_000，
   Legacy 的 reserved_fee = 200_000_000 - 99_800_000 = 99_800_000；`commitment_tx_size` 约 450
   字节，于是两版的真实费率边界都在 ~1.1e8，而费率 50_000_000 只产生 22_500_000 的承诺费。
   也就是说评审行“少 1 Shannon 失败”的边界值（50_000_001）在当前源码里仍然通过；本文件不把
   这个未验证的假设写成断言，而是同时提交 50_000_000 与 50_000_001 并如实记录结果，另用
   u64::MAX 费率触发真实拒绝、从错误文本解析 reserved fee 作为边界存在的证据。
3) witness 宽度：1 笔已承诺 TLC 的真实强关结算后，witness 宽度必须是
   `90 + (97|85) * 1 + 99`（V1 286 / Legacy 274），且 V1 恰好宽 12 字节——V1 多 1 字节 feature
   以及每笔 TLC 多 12 字节完整哈希（97 vs 85）。CKB 与 xUDT 资产都按同一宽度核对。

合并说明：原 test_full_hash_capacity_fee.py 的 test_reserve_matches_version 与
test_witness_width_is_versioned 已删除；H32V2-19 的短锁 CKB 版本化预留与 1 笔已承诺 TLC 的
CKB witness 宽度改由
test_full_hash_channels.py::test_upgrade_then_settle_committed_tlc_with_new_code 证明。
本文件保留费用边界（test_commitment_fee_boundary_is_version_aware）与 xUDT 布局/结算
（test_xudt_witness_width_and_settlement_are_versioned）。

未覆盖（与 TEST-EVIDENCE 注释一致）：
- UDT：本仓库的 UDT 就是 xUDT。`test_xudt_witness_width_and_settlement_are_versioned` 已覆盖
  V1/Legacy xUDT 通道的承诺布局、每 TLC witness 宽度与真实强关结算（含 xUDT 代码 dep）；但本文件
  的容量预留与承诺费边界（99/100 CKB、reserved_fee 校验）仍是 CKB 专属，不为 UDT 编造等价数值。
- 长 shutdown lock：框架节点统一使用默认 funding lock，无法在同一轮里构造长短锁对照，
  故只覆盖短锁自动预留。
- 外部注资 / 接收开通路径：本文件只覆盖普通开通（发起方 = new1）。
"""

import hashlib
import re
import secrets
import socket
import time

from framework.basic_fiber import COMMIT_LOCK_CODE_HASH
from framework.helper.settlement_witness import (
    CKB,
    SettlementWitness,
    assert_commitment_args,
    assert_commitment_args_prefix,
    assert_commitment_delay_epoch,
    commitment_args_len,
    commitment_tx_size,
    witness_size,
)
from framework.onchain_tlc_query import onchain_tlc_query_enabled
from framework.test_fiber import FiberConfigPath
from framework.util import ckb_hash
from test_cases.fiber.devnet.compatibility.contract_upgrade_support import (
    CKB,
    FullHashChannelSupport,
)


def udt_amount(output, data, udt):
    """Read one xUDT amount; never mix it with the CKB capacity of the same cell."""
    if output.get("type") != udt:
        return 0
    raw = bytes.fromhex(data.removeprefix("0x"))
    assert len(raw) == 16, "xUDT amount must be a 16-byte little-endian integer"
    return int.from_bytes(raw, "little")


# 评审行核心边界：恰好两倍承诺费上限的费率。
EXACT_BUDGET_RATE = 50_000_000
# 评审行声称“少 1 Shannon”会被拒绝的费率。
REVIEW_ONE_SHANNON_RATE = 50_000_001


class TestFullHashLayoutFee(FullHashChannelSupport):
    ckb_rpc_port, ckb_p2p_port = 23414, 23415
    fiber1_rpc_port, fiber1_p2p_port = 23428, 23427
    fiber2_rpc_port, fiber2_p2p_port = 23429, 23430
    extra_fiber_rpc_port, extra_fiber_p2p_port = 23500, 23600
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
            cls.extra_fiber_rpc_port,
            cls.extra_fiber_p2p_port,
            cls.extra_fiber_rpc_port + 1,
            cls.extra_fiber_p2p_port + 1,
        ):
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", port))
        super().setup_class()
        cls.ckb = cls.node.getClient()
        cls.new1, cls.new2 = cls.fiber1, cls.fiber2
        # 本类不重启节点：断言进程未退出/重启，避免“升级期间节点重启”被漏掉。
        cls.processes = cls.node_processes()

    def setUp(self):
        cls = self.__class__
        if not hasattr(cls, "legacy_peer"):
            # Legacy 对端固定为 PR base 9a561b3；generate_account 是实例方法，
            # 旧对端在 setUp 里惰性启动一次。
            cls.legacy_peer = self.start_new_fiber(
                self.generate_account(5000), fiber_version=FiberConfigPath.V091_DEV
            )

    # ---- H32V2-19 helpers ------------------------------------------------

    def peers_for(self, version):
        """该版本对应的 (sender, receiver)；Legacy 固定使用旧节点。"""
        return (
            (self.new1, self.new2) if version == "v1" else (self.new1, self.legacy_peer)
        )

    def commitment_args_len(self, version):
        """承诺锁 args 长度：V1 = 57 + 1 字节 feature，Legacy = 57。"""
        return commitment_args_len(version)

    def occupied_capacity(self, version):
        """承诺锁派生 cell 的真实占用（Shannon）。"""
        return (self.commitment_args_len(version) + 1) * CKB

    def reserved_capacity(self, version):
        """`reserved_capacity` = 真实占用 + 1 CKB 默认 shutdown 费用。"""
        return self.occupied_capacity(version) + CKB

    def reserved_fee(self, version):
        """扣除占用后可用于承诺费的预算，即费用校验的 `reserved_fee`。"""
        return self.reserved_capacity(version) - self.occupied_capacity(version)

    def commitment_tx_size(self, version):
        """复刻 fiber-lib `commitment_tx_size` 的 mock 承诺交易字节长度。"""
        return commitment_tx_size(version)

    def commitment_fee(self, rate, version):
        """`rate * tx_size // 1000`，与 ckb-types `FeeRate::fee` 一致。"""
        return rate * self.commitment_tx_size(version) // 1000

    @staticmethod
    def parse_reserved_fee(message):
        """从 `... is larger than half of reserved fee <N>` 取出 N。"""
        match = re.search(r"reserved fee\s+(\d+)", str(message))
        return int(match.group(1)) if match else None

    def probe_channel(self, version, commitment_fee_rate):
        """用显式费率尝试开通新通道，返回 (bool 成功, 通道 dict 或错误文本)。

        失败时原样返回 RPC 错误文本供调用方断言；成功时等两端就绪后返回本端通道 dict。
        """
        self.select_peers(*self.peers_for(version), version)
        self.fiber1.connect_peer(self.fiber2)
        existing = {
            c["channel_id"]
            for c in self.fiber1.get_client().list_channels({})["channels"]
        }
        try:
            self.fiber1.get_client().open_channel(
                {
                    "pubkey": self.fiber2.get_pubkey(),
                    "funding_amount": hex(200 * CKB),
                    "public": True,
                    "commitment_fee_rate": hex(commitment_fee_rate),
                }
            )
        except Exception as error:
            return False, str(error)
        # self.channel() 按 self.channel_id 查找；只存局部变量会让下面的 channel()
        # 读到上一次 probe 的 id（首次调用则直接 AttributeError）。
        self.channel_id = self.wait_for_new_channel_state(
            self.fiber1.get_client(), self.fiber2.get_pubkey(), "ChannelReady", existing
        )
        for _ in range(60):
            channel = self.channel(self.fiber1)
            if (
                channel["channel_id"] == self.channel_id
                and channel["state"]["state_name"] == "ChannelReady"
            ):
                return True, channel
            time.sleep(1)
        return True, self.channel(self.fiber1)

    def probe_boundary(self, version, rate):
        """开一条通道并按费用校验语义核对这个费率，返回观测记录。"""
        reserved_fee = self.reserved_fee(version)
        budget = reserved_fee // 2
        tx_size = self.commitment_tx_size(version)
        fee = self.commitment_fee(rate, version)
        boundary_rate = budget * 1000 // tx_size if tx_size else None
        record = {
            "rate": rate,
            "commitment_fee": fee,
            "version_reserved_fee": reserved_fee,
            "version_budget": budget,
            "tx_size": tx_size,
            "boundary_rate": boundary_rate,
        }
        ok, detail = self.probe_channel(version, rate)
        if ok:
            record["result"] = "accepted"
            record["state"] = detail["state"]["state_name"]
            assert (
                detail["state"]["state_name"] == "ChannelReady"
            ), f"{version}: 费率 {rate} 已开通但未就绪: {detail}"
            assert fee * 2 <= reserved_fee, (
                f"{version}: 费率 {rate} 通过了费用校验，但两倍承诺费 {fee * 2} 超过按版本"
                f"计算的 reserved_fee {reserved_fee}; 记录={record}"
            )
        else:
            text = str(detail)
            record["result"] = "rejected"
            record["error"] = text
            assert (
                "commitment fee" in text or "reserved fee" in text
            ), f"{version}: 费率 {rate} 被拒绝，但错误文本不像费用校验: {text}; 记录={record}"
            parsed = self.parse_reserved_fee(text)
            if parsed is not None:
                record["reserved_fee_from_error"] = parsed
                # 用实测值反推：拒绝时两倍承诺费必须真的越界，且实测预留与按版本推算一致。
                assert fee * 2 > parsed, (
                    f"{version}: 费率 {rate} 被拒绝，但两倍承诺费 {fee * 2} 并未超过错误文本里的 "
                    f"reserved fee {parsed}; 记录={record}"
                )
                assert parsed == reserved_fee, (
                    f"{version}: 错误文本里的 reserved fee {parsed} 与按版本计算的 "
                    f"{reserved_fee} 不一致; 记录={record}"
                )
        return record

    # ---- xUDT helpers (CKB assertions above stay untouched) --------------

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

    def record_udt_channel(self, udt):
        """xUDT 通道的本金只读 output_data；capacity 预留只适用于 CKB 通道。"""
        channels = [self.channel(f) for f in self.fibers]
        assert all(c["funding_udt_type_script"] == udt for c in channels)
        outpoint = bytes.fromhex(channels[0]["channel_outpoint"][2:])
        assert outpoint[32:] == bytes(4)
        self.funding_tx = "0x" + outpoint[:32].hex()
        self.signed_hashes = [c["latest_commitment_transaction_hash"] for c in channels]
        self.udt_principals = [int(c["local_balance"], 16) for c in channels]
        funding = self.ckb.get_transaction(self.funding_tx)["transaction"]
        assert sum(self.udt_principals) == udt_amount(
            funding["outputs"][0], funding["outputs_data"][0], udt
        )
        self.wallet_before = self.wallet_balances()
        self.tokens_before = self.token_balances()

    def open_ready_udt(self, udt, funding_units=None):
        """用原始 RPC 开通 xUDT 通道。

        框架 `open_channel` 的 udt 分支会把 DEFAULT_MIN_DEPOSIT_CKB（CKB 量级）加到 UDT
        单位上，所以这里显式发 funding_udt_type_script，接收方按 UDT 规则自动接受（0 出资）。
        """
        self.fiber1.connect_peer(self.fiber2)
        existing = {
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
            self.fiber1.get_client(), self.fiber2.get_pubkey(), "ChannelReady", existing
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
        self.record_udt_channel(udt)

    def hold_one_payment_udt(self, udt, amount, algorithm="ckb_hash"):
        """像 hold_one_payment，但 invoice 带 udt_type_script 且金额是 UDT 单位。"""
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
                "final_expiry_delta": hex(9_600_000),
                "expiry": "0xe10",
                "udt_type_script": udt,
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
                return payment_hash, preimage
            time.sleep(1)
        self.fail(f"Pending xUDT TLC was not committed on selected channel: {channels}")

    def assert_tlc_settlement_udt(self, previous, tx, code_tx, pending, preimage, udt):
        """xUDT 版 TLC 结算断言；CKB 版 assert_tlc_settlement 保持不变。"""
        version = self.commitment_version
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
        version = self.commitment_version
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

    def settle_held_channel_udt(self, code_tx, payment_hash, preimage, amount, udt):
        commitment = self.force_close(self.fiber2)
        self.assert_commitment_layout(commitment)
        self.ckb.generate_epochs("0x1")
        self.fiber2.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )
        settled = self.wait_for_spend(commitment["hash"])
        self.assert_tlc_settlement_udt(
            commitment, settled, code_tx, [(payment_hash, amount)], preimage, udt
        )
        self.udt_principals[0] -= amount
        self.udt_principals[1] += amount
        self.assert_settled_udt(
            settled, code_tx, udt, self.get_tx_message(commitment["hash"])["fee"]
        )
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

    # ---- tests -----------------------------------------------------------

    # TEST-MAP: H32V2-19
    # TEST-EVIDENCE-BEGIN: H32V2-19
    # Evidence | partial | 只在 CKB/短锁/普通开通下探测费率边界：50_000_000 与 50_000_001 都被真实
    # 提交，u64::MAX 费率触发真实拒绝并从错误文本解析 reserved fee；H32V2-19 的短锁 CKB 版本化预留
    # 与 1 笔已承诺 TLC 的 CKB witness 宽度改由
    # test_full_hash_channels.py::test_upgrade_then_settle_committed_tlc_with_new_code 证明（本文件
    # 不再重复该场景）。UDT 承诺费不按 CKB reserved_fee 口径断言（其资产结算见
    # test_xudt_witness_width_and_settlement_are_versioned）；外部注资与接收
    # 开通路径、长 shutdown lock 未覆盖。
    # TEST-EVIDENCE-END: H32V2-19
    def test_commitment_fee_boundary_is_version_aware(self):
        """核对 50_000_000 / 50_000_001 两个费率，并用越界费率取回真实 reserved fee。"""
        results = {}
        for version in ("v1", "legacy"):
            with self.subTest(version=version):
                self.select_peers(*self.peers_for(version), version)
                reserved_fee = self.reserved_fee(version)
                tx_size = self.commitment_tx_size(version)
                # 先证明这两个费率的两倍承诺费都落在按版本算出的预算内。
                for rate in (EXACT_BUDGET_RATE, REVIEW_ONE_SHANNON_RATE):
                    assert self.commitment_fee(rate, version) * 2 <= reserved_fee, (
                        f"{version}: 费率 {rate} 的两倍承诺费 "
                        f"{self.commitment_fee(rate, version) * 2} 不应超过按版本计算的 "
                        f"reserved_fee {reserved_fee} (tx_size={tx_size})"
                    )
                exact = self.probe_boundary(version, EXACT_BUDGET_RATE)
                assert (
                    exact["result"] == "accepted"
                ), f"{version}: 预算内费率 {EXACT_BUDGET_RATE} 应被接受: {exact}"
                one_shannon = self.probe_boundary(version, REVIEW_ONE_SHANNON_RATE)
                # 真正越界的费率必须被拒绝：这才是费用校验的可观察行为。
                overflow = self.probe_boundary(version, 2**64 - 1)
                assert (
                    overflow["result"] == "rejected"
                ), f"{version}: 超出预留的费率 u64::MAX 必须被拒绝: {overflow}"
                results[version] = {
                    "exact_budget": exact,
                    "review_one_shannon": one_shannon,
                    "overflow": overflow,
                }
        print("费用边界观测:", results)

    # TEST-MAP: H32V2-19
    # TEST-EVIDENCE-BEGIN: H32V2-19
    # Evidence | partial | xUDT：普通开通 V1 (new-new) 与 Legacy (new1-legacy_peer) xUDT 通道，断言
    # 承诺锁布局 58/0x01 vs 57、1 笔已承诺 TLC 的 settle 含 commitment-lock 与 xUDT 代码 dep、witness
    # 宽度 90 + (97|85) * 1 + 99、V1 比 Legacy 宽 12 字节，以及 xUDT 资产减量与收款净增量、付款 Success
    # 与正确原像。评审行的短锁 CKB 自动预留 99/100 CKB 是 CKB 专属，不为 UDT 编造等价数值，本方法
    # 不断言；该 CKB 预留与 CKB witness 宽度由
    # test_full_hash_channels.py::test_upgrade_then_settle_committed_tlc_with_new_code 证明；
    # 0 笔/多笔、外部注资/接收开通、长 shutdown lock、xUDT 费用记账仍未覆盖。
    # TEST-EVIDENCE-END: H32V2-19
    def test_xudt_witness_width_and_settlement_are_versioned(self):
        """xUDT 通道的 V1/Legacy 布局与 1 笔已承诺 TLC 的真实强关结算，V1 宽 12 字节。"""
        code_tx = self.current_contract_code_tx()
        udt = self.udt_script()
        widths = {}
        for version in ("v1", "legacy"):
            with self.subTest(version=version):
                # peers_for 保证出资方始终是持有 xUDT 的 new1；Legacy 对端是 PR base 旧节点。
                self.select_peers(*self.peers_for(version), version)
                self.open_ready_udt(udt)
                # 只保留一笔已承诺 TLC；settle_held_channel_udt 会强关、公布原像并核对链上结算。
                payment_hash, preimage = self.hold_one_payment_udt(udt, amount=100)
                self.settle_held_channel_udt(code_tx, payment_hash, preimage, 100, udt)
                # 重新取原始链上交易：funding -> 承诺 -> 带原像的派生结算交易。
                commitment = self.wait_for_spend(self.funding_tx)
                settled = self.wait_for_spend(commitment["hash"])
                raw = bytes.fromhex(settled["witnesses"][0][2:])
                expected = witness_size(version, 1)
                assert len(raw) == expected, (
                    f"{version}: 1 笔已承诺 xUDT TLC 的 witness 宽度应为 {expected}，"
                    f"实测 {len(raw)}: {settled['witnesses'][0]}"
                )
                widths[version] = len(raw)
        # 1 字节 feature + 每笔 TLC 多出的 12 字节完整哈希。
        assert widths["v1"] - widths["legacy"] == 12, widths
        print("xUDT witness 宽度观测:", widths)
