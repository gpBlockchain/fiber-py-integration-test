"""Issue 1705 的公开接口 fixture；链上消费/净额复用已确认的退款首例。"""

import hashlib
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

from framework.basic_fiber import COMMIT_LOCK_CODE_HASH
from framework.config import DEFAULT_MIN_DEPOSIT_CKB
from framework.helper.settlement_witness import SettlementWitness
from framework.util import ckb_hash, get_project_root
from test_cases.fiber.devnet.trampoline_routing import (
    test_trampoline_timeout_refund as refund,
)

CKB = refund.CKB
RESULT_WAIT = refund.RESULT_WAIT
AMOUNT = 1500 * CKB


def clear_bootnodes(path):
    """只编辑 fiber.bootnode_addrs；不让 YAML 重序列化改变哈希/epoch 的类型。"""
    lines = Path(path).read_text().splitlines(keepends=True)
    result, in_fiber, skipping, found = [], False, False, False
    for line in lines:
        if line.rstrip() == "fiber:":
            assert not found, "重复 fiber 配置段"
            found, in_fiber = True, True
            result.extend([line, "  bootnode_addrs: []\n"])
            continue
        if (
            in_fiber
            and line.strip()
            and not line[0].isspace()
            and not line.startswith("#")
        ):
            in_fiber, skipping = False, False
        if in_fiber and line.startswith("  bootnode_addrs:"):
            skipping = True
            continue
        if skipping:
            if not line.strip() or line.startswith("    ") or line.startswith("  -"):
                continue
            skipping = False
        result.append(line)
    assert found, "缺少 fiber 配置段"
    Path(path).write_text("".join(result))


def binary_config(path):
    path = Path(path).resolve()
    assert path.is_file() and os.access(path, os.X_OK), f"缺少正式构建: {path}"
    return SimpleNamespace(
        fiber_config_path="/source/fiber/dev_config_3.yml.j2",
        fiber_bin_path=os.path.relpath(path, get_project_root()),
    )


def payment_hash(preimage, algorithm):
    return (
        "0x" + hashlib.sha256(bytes.fromhex(preimage[2:])).hexdigest()
        if algorithm == "sha256"
        else ckb_hash(preimage)
    )


class MppFixture(refund.TestTrampolineTimeoutRefund):
    test_upstream_refund_after_all_downstream_timeouts = None
    # 和默认开发网络隔离；类/用例顺序运行，不允许同一套端口并发。
    tmp_path_name = "tmp/mpp1705-all"
    ckb_rpc_port, ckb_p2p_port = 18114, 18125
    fiber1_rpc_port, fiber1_p2p_port = 18228, 18227
    fiber2_rpc_port, fiber2_p2p_port = 18229, 18230
    extra_fiber_rpc_port, extra_fiber_p2p_port = 18251, 18402
    fnn_log_level = "info"
    start_fiber_config = {"fiber_watchtower_check_interval_seconds": 3}

    @classmethod
    def setup_class(cls):
        super().setup_class()
        cls._mpp_method_started = False

    def setup_method(self, method):
        cls = type(self)
        if cls._mpp_method_started:
            cls.teardown_class()
            cls.setup_class()
        cls._mpp_method_started = True
        super().setup_method(method)

    def start_new_fiber(
        self, account_private_key, config=None, fiber_version=None, env=None
    ):
        return super().start_new_fiber(
            account_private_key,
            config=config,
            fiber_version=fiber_version or self.fiber_version,
            env=env,
        )

    def _edge(self, left, right, capacity, private=False, udt=None):
        # 私有接收端不调用仅适用于 public 通道的 update_channel。
        # 实际正向发送者费率仍为 0；私有反向费率不参与本组付款。
        cid = self.open_channel(
            left,
            right,
            capacity,
            0,
            0,
            1000 if private else 0,
            udt=udt,
            other_config={
                "public": not private,
                "commitment_delay_epoch": "0x10000000001",
            },
        )
        before = [self._channel(f, cid) for f in (left, right)]
        raw = bytes.fromhex(before[0]["channel_outpoint"][2:])
        assert len(raw) == 36
        return dict(
            left=left,
            right=right,
            cid=cid,
            before=before,
            commitment_delay_epoch=0x10000000001,
            funding={
                "tx_hash": "0x" + raw[:32].hex(),
                "index": hex(int.from_bytes(raw[32:], "little")),
            },
        )

    def _topology(self, kind="D", algorithm="ckb_hash"):
        self.p, self.m = self.fiber1, self.fiber2
        self.r = self.start_new_fiber(
            self.generate_account(20000), fiber_version=self.fiber_version
        )
        self.kind, self.algorithm = kind, algorithm
        if kind == "D":
            self.split = [self._edge(self.p, self.m, 1000 * CKB) for _ in range(2)]
            self.tail = self._edge(self.m, self.r, 3000 * CKB)
            self.edges = self.split + [self.tail]
        else:
            self.up = self._edge(self.p, self.m, 3000 * CKB)
            if kind == "TF":
                self.bridge = self.start_new_fiber(
                    self.generate_account(20000), fiber_version=self.fiber_version
                )
                self.split = [
                    self._edge(self.m, self.r, 1000 * CKB),
                    self._edge(self.m, self.bridge, 1000 * CKB),
                ]
                self.tail = self._edge(self.bridge, self.r, 2000 * CKB)
                self.edges = [self.up] + self.split + [self.tail]
            else:
                self.split = [
                    self._edge(self.m, self.r, 1000 * CKB, private=True)
                    for _ in range(2)
                ]
                self.edges = [self.up] + self.split
            self._wait(
                "trampoline 能力传播",
                lambda: any(
                    n["pubkey"] == self.m.get_pubkey()
                    and "TRAMPOLINE_ROUTING_REQUIRED" in n["features"]
                    for n in self.p.get_client().graph_nodes({})["nodes"]
                ),
            )
        for f in self.fibers:
            f.get_client().try_count, f.get_client().timeout = 1, 10
        expected_graph = len(self.edges) if kind != "T" else 1
        self._wait(
            "所需通道已同步",
            lambda: len(self.p.get_client().graph_channels({})["channels"])
            >= expected_graph,
        )
        assert all(
            int(e["before"][0]["local_balance"], 16) < AMOUNT for e in self.split
        )
        assert (
            sum(int(e["before"][0]["local_balance"], 16) for e in self.split) >= AMOUNT
        )
        self.preimage = self.generate_random_preimage()
        self.hash = payment_hash(self.preimage, algorithm)
        invoice = self.r.get_client().new_invoice(
            {
                "amount": hex(AMOUNT),
                "currency": "Fibd",
                "payment_hash": self.hash,
                "hash_algorithm": algorithm,
                "allow_mpp": True,
                "allow_trampoline_routing": kind != "D",
                "expiry": hex(86400),
                "final_expiry_delta": hex(43_200_000),
            }
        )
        request = dict(
            invoice=invoice["invoice_address"],
            max_parts=hex(2),
            timeout=hex(1800),
            max_fee_amount=hex(AMOUNT // 100 if kind != "D" else 0),
            tlc_expiry_limit=hex(172_800_000),
        )
        if kind != "D":
            request["trampoline_hops"] = [self.m.get_pubkey()]
        response = self.p.get_client().send_payment(request)
        assert response["payment_hash"] == self.hash
        self.wait_invoice_state(self.r, self.hash, "Received", timeout=120)
        for edge in self.edges:
            self._capture_committed(edge)
        assert sum(e["sent"] for e in self.split) == AMOUNT
        if kind != "D":
            assert 0 < self.up["sent"] - AMOUNT <= AMOUNT // 100
        self._no_preimage(self.p)
        if kind != "D":
            self._no_preimage(self.m)
        self._expiry_margin(1800)
        print(
            json.dumps(
                {
                    "hash": self.hash,
                    "kind": kind,
                    "edges": [
                        dict(channel=e["cid"], amount=e["sent"], tlcs=e["tlcs"])
                        for e in self.edges
                    ],
                }
            ),
            flush=True,
        )

    def _capture_committed(self, edge):
        def read():
            rows = [
                [
                    t
                    for t in self._channel(f, edge["cid"])["pending_tlcs"]
                    if t["payment_hash"] == self.hash
                ]
                for f in (edge["left"], edge["right"])
            ]
            if not rows[0] or len(rows[0]) != len(rows[1]):
                return None
            if not all(
                t["status"] == {direction: "Committed"}
                for ts, direction in zip(rows, ("Outbound", "Inbound"))
                for t in ts
            ):
                return None
            identity = lambda t: (t["id"], t["amount"], t["expiry"])
            assert sorted(map(identity, rows[0])) == sorted(
                map(identity, rows[1])
            ), rows
            return rows[0]

        edge["tlcs"] = self._wait("逐跳两端 Committed", read)
        edge["sent"] = sum(int(t["amount"], 16) for t in edge["tlcs"])

    def _expiry_margin(self, seconds):
        # 正式版 epoch 保护窗口按公开 commitment delay 和 4 小时/epoch 换算。
        for edge in self.edges:
            delay = edge["commitment_delay_epoch"]  # 本 fixture 显式提交的公开开通参数
            number, index, length = (
                delay & 0xFFFFFF,
                (delay >> 24) & 0xFFFF,
                (delay >> 40) & 0xFFFF,
            )
            assert length > 0
            protection = (number + index / length) * 14400 * 2 / 3
            assert min(int(t["expiry"], 16) / 1000 for t in edge["tlcs"]) > (
                time.time() + seconds + protection + 120
            ), "TLC 到期保护裕量不足"

    def _no_preimage(self, fiber):
        row = fiber.get_client().get_payment({"payment_hash": self.hash})
        assert row["status"] != "Success" and row.get("payment_preimage") is None, row
        return row

    def _success(self, fiber, isolated=False, timeout=RESULT_WAIT):
        def read():
            if isolated:
                assert (
                    fiber.get_client().list_peers()["peers"] == []
                ), "原像来源被 Fiber 重连污染"
            row = fiber.get_client().get_payment({"payment_hash": self.hash})
            if row["status"] != "Success":
                return None
            assert row.get("payment_preimage") == self.preimage, row
            assert not row.get("failed_error"), row
            assert payment_hash(row["payment_preimage"], self.algorithm) == self.hash
            return row

        return self._wait("正确原像及成功状态", read, timeout=timeout)

    def _restart(self, fiber, peers=(), config=None):
        fiber.stop()
        return self._start(fiber, peers=peers, config=config)

    def _start(self, fiber, peers=(), config=None):
        # 运维配置中清除 bootstrap；隔离用例还会逐次断言 list_peers 为空。
        path = Path(fiber.tmp_path) / "config.yml"
        clear_bootnodes(path)
        if config is not None:
            fiber.fiber_config_enum = config
        fiber.start(fnn_log_level=self.fnn_log_level, timeout=120)
        self._wait(
            "node_info 可用", lambda: fiber.get_client().node_info(), timeout=120
        )
        for peer in peers:
            fiber.connect_peer(peer)
        return time.monotonic()

    def _settle(self, wait_paid=True):
        self.r.get_client().settle_invoice(
            {"payment_hash": self.hash, "payment_preimage": self.preimage}
        )
        if wait_paid:
            self.wait_invoice_state(self.r, self.hash, "Paid", timeout=120)

    def _open(self, edge, offline=()):
        assert (
            self._spend(edge["funding"]) is None
        ), f"原通道 funding 已消费: {edge['cid']}"
        rows = []
        for fiber in (edge["left"], edge["right"]):
            if fiber in offline:
                continue
            row = self._channel(fiber, edge["cid"])
            assert row["state"]["state_name"] == "ChannelReady", row
            rows.append(row)
        return rows

    def _held(self, edge, offline=()):
        self._open(edge, offline)
        for index, fiber in enumerate((edge["left"], edge["right"])):
            if fiber in offline:
                continue
            row = self._channel(fiber, edge["cid"])
            tlcs = [t for t in row["pending_tlcs"] if t["payment_hash"] == self.hash]
            assert {(t["id"], t["amount"]) for t in tlcs} == {
                (t["id"], t["amount"]) for t in edge["tlcs"]
            }
            assert all(
                t["status"] == {("Outbound", "Inbound")[index]: "Committed"}
                for t in tlcs
            )
            assert (
                int(row[("offered_tlc_balance", "received_tlc_balance")[index]], 16)
                == edge["sent"]
            )
            for field in ("local_balance", "remote_balance"):
                assert row[field] == edge["before"][index][field], (
                    row,
                    edge["before"][index],
                )

    def _cleared(self, edge, success=True):
        rows = self._open(edge)
        if any(
            int(row[key], 16)
            for row in rows
            for key in ("offered_tlc_balance", "received_tlc_balance")
        ):
            return False
        for i, row in enumerate(rows):
            for t in row["pending_tlcs"]:
                if t["payment_hash"] == self.hash:
                    if list(t["status"].values()) != ["RemoveAckConfirmed"]:
                        return False
            delta = edge["sent"] * (-1 if i == 0 else 1) if success else 0
            assert (
                int(row["local_balance"], 16)
                == int(edge["before"][i]["local_balance"], 16) + delta
            )
            assert (
                int(row["remote_balance"], 16)
                == int(edge["before"][i]["remote_balance"], 16) - delta
            )
        return True

    def _close(self, edge, broadcaster):
        assert len(edge["tlcs"]) == 1
        funding = self._output(edge["funding"])
        broadcaster.get_client().shutdown_channel(
            {"channel_id": edge["cid"], "force": True}
        )
        tx = self._wait(
            "指定 funding 强关交易确认", lambda: self._spend(edge["funding"])
        )
        locked = [
            i
            for i, o in enumerate(tx["outputs"])
            if o["lock"]["code_hash"] == COMMIT_LOCK_CODE_HASH
        ]
        assert len(locked) == 1, tx
        locks = [
            self.get_account_script(f.account_private)
            for f in (edge["left"], edge["right"])
        ]
        net, fee = self._net_refund(tx, edge["funding"], locks)
        # 编码来自公开 commitment 的版本字节，而不是尝试解析直到成功。
        args = bytes.fromhex(tx["outputs"][locked[0]]["lock"]["args"][2:])
        assert len(args) in (57, 58)
        version = "v1" if len(args) == 58 and args[-1] == 1 else "legacy"
        chain = dict(
            edge=edge,
            locks=locks,
            net=net,
            fee=fee,
            claimed=False,
            done=False,
            version=version,
            seen=False,
            funding=int(funding["capacity"], 16),
            outpoint={"tx_hash": tx["hash"], "index": hex(locked[0])},
        )
        return chain

    def _advance_chain(self, chain, claim=True):
        if chain["done"]:
            return True
        previous = chain["outpoint"]
        tx = self._spend(previous)
        if tx is None:
            return False
        index = next(
            i
            for i, inp in enumerate(tx["inputs"])
            if inp["previous_output"] == previous
        )
        witness = SettlementWitness.from_hex(
            tx["witnesses"][index], version=chain["version"]
        )
        edge = chain["edge"]
        if not chain["seen"]:
            witness.assert_pending_tlcs([(self.hash, edge["sent"])])
            chain["seen"] = True
        for unlock in witness.unlocks:
            if unlock.preimage is not None:
                assert claim and unlock.preimage == bytes.fromhex(self.preimage[2:])
                assert unlock.unlock_type < len(witness.tlcs)
                tlc = witness.tlcs[unlock.unlock_type]
                expected_hash = bytes.fromhex(self.hash[2:])[: len(tlc.payment_hash)]
                assert (tlc.payment_hash, tlc.amount) == (expected_hash, edge["sent"])
                chain["claimed"] = True
        if not claim:
            assert all(u.preimage is None for u in witness.unlocks)
        net, fee = self._net_refund(tx, previous, chain["locks"])
        chain["net"] = [a + b for a, b in zip(chain["net"], net)]
        chain["fee"] += fee
        locked = [
            i
            for i, o in enumerate(tx["outputs"])
            if o["lock"]["code_hash"] == COMMIT_LOCK_CODE_HASH
        ]
        assert len(locked) <= 1
        remaining = sum(int(tx["outputs"][i]["capacity"], 16) for i in locked)
        assert sum(chain["net"]) + remaining == chain["funding"] - chain["fee"]
        if locked:
            chain["outpoint"] = {"tx_hash": tx["hash"], "index": hex(locked[0])}
        else:
            chain["done"] = True
        print(
            json.dumps(
                {
                    "chain_tx": tx["hash"],
                    "net": chain["net"],
                    "fee": chain["fee"],
                    "claimed": chain["claimed"],
                    "done": chain["done"],
                }
            ),
            flush=True,
        )
        return chain["done"]

    def _chain_until(self, chain, claim=True, claimed_only=False, timeout=1800):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._advance_chain(chain, claim=claim)
            if chain["claimed"] if claimed_only else chain["done"]:
                return
            self.node.getClient().generate_epochs("0x1", wait_time=0)
            time.sleep(3)
        raise AssertionError(f"准确消费链未完成: {chain}")

    def _final_funds(self, chain):
        self._chain_until(chain)
        assert chain["claimed"], "未见属于原付款的原像领取"
        edge = chain["edge"]
        expected = [
            int(row["local_balance"], 16)
            + DEFAULT_MIN_DEPOSIT_CKB
            + sign * edge["sent"]
            for row, sign in zip(edge["before"], (-1, 1))
        ]
        # 从各所有者的实际净到账分别归属费用；合计必须等于独立 inputs-outputs 手续费。
        costs = [expected[i] - chain["net"][i] for i in range(2)]
        assert all(0 <= cost <= chain["fee"] for cost in costs), (expected, chain)
        assert sum(costs) == chain["fee"] < CKB // 100
        for other in self.edges:
            if other is not edge:
                self._wait(
                    "开放原通道只交付一次并解锁",
                    lambda e=other: self._cleared(e),
                    RESULT_WAIT,
                )
        self._success(self.p)
        self.wait_invoice_state(self.r, self.hash, "Paid", timeout=120)
        print(
            json.dumps(
                {
                    "closed_channel_fee_by_owner": costs,
                    "closed_channel_expected": expected,
                }
            ),
            flush=True,
        )

    def _healthy_probe(self, sender, receiver, edge):
        before = [self._channel(f, edge["cid"]) for f in (sender, receiver)]
        result = sender.get_client().send_payment(
            {
                "target_pubkey": receiver.get_pubkey(),
                "amount": hex(CKB),
                "keysend": True,
                "max_fee_amount": "0x0",
            }
        )
        self.wait_payment_state(sender, result["payment_hash"], "Success", timeout=120)
        self._wait(
            "健康付款解锁且不释放原在途锁定",
            lambda: all(
                self._channel(f, edge["cid"])[k] == row[k]
                for f, row in zip((sender, receiver), before)
                for k in ("offered_tlc_balance", "received_tlc_balance")
            ),
        )
        for f, row, sign in zip((sender, receiver), before, (-1, 1)):
            assert (
                int(self._channel(f, edge["cid"])["local_balance"], 16)
                == int(row["local_balance"], 16) + sign * CKB
            )
