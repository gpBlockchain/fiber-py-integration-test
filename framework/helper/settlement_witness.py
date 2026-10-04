"""多版本 settlement witness 的解析、调整和断言（不是 Molecule WitnessArgs）。

    witness = SettlementWitness.from_hex(tx["witnesses"][0], version="legacy")
    witness.assert_single_tlc_claim([(payment_hash, amount)], preimage)
    witness.tlcs[0].amount += 1
    tx["witnesses"][0] = witness.to_hex()

字段中的 hash/signature/preimage 使用 bytes，金额使用 int；计数自动随列表更新。
保留原始 16 字节前缀及签名；修改后不自动重签，也不模拟合约的完整验证。
version 显式选 "legacy"（85 字节 TLC / 20 字节 hash）或 "v1"（97 / 32）。
两版 witness 本身没有版本标签；从通道/承诺的版本传入，不按长度猜测。
"""

from dataclasses import dataclass
import hashlib

HASH_LENGTHS = {"legacy": 20, "v1": 32}


def _hash_length(version: str) -> int:
    if version not in HASH_LENGTHS:
        raise ValueError(
            f"unsupported witness version: {version!r}; expected legacy or v1"
        )
    return HASH_LENGTHS[version]


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
