"""PR #1671：验证 xUDT 通道出资、签名、锁依赖和关闭后的资金返还。"""

import struct
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import toml
from parameterized import parameterized

from framework.basic_fiber import FiberTest
from framework.util import to_big_uint128_le_compatible, to_int_from_big_uint128_le

CKB = 100000000


class TestDualFundedUdt(FiberTest):
    # 本组测试使用单独的数据目录和端口，便于与其他本地开发链同时运行。
    tmp_path_name = "tmp/pr1671"
    ckb_rpc_port = 28114
    ckb_p2p_port = 28125
    fiber1_rpc_port = 28228
    fiber1_p2p_port = 28227
    fiber2_rpc_port = 28229
    fiber2_p2p_port = 28230

    # TEST-MAP: PR1671-01
    def test_partial_acceptor_funding_preserves_both_changes(self):
        """
        1. 为双方各准备一个 cell，每个持有 10000 单位 xUDT 和 142 CKB。
        2. 按默认费率分别出资 5000、3000，检查交易确认和双方通道就绪。
        3. 检查双方找零的资产、锁和数量。
        """
        # 1. 准备双方的代币 cell，核对数量和容量。
        self._prepare_asset()

        # 2. 双方分别出资，等待交易确认，并核对双方查询到的通道相同且都已就绪。
        self._open_udt_channel(5000, 3000)

        # 3. 发起方和接受方分别收到自己的找零。
        self._assert_funding_change((5000, 7000))

    # TEST-MAP: PR1671-02
    @parameterized.expand([(None,), (1000,)])
    def test_full_acceptor_funding_pays_for_both_signatures(self, fee_rate):
        """
        1. 为双方各准备一个代币 cell，确认 CKB 最低费率为 1000。
        2. 省略或显式设置 1000 费率，分别出资 5000、10000。
        3. 检查实际交易的两组签名及找零，不提高费率。
        """
        # 1. 准备代币，并核对实际节点的最低费率。
        self._prepare_asset()

        # 2. 分别省略费率或传入 1000，检查带双方签名的交易能否确认。
        self._open_udt_channel(5000, 10000, fee_rate=fee_rate)

        # 3. 接受方全额出资，不产生 UDT 找零。
        assert len(self.funding_input_groups) == 2
        assert self.funding_fee > 0
        self._assert_funding_change((5000, 0))

    # TEST-MAP: PR1671-03
    def test_full_multi_cell_funding_needs_no_plain_ckb_input(self):
        """
        1. 为双方各准备两个 cell，每个持有 5000 单位 xUDT 和 142 CKB。
        2. 默认费率各投入全部 10000，检查上链和双方就绪。
        3. 检查实际为四个 UDT 输入且没有普通 CKB 输入，锁依赖不重复。
        """
        # 1. 准备四个 UDT cell，每个占用最低存储容量，并逐个核对。
        self._prepare_asset(cells_per_account=2)

        # 2. 双方全额出资，不能依赖额外 CKB 输入补锁依赖。
        self._open_udt_channel(10000, 10000)

        # 3. 检查实际交易只使用这四个 UDT 输入。
        self._assert_udt_inputs((2, 2))
        assert len(self.funding_inputs) == 4
        assert all(cell["type"] == self.udt_script for cell in self.funding_inputs)
        self._assert_funding_change((0, 0))

    # TEST-MAP: PR1671-04
    def test_multi_cell_partial_funding_preserves_both_changes(self):
        """
        1. 为双方各准备两个 cell，每个持有 5000 单位 xUDT 和 142 CKB。
        2. 默认费率分别出资 6000、7000，等待上链和通道就绪。
        3. 核对双方实际各用两个 UDT 输入并各得正确找零。
        """
        # 1. 每个 cell 的代币都不足以单独支付本方出资额。
        self._prepare_asset(cells_per_account=2)

        # 2. 双方分别出资，等待注资交易确认。
        self._open_udt_channel(6000, 7000)

        # 3. 核对交易确实用了双方各两个 UDT 输入，并检查找零。
        self._assert_udt_inputs((2, 2))
        self._assert_funding_change((4000, 3000))

    # TEST-MAP: PR1671-05
    def test_mixed_udt_and_plain_ckb_inputs_keep_one_lock_dependency(self):
        """
        1. 为双方各准备两个 cell，每个持有 5000 单位 xUDT 和 142 CKB。
        2. 默认费率分别出资 5000、10000，等待交易确认。
        3. 检查三个 UDT 输入、额外普通 CKB 输入以及正确找零。
        """
        # 1. 双方准备与仅 UDT 输入用例相同的代币 cell。
        self._prepare_asset(cells_per_account=2)

        # 2. 发起方只需一个 UDT cell，容量不足时补普通 CKB。
        self._open_udt_channel(5000, 10000)

        # 3. 核对三个 UDT 输入和额外的普通 CKB 输入，并检查找零。
        self._assert_udt_inputs((1, 2))
        assert any(cell["type"] is None for cell in self.funding_inputs)
        self._assert_funding_change((0, 0))

    # TEST-MAP: PR1671-06
    def test_zero_acceptor_funding_completes_first_invoice_payment(self):
        """
        1. 准备双方资产并记录接受方原 UDT cell。
        2. 默认费率出资 5000、0，检查接受方资产未被使用。
        3. 支付 100 单位发票，核对状态、余额，并确认所有 TLC 已处理完成。
        """
        # 1. 接受方提前持有代币，便于检查零出资时是否误用了它的 cell。
        self._prepare_asset()
        acceptor_point = self.initial_cells[1][0]["out_point"]

        # 2. 零出资接受不能消费接受方原 UDT cell。
        self._open_udt_channel(5000, 0)
        self._assert_udt_inputs((1, 0))
        assert acceptor_point not in [
            i["previous_output"] for i in self.funding["inputs"]
        ]
        assert (
            self.node.getClient().get_live_cell(
                acceptor_point["index"], acceptor_point["tx_hash"], True
            )["status"]
            == "live"
        )

        # 3. 按同一资产发票完成首次支付。
        self._pay_invoice(self.fiber1, self.fiber2, 100)
        self._assert_channel_balances((4900, 100))
        assert self._asset_balance(1) == 10000

    # TEST-MAP: PR1671-07
    def test_both_funders_can_pay_invoices_with_exact_balances(self):
        """
        1. 默认费率建立出资 5000、3000 的 xUDT 通道。
        2. A 向 B 支付 100 单位，核对双方余额和支付终态。
        3. B 向 A 支付 70 单位，在正确节点查询并核对最终余额。
        """
        # 1. 按旧用例的部分出资流程开通通道。
        self._prepare_asset()
        self._open_udt_channel(5000, 3000)

        # 2. 完成 A 向 B 的付款，等待所有 TLC 处理完毕后核对余额。
        self._pay_invoice(self.fiber1, self.fiber2, 100)
        self._assert_channel_balances((4900, 3100))

        # 3. B 向 A 付款后，在 B 查询付款状态，并核对双方余额。
        self._pay_invoice(self.fiber2, self.fiber1, 70)
        self._assert_channel_balances((4970, 3030))

    # TEST-MAP: PR1671-08
    def test_cooperative_close_returns_paid_balances_and_all_change(self):
        """
        1. 建立 5000、3000 的 xUDT 通道并完成 100、70 的双向支付。
        2. 所有 TLC 处理完毕后协作关闭，等待双方 Closed 和关闭交易确认。
        3. 核对通道资金 cell 已被花费、代币已返还，CKB 总额只减少实际手续费。
        """
        # 1. 准备好代币后记录 CKB 余额，之后只进行本通道的开通、支付和关闭。
        self._prepare_asset()
        ckb_before = sum(self._ckb_balances())
        self._open_udt_channel(5000, 3000)
        self._pay_invoice(self.fiber1, self.fiber2, 100)
        self._assert_channel_balances((4900, 3100))
        self._pay_invoice(self.fiber2, self.fiber1, 70)
        self._assert_channel_balances((4970, 3030))

        # 2. 等待双方关闭及实际关闭交易确认，并检查通道资金 cell 已花费。
        closing, closing_fee = self._close_udt_channel()

        # 3. 汇总双方所有找零和返还的 cell，检查代币与 CKB 总额。
        assert [self._asset_balance(i) for i in range(2)] == [9970, 10030]
        assert [self._output_amount(closing, lock) for lock in self.locks] == [
            4970,
            3030,
        ]
        assert sum(self._ckb_balances()) == ckb_before - self.funding_fee - closing_fee

    # TEST-MAP: UDT-CLOSE-01
    def test_zero_acceptor_funding_returns_paid_balances_on_close(self):
        """
        1. 为双方各准备一个含 10000 单位 xUDT 的 cell，记录 CKB 总额。
        2. A 出资 5000、B 零出资接受，检查 B 原有 UDT cell 未花费。
        3. A 向 B 支付 100，检查支付成功及通道余额为 4900、100。
        4. 协作关闭后检查实际返还、最终钱包余额及 CKB 手续费，B 原 cell 仍未花费。
        """
        # 1. 准备独立资产，记录接受方原有 cell 和开通前 CKB 总额。
        self._prepare_asset()
        acceptor_point = self.initial_cells[1][0]["out_point"]
        ckb_before = sum(self._ckb_balances())

        # 2. 零出资接受不使用 B 的 UDT，A 得到 5000 找零。
        self._open_udt_channel(5000, 0)
        self._assert_udt_inputs((1, 0))
        self._assert_funding_change((5000, 0))
        assert acceptor_point not in [
            i["previous_output"] for i in self.funding["inputs"]
        ]
        assert (
            self.node.getClient().get_live_cell(
                acceptor_point["index"], acceptor_point["tx_hash"], True
            )["status"]
            == "live"
        )

        # 3. 等待付款成功、发票已付和 TLC 清空，再核对双方通道余额。
        self._pay_invoice(self.fiber1, self.fiber2, 100)
        self._assert_channel_balances((4900, 100))

        # 4. 在同一通道上关闭并核对返还，汇总找零和原有余额。
        closing, closing_fee = self._close_udt_channel()
        assert [self._output_amount(closing, lock) for lock in self.locks] == [
            4900,
            100,
        ]
        assert [self._asset_balance(i) for i in range(2)] == [9900, 10100]
        assert sum(self._ckb_balances()) == ckb_before - self.funding_fee - closing_fee
        assert (
            self.node.getClient().get_live_cell(
                acceptor_point["index"], acceptor_point["tx_hash"], True
            )["status"]
            == "live"
        )

    # TEST-MAP: UDT-CLOSE-02
    def test_full_multi_cell_acceptor_funding_can_pay_and_close(self):
        """
        1. 为 A 准备一个 10000 单位 cell，为 B 准备两个各 5000 单位 cell。
        2. A 出资 5000、B 投入全部 10000，核对实际输入和找零。
        3. B 向 A 支付 1000，检查支付成功及通道余额为 6000、9000。
        4. 协作关闭后检查返还和最终钱包余额为 11000、9000，CKB 只减少手续费。
        """
        # 1. A 持一个 UDT cell，B 持两个，每方代币总量均为 10000。
        self._prepare_asset(acceptor_cells=2)
        ckb_before = sum(self._ckb_balances())

        # 2. B 的出资必须实际使用两个 UDT 输入，且不能产生 UDT 找零。
        self._open_udt_channel(5000, 10000)
        self._assert_udt_inputs((1, 2))
        self._assert_funding_change((5000, 0))
        assert self._asset_balance(1) == 0

        # 3. 在 B 查询付款成功，在 A 查询发票已付，等待 TLC 清空后核对余额。
        self._pay_invoice(self.fiber2, self.fiber1, 1000)
        self._assert_channel_balances((6000, 9000))

        # 4. 关闭同一通道，逐方核对实际返还及包含 A 找零的最终钱包余额。
        closing, closing_fee = self._close_udt_channel()
        assert [self._output_amount(closing, lock) for lock in self.locks] == [
            6000,
            9000,
        ]
        assert [self._asset_balance(i) for i in range(2)] == [11000, 9000]
        assert sum(self._ckb_balances()) == ckb_before - self.funding_fee - closing_fee

    def _close_udt_channel(self):
        self.fiber1.get_client().shutdown_channel(
            {
                "channel_id": self.channel_id,
                "close_script": self.locks[0],
                "fee_rate": hex(1000),
            }
        )
        for fiber, peer in ((self.fiber1, self.fiber2), (self.fiber2, self.fiber1)):
            self.wait_for_channel_state(
                fiber.get_client(),
                peer.get_pubkey(),
                "Closed",
                timeout=180,
                include_closed=True,
                channel_id=self.channel_id,
            )
        for _ in range(180):
            entries = self.node.getClient().get_transactions(
                {
                    "script": self.funding_output["lock"],
                    "script_type": "lock",
                    "script_search_mode": "exact",
                    "group_by_transaction": False,
                },
                "asc",
                "0x64",
                None,
            )["objects"]
            spent = [e for e in entries if e["io_type"] == "input"]
            if spent:
                assert len(spent) == 1, spent
                closing_hash = spent[0]["tx_hash"]
                break
            time.sleep(1)
        else:
            assert False, "No confirmed transaction spent the channel funding cell"
        closing = self._committed_transaction(closing_hash)

        assert [i["previous_output"] for i in closing["inputs"]] == [self.funding_point]
        assert (
            self.node.getClient().get_live_cell(
                self.funding_point["index"], self.funding_point["tx_hash"], True
            )["status"]
            != "live"
        )
        closing_fee = self.get_tx_message(closing_hash)["fee"]
        assert closing_fee > 0
        return closing, closing_fee

    def _wallet_cells(self, lock):
        cells, cursor = [], None
        while True:
            page = self.node.getClient().get_cells(
                {"script": lock, "script_type": "lock", "script_search_mode": "exact"},
                "asc",
                "0x64",
                cursor,
            )
            cells.extend(page["objects"])
            if len(page["objects"]) < 100:
                return cells
            assert page["last_cursor"] != cursor, "Indexer cursor did not advance"
            cursor = page["last_cursor"]

    def _asset_cells(self, account_index):
        return [
            c
            for c in self._wallet_cells(self.locks[account_index])
            if c["output"]["type"] == self.udt_script
        ]

    def _asset_balance(self, account_index):
        return self.udtContract.balance(
            self.node.getClient(),
            self.issuer_lock["args"],
            self.locks[account_index]["args"],
        )

    def _ckb_balances(self):
        return [
            sum(int(c["output"]["capacity"], 16) for c in self._wallet_cells(lock))
            for lock in self.locks
        ]

    def _committed_transaction(self, tx_hash):
        for _ in range(180):
            response = self.node.getClient().get_transaction(tx_hash)
            status = response["tx_status"]["status"]
            assert status != "rejected", response
            if status == "committed":
                block_number = int(response["tx_status"]["block_number"], 16)
                break
            time.sleep(1)
        else:
            assert False, f"Transaction did not commit: {tx_hash}: {response}"
        for _ in range(180):
            tip = self.node.getClient().get_indexer_tip()
            if tip and int(tip["block_number"], 16) >= block_number:
                return response["transaction"]
            time.sleep(1)
        assert False, f"Indexer did not reach transaction block {block_number}: {tip}"

    def _prepare_asset(self, cells_per_account=1, acceptor_cells=None):
        cell_counts = (
            cells_per_account,
            cells_per_account if acceptor_cells is None else acceptor_cells,
        )
        assert toml.load(self.node.ckb_toml_path)["tx_pool"]["min_fee_rate"] == 1000
        assert int(self.node.getClient().tx_pool_info()["min_fee_rate"], 16) == 1000
        self.logger.info(
            "FNN under test: %s", self.fiber1.get_client().node_info()["version"]
        )
        issuer = self.generate_account(10000)
        self.issuer_lock = self.get_account_script(issuer)
        self.udt_script = self.get_account_udt_script(issuer)
        self.locks = [
            self.get_account_script(f.account_private)
            for f in (self.fiber1, self.fiber2)
        ]
        assert [self._asset_balance(i) for i in range(2)] == [0, 0]
        # 使用现有 CLI 发行代币，直接创建每个携带 142 CKB 的 cell。
        issuer_cell = next(
            c
            for c in self._wallet_cells(self.issuer_lock)
            if c["output"]["type"] is None
            and int(c["output"]["capacity"], 16) >= 1000 * CKB
        )
        capacity = int(issuer_cell["output"]["capacity"], 16)
        with TemporaryDirectory(prefix="fiber-pr1671-") as directory:
            tx_file = str(Path(directory) / "issue.json")
            self.Ckb_cli.tx_init(tx_file, self.node.rpcUrl)
            point = issuer_cell["out_point"]
            self.Ckb_cli.tx_add_input(
                point["tx_hash"], int(point["index"], 16), tx_file, self.node.rpcUrl
            )
            self.Ckb_cli.tx_add_cell_dep(
                self.udtContract.contract_hash,
                hex(self.udtContract.contract_tx_index),
                tx_file,
            )
            for lock, count in zip(self.locks, cell_counts):
                for _ in range(count):
                    self.Ckb_cli.tx_add_output(
                        {
                            "capacity": hex(142 * CKB),
                            "lock": lock,
                            "type": self.udt_script,
                        },
                        to_big_uint128_le_compatible(10000 // count),
                        tx_file,
                    )
            self.Ckb_cli.tx_add_output(
                {
                    "capacity": hex(capacity - sum(cell_counts) * 142 * CKB - 10000),
                    "lock": self.issuer_lock,
                    "type": None,
                },
                "0x",
                tx_file,
            )
            for signature in self.Ckb_cli.tx_sign_inputs(
                issuer, tx_file, self.node.rpcUrl
            ):
                self.Ckb_cli.tx_add_signature(
                    signature["lock-arg"],
                    signature["signature"],
                    tx_file,
                    self.node.rpcUrl,
                )
            tx_hash = self.Ckb_cli.tx_send(tx_file, self.node.rpcUrl).strip()
        prepared = self._committed_transaction(tx_hash)
        secp_deps = [d for d in prepared["cell_deps"] if d["dep_type"] == "dep_group"]
        assert len(secp_deps) == 1
        self.secp_dep = secp_deps[0]
        self.initial_cells = [self._asset_cells(i) for i in range(2)]
        for cells, count in zip(self.initial_cells, cell_counts):
            assert len(cells) == count
            assert all(int(c["output"]["capacity"], 16) == 142 * CKB for c in cells)
            assert all(
                to_int_from_big_uint128_le(c["output_data"]) == 10000 // count
                for c in cells
            )
        assert [self._asset_balance(i) for i in range(2)] == [10000, 10000]

    def _assert_channel_balances(self, balances):
        for _ in range(120):
            pair = []
            for fiber in (self.fiber1, self.fiber2):
                channels = fiber.get_client().list_channels({})["channels"]
                pair.append(
                    next(c for c in channels if c["channel_id"] == self.channel_id)
                )
            assert all(c["state"]["state_name"] == "ChannelReady" for c in pair), pair
            if all(not c["pending_tlcs"] for c in pair):
                assert [int(c["local_balance"], 16) for c in pair] == list(
                    balances
                ), pair
                assert [int(c["remote_balance"], 16) for c in pair] == list(
                    reversed(balances)
                ), pair
                assert all(
                    c["funding_udt_type_script"] == self.udt_script for c in pair
                )
                return pair
            time.sleep(1)
        assert False, f"TLCs did not settle: {pair}"

    def _open_udt_channel(self, amount_a, amount_b, fee_rate=None):
        request = {
            "pubkey": self.fiber2.get_pubkey(),
            "funding_amount": hex(amount_a),
            "funding_udt_type_script": self.udt_script,
            "public": True,
        }
        if fee_rate is not None:
            request["funding_fee_rate"] = hex(fee_rate)
        temporary_id = self.fiber1.get_client().open_channel(request)[
            "temporary_channel_id"
        ]
        for _ in range(120):
            pending = self.fiber2.get_client().list_channels({"only_pending": True})[
                "channels"
            ]
            matches = [c for c in pending if c["channel_id"] == temporary_id]
            if matches:
                assert len(matches) == 1
                channel = matches[0]
                assert channel["is_acceptor"] is True
                assert channel["state"]["state_name"] == "NegotiatingFunding"
                assert channel["funding_udt_type_script"] == self.udt_script
                assert int(channel["remote_balance"], 16) == amount_a
                assert channel["channel_outpoint"] is None
                break
            time.sleep(1)
        else:
            assert (
                False
            ), f"Acceptor did not expose pending channel {temporary_id}: {pending}"
        self.channel_id = self.fiber2.get_client().accept_channel(
            {"temporary_channel_id": temporary_id, "funding_amount": hex(amount_b)}
        )["channel_id"]
        for fiber, peer in ((self.fiber1, self.fiber2), (self.fiber2, self.fiber1)):
            self.wait_for_channel_state(
                fiber.get_client(),
                peer.get_pubkey(),
                "ChannelReady",
                channel_id=self.channel_id,
            )
        pair = self._assert_channel_balances((amount_a, amount_b))
        assert pair[0]["channel_outpoint"] == pair[1]["channel_outpoint"]
        point = bytes.fromhex(pair[0]["channel_outpoint"][2:])
        assert len(point) == 36
        self.funding_point = {
            "tx_hash": "0x" + point[:32].hex(),
            "index": hex(int.from_bytes(point[32:], "little")),
        }
        self.funding = self._committed_transaction(self.funding_point["tx_hash"])
        self.funding_output = self.funding["outputs"][
            int(self.funding_point["index"], 16)
        ]
        assert self.funding_output["type"] == self.udt_script
        assert (
            to_int_from_big_uint128_le(
                self.funding["outputs_data"][int(self.funding_point["index"], 16)]
            )
            == amount_a + amount_b
        )
        self.funding_inputs, self.funding_input_groups = [], []
        input_units = 0
        for index, tx_input in enumerate(self.funding["inputs"]):
            previous = tx_input["previous_output"]
            parent = self.node.getClient().get_transaction(previous["tx_hash"])[
                "transaction"
            ]
            cell = parent["outputs"][int(previous["index"], 16)]
            self.funding_inputs.append(cell)
            if cell["type"] is not None:
                assert cell["type"] == self.udt_script
                input_units += to_int_from_big_uint128_le(
                    parent["outputs_data"][int(previous["index"], 16)]
                )
            if cell["lock"] not in self.funding_input_groups:
                self.funding_input_groups.append(cell["lock"])
                # 按 Molecule 格式解析 WitnessArgs.lock，检查其中的签名。
                witness = bytes.fromhex(self.funding["witnesses"][index][2:])
                size, start, end, output_type = struct.unpack_from("<IIII", witness)
                assert (
                    size == len(witness) and start == 16 and end <= output_type <= size
                )
                assert (
                    end - start == 69
                    and struct.unpack_from("<I", witness, start)[0] == 65
                )
                assert any(witness[start + 4 : end]), "Empty lock-group signature"
        assert self.funding_input_groups == self.locks
        deps = self.funding["cell_deps"]
        assert deps.count(self.secp_dep) == 1, deps
        assert len(deps) == len(
            {
                (d["dep_type"], d["out_point"]["tx_hash"], d["out_point"]["index"])
                for d in deps
            }
        )
        assert len(self.funding["outputs"]) == len(self.funding["outputs_data"])
        output_units = sum(
            to_int_from_big_uint128_le(data)
            for cell, data in zip(self.funding["outputs"], self.funding["outputs_data"])
            if cell["type"] == self.udt_script
        )
        assert input_units == output_units
        assert [self._asset_balance(i) for i in range(2)] == [
            10000 - amount_a,
            10000 - amount_b,
        ]
        self.funding_fee = self.get_tx_message(self.funding_point["tx_hash"])["fee"]
        assert self.funding_fee > 0
        self.logger.info(
            "Funding %s: UDT input counts=%s, total inputs=%s, fee=%s",
            self.funding_point["tx_hash"],
            [
                sum(
                    c["type"] == self.udt_script and c["lock"] == lock
                    for c in self.funding_inputs
                )
                for lock in self.locks
            ],
            len(self.funding_inputs),
            self.funding_fee,
        )

    def _assert_udt_inputs(self, counts):
        assert [
            sum(
                c["type"] == self.udt_script and c["lock"] == lock
                for c in self.funding_inputs
            )
            for lock in self.locks
        ] == list(counts)

    def _output_amount(self, transaction, lock):
        return sum(
            to_int_from_big_uint128_le(data)
            for cell, data in zip(transaction["outputs"], transaction["outputs_data"])
            if cell["type"] == self.udt_script and cell["lock"] == lock
        )

    def _assert_funding_change(self, amounts):
        assert [self._output_amount(self.funding, lock) for lock in self.locks] == list(
            amounts
        )

    def _pay_invoice(self, sender, recipient, amount):
        invoice = recipient.get_client().new_invoice(
            {
                "amount": hex(amount),
                "currency": "Fibd",
                "udt_type_script": self.udt_script,
                "payment_preimage": self.generate_random_preimage(),
                "hash_algorithm": "sha256",
            }
        )
        payment = sender.get_client().send_payment(
            {"invoice": invoice["invoice_address"]}
        )
        assert payment["payment_hash"] == invoice["invoice"]["data"]["payment_hash"]
        self.wait_payment_state(sender, payment["payment_hash"], "Success")
        self.wait_invoice_state(recipient, payment["payment_hash"], "Paid")
