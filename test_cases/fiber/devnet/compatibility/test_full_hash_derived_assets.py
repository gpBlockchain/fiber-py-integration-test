"""H32-09: two sequential V1 claims on real CKB and xUDT cells.

Run serially with other devnet tests (the existing CKB CLI shares /tmp files).
xUDT 直接用默认 devnet 快照里已部署的合约（框架 `self.udtContract` / `XUDT_TX_HASH`），
本文件不再自己部署 UDT。
"""

import secrets
import socket
import time

from framework.basic_fiber import COMMIT_LOCK_CODE_HASH
from framework.config import DEFAULT_MIN_DEPOSIT_CKB
from framework.helper.settlement_witness import SettlementWitness
from framework.util import ckb_hash
from test_cases.fiber.devnet.compatibility.contract_upgrade_support import (
    CKB,
    ContractUpgradeSupport,
)


def asset_amount(output, data, udt):
    """Read only the requested asset; reject malformed matching xUDT data."""
    if udt is None:
        return int(output["capacity"], 16)
    if output.get("type") != udt:
        return 0
    raw = bytes.fromhex(data.removeprefix("0x"))
    assert len(raw) == 16, "xUDT amount must be a 16-byte little-endian integer"
    return int.from_bytes(raw, "little")


class TestFullHashDerivedAssets(ContractUpgradeSupport):
    tmp_path_name = f"report/h32-derived-assets-{time.time_ns()}"
    ckb_rpc_port, ckb_p2p_port = 22014, 22015
    fiber1_rpc_port, fiber1_p2p_port = 22028, 22027
    fiber2_rpc_port, fiber2_p2p_port = 22029, 22030
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 2}
    commitment_version = "v1"

    @classmethod
    def setup_class(cls):
        for port in (22014, 22015, 22028, 22027, 22029, 22030):
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", port))
        super().setup_class()
        cls.ckb = cls.node.getClient()
        # 默认快照已部署 xUDT 且框架已发币给 fiber1/account1，这里只取它的脚本与代码 dep。
        cls.udt_code = cls.udtContract.contract_hash
        cls.udt_script = {
            "code_hash": cls.udtContract.get_code_hash(True, cls.node.rpcUrl),
            "hash_type": "type",
            "args": cls.udtContract.get_owner_arg_by_lock_arg(cls.account1["lock_arg"]),
        }
        cls.fiber1.connect_peer(cls.fiber2)
        cls.processes = cls.node_processes()

    def token_balances(self):
        return [
            self.udtContract.balance(self.ckb, self.account1["lock_arg"], a["lock_arg"])
            for a in (self.account1, self.account2)
        ]

    # H32-09 的逐笔兑现判据（CKB / xUDT 共用）：一次只兑付一笔，且派生 cell 必须保留 V1 布局。
    def assert_claim(self, previous, tx, code_tx, pending, preimage, udt):
        # 必须执行升级后的 commitment-lock；xUDT 场景还要带上 xUDT 的代码 dep。
        assert {
            "out_point": {"tx_hash": code_tx, "index": "0x0"},
            "dep_type": "code",
        } in tx["cell_deps"]
        if udt is not None:
            assert {
                "out_point": {"tx_hash": self.udt_code, "index": "0x0"},
                "dep_type": "code",
            } in tx["cell_deps"]
        witness = SettlementWitness.from_hex(tx["witnesses"][0], version="v1")
        assert witness.to_hex() == tx["witnesses"][0]
        witness.assert_single_tlc_claim(pending, preimage)
        # SPEC-06 还要求剩余 TLC 的顺序正确：assert_pending_tlcs 会排序比较，这里额外按解析顺序
        # 逐项核对，确保派生 cell 没有重排或丢失 TLC 条目。
        assert [t.payment_hash for t in witness.tlcs] == [
            bytes.fromhex(h.removeprefix("0x")) for h, _ in pending
        ], (witness.tlcs, pending)
        # Explicit byte-width assertion in addition to strict parser round-trip.
        # 显式字节宽度：每笔剩余 TLC 仍按 V1 的 97 字节条目计（90 前缀 + 97×N + 99 余额与解锁）。
        assert len(bytes.fromhex(tx["witnesses"][0][2:])) == 90 + 97 * len(pending) + 99
        amount = witness.tlcs[witness.unlocks[0].unlock_type].amount
        before, after = previous["outputs"][0], tx["outputs"][0]
        # 花费前后的承诺锁都必须是 58 字节 + 末位 0x01：派生一次也不能丢版本位。
        before_args = bytes.fromhex(before["lock"]["args"][2:])
        assert len(before_args) == 58 and before_args[-1] == 1
        args = bytes.fromhex(after["lock"]["args"][2:])
        assert after["lock"]["code_hash"] == COMMIT_LOCK_CODE_HASH
        assert (
            after["lock"]["hash_type"] == "type" and len(args) == 58 and args[-1] == 1
        )
        # 前 36 字节（通道身份与承诺号前缀）保持不变，避免把换锁误当成同布局派生。
        assert args[:36] == before_args[:36]
        # 只比较本资产：CKB 看 capacity，xUDT 看 16 字节小额端金额；减少值须恰等于本次兑现金额。
        assert before["type"] == after["type"] == udt
        assert (
            asset_amount(before, previous["outputs_data"][0], udt)
            - asset_amount(after, tx["outputs_data"][0], udt)
        ) == amount
        # Count recipient outputs minus their actual wallet inputs, not gross
        # output totals (xUDT claims may consume wallet capacity/change).
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
                        net[owner] += sign * asset_amount(
                            output, transaction["outputs_data"][index], udt
                        )
        fee = self.get_tx_message(tx["hash"])["fee"]
        # 收款人净增量扣除实际矿工费：CKB 承载费用，xUDT 转账不计费。
        expected_net = [0, amount - fee] if udt is None else [0, amount]
        assert net == expected_net
        # 派生 cell 仍 live：第一次兑现后剩余资产没有被锁死。
        assert self.ckb.get_live_cell("0x0", tx["hash"])["status"] == "live"
        self.assert_nodes_running()
        print(
            "V1 derived claim:",
            tx["hash"],
            "asset:",
            "CKB" if udt is None else "xUDT",
            "remaining TLCs:",
            len(pending) - 1,
            "net:",
            net,
            "fee:",
            fee,
        )

    # H32-09 证明链：V1 承诺含至少两笔 TLC，先兑现一笔再从派生 cell 兑现另一笔，分别用 CKB 与 xUDT。
    # 1) 两笔待结算输入：用两份 invoice 让同一通道内同时存在两笔已完成承诺握手的 hold TLC；
    #    每轮都断言两端 pending_tlcs 恰好是 payments[:count]（哈希与金额），不是只看笔数。
    # 2) 真实资产：CKB 与 xUDT 各跑一遍；xUDT 直接用默认快照已部署的合约（框架已发币且两个节点
    #    的配置自带白名单）；本金取自实际 funding 输出（sum(principals) == funding 资产额），
    #    不写死期望值。
    # 3) 逐笔派生：先公布第一笔原像，等该承诺 cell 被花费并确认，再公布第二笔；第二笔的承诺对象
    #    是上一笔留下的派生 cell，而不是最初那份承诺。
    # 4) 每笔都验 V1 布局：花费前后的承诺锁均为 58 字节 + 末位 0x01，args 前 36 字节不变；witness
    #    按 v1 解析并往返一致，另显式校验字节宽度 90 + 97×剩余笔数 + 99 → 剩余 TLC 仍是 97 字节条目。
    # 5) 金额与收尾：每笔减少的资产额等于该 TLC 金额，收款人净增量扣除实际矿工费（xUDT 不计费）；
    #    两笔兑现后继续花费剩余派生 cell，要求 TLC 清单为空、仍为 58/0x01 且资产类型不变，直至不再
    #    有 commitment-lock 输出；最后核对 CKB/UDT 余额、两笔付款 Success 与正确原像、TLC 终态、
    #    本端最终关闭。
    # TEST-MAP: H32V2-09
    # TEST-EVIDENCE-BEGIN: H32V2-09
    # Evidence | partial | V1/CKB and the adjacent V1/xUDT extension both settle two committed TLCs on
    # the derived cell and now also assert the remaining-TLC ORDER (SPEC-06) instead of only a sorted
    # comparison. Legacy two-claim derivation is not repeated here — it is exercised by H32V2-23
    # (Legacy, two TLCs, claim before and after the in-place code upgrade). The asset matrix here is CKB
    # and xUDT (the project's UDT asset).
    # TEST-EVIDENCE-END: H32V2-09
    def test_ckb_two_claims_preserve_v1_and_balances(self):
        # CKB 场景：承诺 cell 与派生 cell 的资产都是 capacity。
        self.two_claims(None)

    # TEST-MAP: H32V2-09
    # 本仓库的 UDT 就是 xUDT，该扩展同样计入 H32V2-09。
    def test_xudt_two_claims_preserve_v1_and_balances(self):
        # xUDT 场景：承诺 cell 带 xUDT type script，金额在 output_data 的 16 字节小额端里。
        self.two_claims(self.udt_script)

    def udt_funding_units(self):
        """接收方只在 funding_amount >= udt_cfg_infos.auto_accept_amount 时自动接受 UDT 开通。

        dev 模板默认 1_000_000_000；小于该值会停在 NegotiatingFunding，永远到不了 ChannelReady。
        """
        infos = self.fiber2.get_client().node_info().get("udt_cfg_infos") or []
        amounts = [
            int(info["auto_accept_amount"], 16)
            for info in infos
            if info.get("auto_accept_amount")
        ]
        return (max(amounts) if amounts else 1_000_000_000) + 1

    def two_claims(self, udt):
        # 默认快照已部署升级后的 commitment-lock，直接用当前代码 cell。
        code_tx = self.current_contract_code_tx()
        # CKB 与 xUDT 各自新开一条通道：本金、资产类型与后续断言互不串用。
        previous_ids = {
            c["channel_id"]
            for c in self.fiber1.get_client().list_channels({})["channels"]
        }
        # Raw RPC avoids the framework helper's CKB reserve addition to UDT units.
        self.fiber1.get_client().open_channel(
            {
                "pubkey": self.fiber2.get_pubkey(),
                "public": True,
                "funding_amount": hex(
                    1000 * CKB if udt is None else self.udt_funding_units()
                ),
                "funding_udt_type_script": udt,
            }
        )
        self.channel_id = self.wait_for_new_channel_state(
            self.fiber1.get_client(),
            self.fiber2.get_pubkey(),
            "ChannelReady",
            previous_ids,
        )
        for _ in range(60):
            channels = [self.channel(f) for f in self.fibers]
            if all(c["state"]["state_name"] == "ChannelReady" for c in channels):
                break
            time.sleep(1)
        else:
            self.fail(f"Both peers must be ready: {channels}")
        assert all(
            c["funding_udt_type_script"] == udt and not c["pending_tlcs"]
            for c in channels
        )
        point = bytes.fromhex(channels[0]["channel_outpoint"][2:])
        assert len(point) == 36 and point[32:] == bytes(4)
        self.funding_tx = "0x" + point[:32].hex()
        # 期望本金必须与链上 funding 输出的本资产金额自洽，而不是写死的数字。
        principals = [
            int(c["local_balance"], 16) + (DEFAULT_MIN_DEPOSIT_CKB if udt is None else 0)
            for c in channels
        ]
        funding = self.ckb.get_transaction(self.funding_tx)["transaction"]
        assert sum(principals) == asset_amount(
            funding["outputs"][0], funding["outputs_data"][0], udt
        )
        wallet_before, tokens_before = self.wallet_balances(), self.token_balances()
        # 两笔待结算输入：1 单位与 2 单位（CKB 用 CKB、xUDT 用最小单位），金额不同便于区分兑付顺序。
        preimages = ["0x" + secrets.token_hex(32) for _ in range(2)]
        payments = [
            (ckb_hash(p), n * (CKB if udt is None else 100))
            for p, n in zip(preimages, (1, 2))
        ]
        for count, (payment_hash, amount) in enumerate(payments, 1):
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
            for _ in range(60):
                channels = [self.channel(f) for f in self.fibers]
                if all(
                    len(c["pending_tlcs"]) == count
                    and all(
                        "Committed" in t["status"].values() for t in c["pending_tlcs"]
                    )
                    for c in channels
                ):
                    break
                time.sleep(1)
            else:
                self.fail(f"TLC commitment handshake incomplete: {channels}")
            # 两端都必须持有这两笔（哈希 + 金额），不是只统计笔数。
            for channel in channels:
                assert sorted(
                    (t["payment_hash"], int(t["amount"], 16))
                    for t in channel["pending_tlcs"]
                ) == sorted(payments[:count])
        self.signed_hashes = [c["latest_commitment_transaction_hash"] for c in channels]
        # 冻结签名承诺后由对端强关，作为两笔派生的起点。
        commitment = self.force_close(self.fiber2)
        self.ckb.generate_epochs("0x1")
        tx = commitment
        fees = self.get_tx_message(tx["hash"])["fee"]
        for index, preimage in enumerate(preimages):
            # The second preimage remains secret until the first derived cell is live.
            # 逐笔公布原像：每次都在上一轮留下的承诺/派生 cell 上花费并等待确认。
            self.fiber2.get_client().settle_invoice(
                {"payment_hash": payments[index][0], "payment_preimage": preimage}
            )
            following = self.wait_for_spend(tx["hash"])
            self.assert_claim(tx, following, code_tx, payments[index:], preimage, udt)
            fees += self.get_tx_message(following["hash"])["fee"]
            tx = following
        # 两笔兑现后继续清剩余派生 cell：TLC 清单必须为空，且仍为 58/0x01 与同一资产类型。
        for _ in range(3):
            if not any(
                o["lock"]["code_hash"] == COMMIT_LOCK_CODE_HASH for o in tx["outputs"]
            ):
                break
            self.ckb.generate_epochs("0x2")
            tx = self.wait_for_spend(tx["hash"])
            witness = SettlementWitness.from_hex(tx["witnesses"][0], version="v1")
            witness.assert_pending_tlcs([])
            assert {
                "out_point": {"tx_hash": code_tx, "index": "0x0"},
                "dep_type": "code",
            } in tx["cell_deps"]
            for output in tx["outputs"]:
                if output["lock"]["code_hash"] == COMMIT_LOCK_CODE_HASH:
                    args = bytes.fromhex(output["lock"]["args"][2:])
                    assert len(args) == 58 and args[-1] == 1 and output["type"] == udt
            fees += self.get_tx_message(tx["hash"])["fee"]
        assert not any(
            o["lock"]["code_hash"] == COMMIT_LOCK_CODE_HASH for o in tx["outputs"]
        )
        # 两笔都兑现后核对资产归属：CKB 按扣除总矿工费核对双方 capacity，xUDT 核对代币增量。
        transferred = sum(amount for _, amount in payments)
        principals[0] -= transferred
        principals[1] += transferred
        ckb_delta = [a - b for a, b in zip(self.wallet_balances(), wallet_before)]
        token_delta = [a - b for a, b in zip(self.token_balances(), tokens_before)]
        assert 0 <= fees < CKB // 100
        assert sum(ckb_delta) == int(funding["outputs"][0]["capacity"], 16) - fees
        if udt is None:
            assert token_delta == [0, 0]
            assert all(
                expected - fees <= actual <= expected
                for actual, expected in zip(ckb_delta, principals)
            )
        else:
            assert token_delta == principals
        # 两笔付款都要记录 Success、持有各自正确原像，且对应 TLC 进入终态。
        for (payment_hash, _), preimage in zip(payments, preimages):
            self.wait_payment_state(self.fiber1, payment_hash, "Success")
            assert (
                self.fiber1.get_client().get_payment({"payment_hash": payment_hash})[
                    "payment_preimage"
                ]
                == preimage
            )
            for fiber in self.fibers:
                self.wait_tlc_terminal(fiber, payment_hash, timeout=660)
        self.assert_local_closed()
        self.assert_nodes_running()
