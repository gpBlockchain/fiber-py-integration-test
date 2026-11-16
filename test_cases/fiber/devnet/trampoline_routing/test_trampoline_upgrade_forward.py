"""0.9.1 升级当前版本后，trampoline 持单仍正确结算（PR #1705）。"""

import os
from pathlib import Path
import shutil
import socket
import subprocess
import time

from framework.basic_fiber import FiberTest
from framework.test_fiber import FiberConfigPath
from framework.util import ckb_hash, get_project_root
from test_cases.fiber.devnet.migration._helpers import start_with_confirm

ROOT = Path(get_project_root())
CKB = 100_000_000
AMOUNT = CKB


class TestTrampolineUpgradeCKB(FiberTest):
    asset = "ckb"
    fiber_version = FiberConfigPath.V091_DEV
    tmp_path_name = "tmp-pr1705-ckb"
    ckb_rpc_port, ckb_p2p_port = 27114, 27115
    fiber1_rpc_port, fiber1_p2p_port = 27128, 27127
    fiber2_rpc_port, fiber2_p2p_port = 27129, 27130
    extra_fiber_rpc_port, extra_fiber_p2p_port = 27151, 27152

    @classmethod
    def setup_class(cls):
        for port in (27114, 27115, 27128, 27127, 27129, 27130, 27151, 27152):
            with socket.socket() as sock:
                # 连续资产变体允许上一轮 TCP TIME_WAIT；实际监听仍触发占用错误。
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("127.0.0.1", port))
        assert not (ROOT / cls.tmp_path_name).exists(), "保留已存在的测试目录，请先检查"
        for build in (cls.fiber_version, FiberConfigPath.CURRENT_DEV):
            binary = ROOT / build.fiber_bin_path
            assert binary.is_file(), f"缺少仓库配置的节点二进制: {binary}"
            version = subprocess.check_output([str(binary), "--version"], text=True)
            print(f"{build.name}: {version.strip()}")
        super().setup_class()

    def setup_method(self, method):
        super().setup_method(method)
        for fiber in self.fibers:
            fiber.get_client().try_count = 1
            fiber.get_client().timeout = 10

    @classmethod
    def teardown_class(cls):
        super().teardown_class()
        shutil.rmtree(ROOT / cls.tmp_path_name, ignore_errors=True)

    # TEST-MAP: PR1705-01
    def test_fulfill_held_trampoline_payment_after_upgrade(self):
        """
        1. 0.9.1 节点产生转发历史和持单，T 创建同哈希发票，再保留原目录升级 T。
        2. C 结算，核对发票归属、原像、逐跳资金、TLC 释放和 UDT 的链上容量。
        3. 同一路径再次成功付款，T 的同哈希本地发票仍为 Open。
        """
        # 1. 复用旧版转发历史、持单及升级步骤，确认原通道和持单已恢复。
        before, preimage, payment_hash, tlcs, funding_before = self._prepare_upgrade()

        # 2. 先核对 H 的独立余额方程，再发送其他付款，避免混入后续资金。
        self.fiber3.get_client().settle_invoice(
            {"payment_hash": payment_hash, "payment_preimage": preimage}
        )
        self.wait_payment_state(self.fiber1, payment_hash, "Success", timeout=120)
        payment = self.fiber1.get_client().get_payment({"payment_hash": payment_hash})
        assert payment["payment_preimage"] == preimage
        self.wait_invoice_state(self.fiber3, payment_hash, "Paid", timeout=120)
        assert self._invoice_status(self.fiber2, payment_hash) == "Open"
        self._wait_clear()
        incoming, outgoing = int(tlcs[0]["amount"], 16), int(tlcs[2]["amount"], 16)
        expected = [
            before[0] - incoming,
            before[1] + incoming,
            before[2] - outgoing,
            before[3] + outgoing,
        ]
        channels = self._wait(
            "H 逐跳结算余额",
            self._channels,
            lambda rows: [int(c["local_balance"], 16) for c in rows] == expected,
        )
        for channel, tlc in zip(channels, tlcs):
            assert channel["state"]["state_name"] == "ChannelReady"
            assert not any(
                t["payment_hash"] == payment_hash and t["id"] == tlc["id"]
                for t in channel["pending_tlcs"]
            )
        for index, channel in enumerate(channels):
            assert int(channel["remote_balance"], 16) == expected[index ^ 1]
        funding_after = self._funding_cells() if self.udt else None
        assert funding_after == funding_before

        # 3. 同一原通道的新 hash 付款成功，T 的同哈希本地发票仍为 Open。
        next_preimage, next_hash, _ = self._start_hold()
        self.fiber3.get_client().settle_invoice(
            {"payment_hash": next_hash, "payment_preimage": next_preimage}
        )
        self.wait_payment_state(self.fiber1, next_hash, "Success", timeout=120)
        self.wait_invoice_state(self.fiber3, next_hash, "Paid", timeout=120)
        self._wait_clear()
        assert self._invoice_status(self.fiber2, payment_hash) == "Open"
        print(
            f"PR1705-01 {self.asset}: H settled once; local invoice Open; probe Success"
        )

    # TEST-MAP: PR1705-02
    def test_cancel_held_trampoline_payment_after_upgrade(self):
        """
        1. 0.9.1 节点产生转发历史和持单，T 创建同哈希发票，再保留原目录升级 T。
        2. C 取消，A 最终失败且无原像，T 发票仍 Open；两跳余额不变、在途资金释放。
        3. 同一路径再次成功付款，原 C 发票仍 Cancelled，T 发票仍 Open。
        """
        # 1. 与成功结算共用前置条件，只升级 T，A/C 仍使用 0.9.1。
        before, _, payment_hash, _, funding_before = self._prepare_upgrade()

        # 2. 直接取消持单；等待真实失败及所有尝试收尾，再核对原余额。
        self.fiber3.get_client().cancel_invoice({"payment_hash": payment_hash})
        self.wait_invoice_state(self.fiber3, payment_hash, "Cancelled", timeout=120)
        self.wait_payment_state(self.fiber1, payment_hash, "Failed", timeout=120)
        payment = self.fiber1.get_client().get_payment({"payment_hash": payment_hash})
        assert payment.get("payment_preimage") is None, payment
        assert self._invoice_status(self.fiber2, payment_hash) == "Open"
        self._wait_clear()
        channels = self._wait(
            "取消付款的全部 TLC 已确认移除",
            self._channels,
            lambda rows: all(
                tlc["status"] == {end[2]: "RemoveAckConfirmed"}
                for channel, end in zip(rows, self.ends)
                for tlc in channel["pending_tlcs"]
                if tlc["payment_hash"] == payment_hash
            ),
        )
        # 已移除的失败 TLC 可保留为历史行；四侧金额和资金占用共同证明退款。
        for index, channel in enumerate(channels):
            assert channel["state"]["state_name"] == "ChannelReady"
            assert int(channel["local_balance"], 16) == before[index]
            assert int(channel["remote_balance"], 16) == before[index ^ 1]
            assert int(channel["offered_tlc_balance"], 16) == 0
            assert int(channel["received_tlc_balance"], 16) == 0
        funding_after = self._funding_cells() if self.udt else None
        assert funding_after == funding_before

        # 3. 新 hash 的付款仍可成功，原取消发票与 T 的本地发票均保持原状态。
        next_preimage, next_hash, _ = self._start_hold()
        self.fiber3.get_client().settle_invoice(
            {"payment_hash": next_hash, "payment_preimage": next_preimage}
        )
        self.wait_payment_state(self.fiber1, next_hash, "Success", timeout=120)
        self.wait_invoice_state(self.fiber3, next_hash, "Paid", timeout=120)
        self._wait_clear()
        assert self._invoice_status(self.fiber3, payment_hash) == "Cancelled"
        assert self._invoice_status(self.fiber2, payment_hash) == "Open"
        print(
            f"PR1705-02 {self.asset}: H Failed; balances unchanged; local invoice Open; probe Success"
        )

    def _prepare_upgrade(self):
        """建立旧版转发历史及在途付款，升级 T 后确认原通道和持单已恢复。"""
        # 1. 仅通过公开接口产生旧版转发历史，两个资产变体共用相同步骤。
        self.fiber3 = self.start_new_fiber(
            self.generate_account(10000), fiber_version=self.fiber_version
        )
        self.fiber3.get_client().try_count = 1
        self.fiber3.get_client().timeout = 10
        self.udt = None
        if self.asset == "udt":
            for fiber in (self.fiber1, self.fiber2):
                self.faucet(fiber.account_private, 0, self.fiber1.account_private)
            self.udt = self.get_account_udt_script(self.fiber1.account_private)
        channel_at = self.open_channel(
            self.fiber1, self.fiber2, 1000 * CKB, 0, udt=self.udt
        )
        channel_tc = self.open_channel(
            self.fiber2,
            self.fiber3,
            1000 * CKB,
            0,
            udt=self.udt,
            other_config={"public": False},
        )
        self.ends = [
            (self.fiber1, channel_at, "Outbound"),
            (self.fiber2, channel_at, "Inbound"),
            (self.fiber2, channel_tc, "Outbound"),
            (self.fiber3, channel_tc, "Inbound"),
        ]
        self.wait_graph_channels_sync(self.fiber1, 1, timeout=120)
        trampoline_pubkey = self.fiber2.get_pubkey()
        self._wait(
            "A 已获知 T 的 trampoline 能力",
            lambda: self.fiber1.get_client().graph_nodes({})["nodes"],
            lambda nodes: any(
                node["pubkey"] == trampoline_pubkey
                and "TRAMPOLINE_ROUTING_REQUIRED" in node["features"]
                for node in nodes
            ),
        )
        for outcome in ("Success", "Failed", "Success"):
            preimage, payment_hash, _ = self._start_hold()
            if outcome == "Success":
                self.fiber3.get_client().settle_invoice(
                    {"payment_hash": payment_hash, "payment_preimage": preimage}
                )
            else:
                self.fiber3.get_client().cancel_invoice({"payment_hash": payment_hash})
            self.wait_payment_state(self.fiber1, payment_hash, outcome, timeout=120)
            self.wait_invoice_state(
                self.fiber3,
                payment_hash,
                "Paid" if outcome == "Success" else "Cancelled",
                timeout=120,
            )
            self._wait_clear()

        # 2. 余额基线先于 H；四侧已承诺之后创建 T 的同哈希 hold 发票。
        before = [int(channel["local_balance"], 16) for channel in self._channels()]
        funding_before = self._funding_cells() if self.udt else None
        preimage, payment_hash, tlcs = self._start_hold()
        local = self.fiber2.get_client().new_invoice(self._invoice_params(payment_hash))
        assert local["invoice"]["data"]["payment_hash"] == payment_hash
        assert self._invoice_status(self.fiber2, payment_hash) == "Open"
        assert self._invoice_status(self.fiber3, payment_hash) == "Received"
        incoming, outgoing = int(tlcs[0]["amount"], 16), int(tlcs[2]["amount"], 16)
        assert int(tlcs[1]["amount"], 16) == incoming
        assert int(tlcs[3]["amount"], 16) == outgoing == AMOUNT
        assert 0 <= incoming - outgoing <= AMOUNT // 100
        # release 默认一 epoch：提前量为 4h×2/3，维护间隔为 60s。
        expiry_margin_ms = 4 * 60 * 60 * 1000 * 2 // 3 + 60_000
        for tlc in tlcs:
            assert (
                int(tlc["expiry"], 16) - int(time.time() * 1000) - expiry_margin_ms
                >= 600_000
            )

        # 3. T 的旧数据保持原路径；停机后只更换该实例的二进制配置。
        old_pid = int(
            subprocess.check_output(
                ["lsof", "-t", f"-iTCP:{self.fiber2.rpc_port}", "-sTCP:LISTEN"],
                text=True,
            ).strip()
        )
        self.fiber2.stop()

        def process_alive():
            try:
                os.kill(old_pid, 0)
            except ProcessLookupError:
                return False
            return True

        self._wait("旧 T 进程退出", process_alive, lambda alive: not alive)
        self.fiber2.fiber_config_enum = FiberConfigPath.CURRENT_DEV
        start_with_confirm(self.fiber2, confirm="y", timeout=120)
        self.fiber2.connect_peer(self.fiber1)
        self.fiber2.connect_peer(self.fiber3)
        self._wait(
            "升级后恢复原通道",
            self._channels,
            lambda channels: all(
                c["state"]["state_name"] == "ChannelReady" for c in channels
            ),
        )
        assert self._invoice_status(self.fiber3, payment_hash) == "Received"
        assert self._invoice_status(self.fiber2, payment_hash) == "Open"
        restored = self._wait_committed(payment_hash)
        assert [t["id"] for t in restored] == [t["id"] for t in tlcs]
        assert [int(c["local_balance"], 16) for c in self._channels()] == before
        return before, preimage, payment_hash, tlcs, funding_before

    def _channels(self):
        result = []
        for fiber, channel_id, _ in self.ends:
            channels = fiber.get_client().list_channels({"include_closed": True})[
                "channels"
            ]
            result.append(next(c for c in channels if c["channel_id"] == channel_id))
        return result

    def _wait(self, label, read, predicate):
        deadline = time.monotonic() + 120
        last = None
        while time.monotonic() < deadline:
            last = read()
            if predicate(last):
                return last
            time.sleep(0.5)
        raise AssertionError(f"{label} 120 秒内未收敛: {last}")

    def _wait_clear(self):
        return self._wait(
            "四侧在途资金释放",
            self._channels,
            lambda rows: all(
                int(c[field], 16) == 0
                for c in rows
                for field in ("offered_tlc_balance", "received_tlc_balance")
            ),
        )

    def _wait_committed(self, payment_hash):
        def read():
            return [
                [t for t in c["pending_tlcs"] if t["payment_hash"] == payment_hash]
                for c in self._channels()
            ]

        rows = self._wait(
            "四侧同一付款已承诺",
            read,
            lambda rows: all(
                len(ts) == 1 and ts[0]["status"] == {end[2]: "Committed"}
                for ts, end in zip(rows, self.ends)
            ),
        )
        return [ts[0] for ts in rows]

    def _invoice_params(self, payment_hash):
        return {
            "amount": hex(AMOUNT),
            "currency": "Fibd",
            "payment_hash": payment_hash,
            "hash_algorithm": "ckb_hash",
            "expiry": hex(3600),
            "final_expiry_delta": hex(86_400_000),
            "allow_mpp": False,
            "allow_trampoline_routing": True,
            "udt_type_script": self.udt,
        }

    def _start_hold(self):
        preimage = self.generate_random_preimage()
        payment_hash = ckb_hash(preimage)
        invoice = self.fiber3.get_client().new_invoice(
            self._invoice_params(payment_hash)
        )
        payment = self.fiber1.get_client().send_payment(
            {
                "invoice": invoice["invoice_address"],
                "trampoline_hops": [self.fiber2.get_pubkey()],
                "max_parts": hex(1),
                "max_fee_amount": hex(AMOUNT // 100),
                "tlc_expiry_limit": hex(172_800_000),
                "timeout": hex(600),
            }
        )
        assert payment["payment_hash"] == payment_hash
        self.wait_invoice_state(self.fiber3, payment_hash, "Received", timeout=120)
        return preimage, payment_hash, self._wait_committed(payment_hash)

    @staticmethod
    def _invoice_status(fiber, payment_hash):
        return fiber.get_client().get_invoice({"payment_hash": payment_hash})["status"]

    def _funding_cells(self):
        cells = []
        for channel in self._channels()[::2]:
            outpoint = channel["channel_outpoint"]
            raw = bytes.fromhex(outpoint[2:])
            assert len(raw) == 36, outpoint
            tx_hash = "0x" + raw[:32].hex()
            index = hex(int.from_bytes(raw[32:], "little"))
            cell = self.node.getClient().get_live_cell(index, tx_hash)
            assert cell["status"] == "live", cell
            cells.append({"outpoint": outpoint, "cell": cell["cell"]})
        return cells


class TestTrampolineUpgradeUDT(TestTrampolineUpgradeCKB):
    """两个升级回归用例的 UDT 变体，继承步骤和逐跳资金断言。"""

    asset = "udt"
    tmp_path_name = "tmp-pr1705-udt"
