"""Pick the exact signed commitment cached by the debug (attack) fnn.

The attack fixture caches every peer-signed commitment it accepts, keyed by
the commitment number embedded in the funding lock args (see
``list_commitment_transactions``). Guessing numbers from
``get_channel_musig2_public`` is wrong on Channel V2: the first guess that
exists is usually the *pre-TLC* commitment, which settles with 0 pending TLCs
and never exercises the LocalAnnounced on-chain path.
"""

import time


def _cn(value):
    return int(value, 16) if isinstance(value, str) else int(value)


def cached_commitments(client, channel_id):
    result = client.call("list_commitment_transactions", [{"channel_id": channel_id}])
    return sorted(
        result.get("transactions") or [], key=lambda tx: _cn(tx["commitment_number"])
    )


def latest_commitment_number(client, channel_id, timeout=10):
    """Highest cached number before a payment; -1 if the cache stays empty."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        txs = cached_commitments(client, channel_id)
        if txs:
            return _cn(txs[-1]["commitment_number"])
        time.sleep(0.2)
    return -1


def submit_newest_commitment(client, channel_id, after_number, timeout=30):
    """Submit the newest cached commitment strictly newer than ``after_number``."""
    deadline = time.monotonic() + timeout
    txs = []
    while time.monotonic() < deadline:
        txs = cached_commitments(client, channel_id)
        if txs and _cn(txs[-1]["commitment_number"]) > after_number:
            newest = txs[-1]
            number = _cn(newest["commitment_number"])
            submitted = client.call(
                "submit_commitment_transaction",
                [{"channel_id": channel_id, "commitment_number": hex(number)}],
            )
            assert submitted["tx_hash"] == newest["tx_hash"], {
                "expected": newest,
                "submitted": submitted,
            }
            return submitted
        time.sleep(0.2)
    raise TimeoutError(
        f"no signed commitment newer than {after_number} cached for {channel_id}: {txs}"
    )
