"""CKB 链上通道结算观察：精确消费关系、领取原像与逐所有者净额。"""

import time

from framework.helper.fiber_ops import get_channel, wait_for_result
from framework.helper.settlement_witness import SettlementWitness


class ChannelSettlement:
    """通过公开 RPC 检查原通道和原 TLC，不管理节点或重新发起付款。"""

    def __init__(self, ckb_client, commitment_lock_code_hash):
        self.ckb = ckb_client
        self.commitment_lock_code_hash = commitment_lock_code_hash

    def get_output(self, outpoint):
        return self.ckb.get_transaction(outpoint["tx_hash"])["transaction"]["outputs"][
            int(outpoint["index"], 16)
        ]

    def find_spending_transaction(self, outpoint):
        """同 lock 的交易只是候选；必须实际消费给定 outpoint 且已确认。"""
        ckb = self.ckb
        cursor = None
        while True:
            page = ckb.get_transactions(
                {
                    "script": self.get_output(outpoint)["lock"],
                    "script_type": "lock",
                    "script_search_mode": "exact",
                },
                "asc",
                "0x64",
                cursor,
            )
            for item in page["objects"]:
                if item["io_type"] != "input":
                    continue
                result = ckb.get_transaction(item["tx_hash"])
                if result["tx_status"]["status"] == "committed" and any(
                    i["previous_output"] == outpoint
                    for i in result["transaction"]["inputs"]
                ):
                    return result["transaction"]
            if len(page["objects"]) < 100:
                return None
            cursor = page["last_cursor"]

    def net_amounts(self, tx, previous, locks):
        """按所有者扣除额外手续费输入，分别返回净到账与整笔交易手续费。"""
        inputs = [self.get_output(i["previous_output"]) for i in tx["inputs"]]
        fee = sum(int(c["capacity"], 16) for c in inputs) - sum(
            int(c["capacity"], 16) for c in tx["outputs"]
        )
        assert fee >= 0, tx
        net = [0 for _ in locks]
        for sign, cells in (
            (1, tx["outputs"]),
            (
                -1,
                [
                    c
                    for i, c in zip(tx["inputs"], inputs)
                    if i["previous_output"] != previous
                ],
            ),
        ):
            for cell in cells:
                assert cell["type"] is None, cell
                if (
                    sign == 1
                    and cell["lock"]["code_hash"] == self.commitment_lock_code_hash
                ):
                    continue
                assert cell["lock"] in locks, cell
                net[locks.index(cell["lock"])] += sign * int(cell["capacity"], 16)
        return net, fee

    def track_commitment(self, funding_outpoint, owner_locks):
        """从 funding 的已确认强关交易开始跟踪，不把强关本身当作领取。"""
        funding = self.get_output(funding_outpoint)
        tx = wait_for_result(
            "指定 funding 强关交易确认",
            lambda: self.find_spending_transaction(funding_outpoint),
        )
        locked = [
            i
            for i, o in enumerate(tx["outputs"])
            if o["lock"]["code_hash"] == self.commitment_lock_code_hash
        ]
        assert len(locked) == 1, tx
        net, fee = self.net_amounts(tx, funding_outpoint, owner_locks)
        # 编码来自公开 commitment 的版本字节，而不是尝试解析直到成功。
        args = bytes.fromhex(tx["outputs"][locked[0]]["lock"]["args"][2:])
        assert len(args) in (57, 58)
        version = "v1" if len(args) == 58 and args[-1] == 1 else "legacy"
        chain = dict(
            locks=owner_locks,
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

    def advance(self, chain, payment_hash, amount, preimage, *, allow_claim=True):
        """前进一个已确认消费，核对原 TLC、原像、收款归属和资金守恒。"""
        if chain["done"]:
            return True
        previous = chain["outpoint"]
        tx = self.find_spending_transaction(previous)
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
        if not chain["seen"]:
            witness.assert_pending_tlcs([(payment_hash, amount)])
            chain["seen"] = True
        for unlock in witness.unlocks:
            if unlock.preimage is not None:
                assert allow_claim and unlock.preimage == bytes.fromhex(preimage[2:])
                assert unlock.unlock_type < len(witness.tlcs)
                tlc = witness.tlcs[unlock.unlock_type]
                expected_hash = bytes.fromhex(payment_hash[2:])[: len(tlc.payment_hash)]
                assert (tlc.payment_hash, tlc.amount) == (expected_hash, amount)
                chain["claimed"] = True
        if not allow_claim:
            assert all(u.preimage is None for u in witness.unlocks)
        net, fee = self.net_amounts(tx, previous, chain["locks"])
        chain["net"] = [a + b for a, b in zip(chain["net"], net)]
        chain["fee"] += fee
        locked = [
            i
            for i, o in enumerate(tx["outputs"])
            if o["lock"]["code_hash"] == self.commitment_lock_code_hash
        ]
        assert len(locked) <= 1
        remaining = sum(int(tx["outputs"][i]["capacity"], 16) for i in locked)
        assert sum(chain["net"]) + remaining == chain["funding"] - chain["fee"]
        if locked:
            chain["outpoint"] = {"tx_hash": tx["hash"], "index": hex(locked[0])}
        else:
            chain["done"] = True
        return chain["done"]

    def wait(
        self, chain, payment_hash, amount, preimage, *, until_claim=False, timeout=1800
    ):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.advance(chain, payment_hash, amount, preimage)
            reached_claim = until_claim and chain["claimed"]
            fully_settled = not until_claim and chain["done"]
            if reached_claim or fully_settled:
                return
            self.ckb.generate_epochs("0x1", wait_time=0)
            time.sleep(3)
        raise AssertionError(f"准确消费链未完成: {chain}")

    def record_committed_tlcs(self, edge, payment_hash):
        def read():
            rows = [
                [
                    t
                    for t in get_channel(f, edge["channel_id"])["pending_tlcs"]
                    if t["payment_hash"] == payment_hash
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

        edge["tlcs"] = wait_for_result("逐跳两端 Committed", read)
        edge["sent"] = sum(int(t["amount"], 16) for t in edge["tlcs"])

    def assert_channel_open(self, edge, offline=()):
        assert (
            self.find_spending_transaction(edge["funding"]) is None
        ), f"原通道 funding 已消费: {edge['channel_id']}"
        rows = []
        for fiber in (edge["left"], edge["right"]):
            if fiber in offline:
                continue
            row = get_channel(fiber, edge["channel_id"])
            assert row["state"]["state_name"] == "ChannelReady", row
            rows.append(row)
        return rows

    def assert_payment_locked(self, edge, payment_hash, offline=()):
        self.assert_channel_open(edge, offline)
        for index, fiber in enumerate((edge["left"], edge["right"])):
            if fiber in offline:
                continue
            row = get_channel(fiber, edge["channel_id"])
            tlcs = [t for t in row["pending_tlcs"] if t["payment_hash"] == payment_hash]
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

    def payment_is_settled(self, edge, payment_hash, success=True):
        rows = self.assert_channel_open(edge)
        if any(
            int(row[key], 16)
            for row in rows
            for key in ("offered_tlc_balance", "received_tlc_balance")
        ):
            return False
        for i, row in enumerate(rows):
            for t in row["pending_tlcs"]:
                if t["payment_hash"] == payment_hash:
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
