"""多版本 settlement witness / commitment lock 布局的解析、调整和断言（witness 不是 Molecule WitnessArgs）。

    witness = SettlementWitness.from_hex(tx["witnesses"][0], version="legacy")
    witness.assert_single_tlc_claim([(payment_hash, amount)], preimage)
    witness.tlcs[0].amount += 1
    tx["witnesses"][0] = witness.to_hex()

字段中的 hash/signature/preimage 使用 bytes，金额使用 int；计数自动随列表更新。
保留原始 16 字节前缀及签名；修改后不自动重签，也不模拟合约的完整验证。
version 显式选 "legacy"（85 字节 TLC / 20 字节 hash）或 "v1"（97 / 32）。
两版 witness 本身没有版本标签；从通道/承诺的版本传入，不按长度猜测。

承诺锁 args 布局（版本相关数字集中在 COMMITMENT_LOCK_LAYOUTS，合约升级 v2/v3 时
只需在该表登记新布局；未登记的版本会直接报错，而不是悄悄按 Legacy 计算）：

    args[0:20]   聚合公钥哈希 blake160(x_only_aggregated_pubkey)
    args[20:28]  延迟解锁 epoch（小端，固定值 COMMITMENT_DELAY_EPOCH）
    args[28:36]  承诺号（大端）
    args[36:56]  settlement 数据哈希 blake160
    args[56]     状态标志：0 = 首次承诺，1 = 派生输出
    args[57]     feature 字节（仅 V1，0x01 = TLC 携带完整支付哈希）

派生输出保留原 args 的前 36 字节（公钥哈希 + delay epoch + 承诺号）。
"""

from dataclasses import dataclass
import hashlib

HASH_LENGTHS = {"legacy": 20, "v1": 32}
# 1 CKB = 100_000_000 Shannon。
CKB = 100_000_000


@dataclass(frozen=True)
class CommitmentLockLayout:
    """一个合约版本的承诺锁 args 与 settlement witness 布局。"""

    args_len: int  # 承诺锁 args 总字节数
    tlc_entry_size: int  # witness 中单笔 TLC 条目字节数
    feature_byte: int | None  # args 末尾 feature 字节；None = 该版本没有 feature 字节


# 新增合约版本（v2/v3）时在此登记一行；断言辅助全部按此表生效。
COMMITMENT_LOCK_LAYOUTS = {
    "legacy": CommitmentLockLayout(args_len=57, tlc_entry_size=85, feature_byte=None),
    # feature 0x01 = TLC 条目携带完整 32 字节支付哈希。
    "v1": CommitmentLockLayout(args_len=58, tlc_entry_size=97, feature_byte=1),
}

# 承诺锁 args[20:28]：延迟解锁 epoch 的小端编码，各版本一致。
COMMITMENT_DELAY_EPOCH = 0xA000010000000001
# 派生输出保留原 args 的前 36 字节（公钥哈希 + delay epoch + 承诺号）。
COMMITMENT_ARGS_PREFIX_LEN = 36
# args[56] 状态标志：0 = 首次承诺，1 = 派生输出。
COMMITMENT_STATE_FLAG_INDEX = 56


def commitment_lock_layout(version: str) -> CommitmentLockLayout:
    """按版本取承诺锁布局；未知版本直接报错（合约升级后先在这里登记）。"""
    try:
        return COMMITMENT_LOCK_LAYOUTS[version]
    except KeyError:
        raise ValueError(
            f"unsupported commitment version: {version!r}; "
            f"expected one of {sorted(COMMITMENT_LOCK_LAYOUTS)}"
        ) from None


def commitment_args_len(version: str) -> int:
    """承诺锁 args 长度：Legacy = 57，V1 = 57 + 1 字节 feature。"""
    return commitment_lock_layout(version).args_len


def assert_commitment_args(args: bytes, version: str, *, derived: bool | None = None):
    """核对承诺锁 args 的版本化布局：长度、状态标志与 feature 字节。

    derived=True/False 额外核对 args[56] 状态标志（1 = 派生输出 / 0 = 首次承诺）；
    derived=None 不核对状态标志。feature 字节只在有 feature 的版本（V1）上核对。
    """
    layout = commitment_lock_layout(version)
    assert (
        len(args) == layout.args_len
    ), f"{version}: commitment lock args 应为 {layout.args_len} 字节，实测 {len(args)}"
    if derived is not None:
        expected_flag = 1 if derived else 0
        assert args[COMMITMENT_STATE_FLAG_INDEX] == expected_flag, (
            f"{version}: args[{COMMITMENT_STATE_FLAG_INDEX}] 状态标志应为 "
            f"{expected_flag}（{'派生' if derived else '首次承诺'}），"
            f"实测 {args[COMMITMENT_STATE_FLAG_INDEX]:#04x}"
        )
    if layout.feature_byte is not None:
        assert args[-1] == layout.feature_byte, (
            f"{version}: feature 字节应为 {layout.feature_byte:#04x}，"
            f"实测 {args[-1]:#04x}"
        )


def assert_commitment_args_prefix(args: bytes, before_args: bytes):
    """派生输出必须保留原 args 前 36 字节（公钥哈希 + delay epoch + 承诺号）。"""
    assert (
        args[:COMMITMENT_ARGS_PREFIX_LEN] == before_args[:COMMITMENT_ARGS_PREFIX_LEN]
    ), "derived commitment args must keep the original 36-byte prefix"


def assert_commitment_delay_epoch(args: bytes):
    """args[20:28] 是固定的延迟解锁 epoch 编码，各版本一致。"""
    actual = int.from_bytes(args[20:28], "little")
    assert (
        actual == COMMITMENT_DELAY_EPOCH
    ), f"delay epoch 应为 {COMMITMENT_DELAY_EPOCH:#x}，实测 {actual:#x}"


def _hash_length(version: str) -> int:
    if version not in HASH_LENGTHS:
        raise ValueError(
            f"unsupported witness version: {version!r}; expected legacy or v1"
        )
    return HASH_LENGTHS[version]


def tlc_entry_size(version: str) -> int:
    """单笔 TLC 条目在 witness 中的字节数：V1 = 97，Legacy = 85。"""
    return commitment_lock_layout(version).tlc_entry_size


def witness_size(version: str, tlc_count: int) -> int:
    """settlement witness 总字节数 = 90 字节固定头 + 每笔 TLC 条目 + 99 字节固定尾。"""
    return 90 + tlc_entry_size(version) * tlc_count + 99


def commitment_tx_size(version: str) -> int:
    """复刻 fiber-lib `commitment_tx_size` 的 mock 承诺交易字节长度。

    mock = 1 个默认 input + 1 个 commitment-lock output + FUNDING_CELL_WITNESS_LEN(112)
    witness + FundingLock 的 cell deps；承诺锁 args 长度按版本取 57/58。
    """
    cell_dep = 4 + 8 + 36 + 1  # CellDep 表头 + OutPoint(36) + option dep_type(1)
    args = commitment_args_len(version)
    lock = 4 + 12 + 32 + 1 + (4 + 4 + args + (-args) % 4)  # Script 表
    output = 4 + 12 + 8 + lock + 4  # CellOutput 表 + 空 type option
    parts = (
        4 + 4 + cell_dep,  # cell_deps：FundingLock 的 1 个 code dep
        4 + 4,  # header_deps：空
        4 + 4 + 44,  # inputs：1 个默认 input
        4 + 4 + output,  # outputs：1 个承诺锁 output
        4 + 4 + (4 + 4),  # outputs_data：1 个空 Bytes
        4 + 4 + (4 + 4 + 112 + (-112) % 4),  # witnesses：112 字节 funding witness
    )
    return 4 + 28 + 4 + sum(parts)


def _fixed(value: bytes, size: int, name: str) -> bytes:
    if not isinstance(value, bytes) or len(value) != size:
        raise ValueError(f"{name}: expected {size} bytes")
    return value


@dataclass
class SettlementTlc:
    tlc_type: int
    amount: int
    payment_hash: bytes  # Legacy: 20 字节前缀；V1: 完整 32 字节。
    remote_pubkey_hash: bytes
    local_pubkey_hash: bytes
    expiry: int


@dataclass
class SettlementUnlock:
    unlock_type: int  # TLC 索引，或 0xfe / 0xff 双方余额。
    signature: bytes
    preimage: bytes | None = None  # None 表示无原像；32 字节全零仍是有原像。


@dataclass
class SettlementWitness:
    version: str
    prefix: bytes
    tlcs: list[SettlementTlc]
    remote_pubkey_hash: bytes
    remote_amount: int
    local_pubkey_hash: bytes
    local_amount: int
    unlocks: list[SettlementUnlock]

    @classmethod
    def from_hex(cls, value: str, *, version: str) -> "SettlementWitness":
        hash_length = _hash_length(version)
        raw = bytes.fromhex(value.removeprefix("0x"))
        offset = 0

        def take(size):
            nonlocal offset
            end = offset + size
            if end > len(raw):
                raise ValueError(
                    f"truncated {version} witness at byte {offset}: need {size}"
                )
            value = raw[offset:end]
            offset = end
            return value

        prefix = take(16)
        unlock_count, tlc_count = take(2)
        tlcs = []
        for _ in range(tlc_count):
            tlcs.append(
                SettlementTlc(
                    take(1)[0],
                    int.from_bytes(take(16), "little"),
                    take(hash_length),
                    take(20),
                    take(20),
                    int.from_bytes(take(8), "little"),
                )
            )
        remote_hash = take(20)
        remote_amount = int.from_bytes(take(16), "little")
        local_hash = take(20)
        local_amount = int.from_bytes(take(16), "little")
        unlocks = []
        for _ in range(unlock_count):
            unlock_type, flag = take(2)
            if flag not in (0, 1):
                raise ValueError(f"invalid with_preimage flag: {flag}")
            signature = take(65)
            unlocks.append(
                SettlementUnlock(unlock_type, signature, take(32) if flag else None)
            )
        if offset != len(raw):
            raise ValueError(f"trailing {version} witness bytes: {len(raw) - offset}")
        return cls(
            version,
            prefix,
            tlcs,
            remote_hash,
            remote_amount,
            local_hash,
            local_amount,
            unlocks,
        )

    def to_hex(self) -> str:
        hash_length = _hash_length(self.version)
        parts = [
            _fixed(self.prefix, 16, "prefix"),
            bytes((len(self.unlocks), len(self.tlcs))),
        ]
        for tlc in self.tlcs:
            parts.extend(
                (
                    bytes((tlc.tlc_type,)),
                    tlc.amount.to_bytes(16, "little"),
                    _fixed(tlc.payment_hash, hash_length, "TLC payment_hash"),
                    _fixed(tlc.remote_pubkey_hash, 20, "TLC remote_pubkey_hash"),
                    _fixed(tlc.local_pubkey_hash, 20, "TLC local_pubkey_hash"),
                    tlc.expiry.to_bytes(8, "little"),
                )
            )
        parts.extend(
            (
                _fixed(self.remote_pubkey_hash, 20, "remote_pubkey_hash"),
                self.remote_amount.to_bytes(16, "little"),
                _fixed(self.local_pubkey_hash, 20, "local_pubkey_hash"),
                self.local_amount.to_bytes(16, "little"),
            )
        )
        for unlock in self.unlocks:
            parts.extend(
                (
                    bytes((unlock.unlock_type, int(unlock.preimage is not None))),
                    _fixed(unlock.signature, 65, "signature"),
                )
            )
            if unlock.preimage is not None:
                parts.append(_fixed(unlock.preimage, 32, "preimage"))
        return "0x" + b"".join(parts).hex()

    def assert_pending_tlcs(self, pending: list[tuple[str, int]]) -> None:
        """按版本核对全部 TLC 的 hash 和金额，忽略列表顺序但保留重复项。"""
        hash_length = _hash_length(self.version)
        expected = []
        for payment_hash, amount in pending:
            full_hash = _fixed(
                bytes.fromhex(payment_hash.removeprefix("0x")), 32, "payment_hash"
            )
            expected.append((full_hash[:hash_length], amount))
        actual = [(tlc.payment_hash, tlc.amount) for tlc in self.tlcs]
        assert sorted(actual) == sorted(
            expected
        ), f"pending TLCs differ: {actual!r} != {expected!r}"

    def assert_single_tlc_claim(
        self, pending: list[tuple[str, int]], preimage: str
    ) -> None:
        """断言单笔原像兑付；pending 传完整 32 字节 hash 和金额，不验证签名。"""
        self.assert_pending_tlcs(pending)
        hash_length = _hash_length(self.version)
        assert len(self.unlocks) == 1, f"expected one unlock, got {len(self.unlocks)}"
        unlock = self.unlocks[0]
        assert (
            0 <= unlock.unlock_type < len(self.tlcs)
        ), f"invalid TLC index: {unlock.unlock_type}"
        expected_preimage = _fixed(
            bytes.fromhex(preimage.removeprefix("0x")), 32, "preimage"
        )
        assert unlock.preimage == expected_preimage, "unlock preimage differs"
        tlc = self.tlcs[unlock.unlock_type]
        digest = (
            hashlib.sha256(expected_preimage).digest()
            if tlc.tlc_type & 2
            else hashlib.blake2b(
                expected_preimage, digest_size=32, person=b"ckb-default-hash"
            ).digest()
        )
        assert (
            tlc.payment_hash == digest[:hash_length]
        ), "unlock selects a TLC with a different payment hash"
