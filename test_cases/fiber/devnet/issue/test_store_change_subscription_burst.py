import hashlib
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import websocket

from framework.basic_fiber import FiberTest


def sha256_hex(preimage_hex):
    raw = bytes.fromhex(preimage_hex.replace("0x", ""))
    return "0x" + hashlib.sha256(raw).hexdigest()


def subscribe_store_changes(rpc_port):
    """Open a websocket subscription to store changes; return (ws, sub_id)."""
    ws = websocket.create_connection(f"ws://127.0.0.1:{rpc_port}", timeout=5)
    ws.send(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "subscribe_store_changes",
                "params": [],
            }
        )
    )
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == 1:
            assert "result" in msg, f"subscribe failed: {msg}"
            return ws, msg["result"]


def collect_events(
    ws, relevant_hashes, target_count, deadline_seconds=120, settle_seconds=5
):
    """Collect store-change events in arrival order.

    Each event is (event_type, payment_hash, payload). Once `target_count`
    events whose payment_hash is in `relevant_hashes` have been seen, keep
    observing for a fixed trailing window (`settle_seconds`) so duplicates
    arriving right after the last target event are still caught. Raises
    TimeoutError if the target is not reached before the deadline.
    """
    events = []
    relevant_seen = 0
    deadline = time.time() + deadline_seconds
    settle_until = None
    ws.settimeout(1)
    while True:
        # two separate bounds: the main deadline applies only until the target
        # is reached; the trailing window then gets its full settle_seconds
        now = time.time()
        if settle_until is None:
            if now >= deadline:
                break
        elif now >= settle_until:
            break
        try:
            msg = json.loads(ws.recv())
        except websocket.WebSocketTimeoutException:
            continue
        params = msg.get("params", {})
        if "subscription" not in params:
            continue
        result = params.get("result")
        if not isinstance(result, dict) or not result:
            continue
        event_type = next(iter(result))
        payload = result[event_type]
        payment_hash = payload.get("payment_hash")
        events.append((event_type, payment_hash, payload))
        if settle_until is None and payment_hash in relevant_hashes:
            relevant_seen += 1
            if relevant_seen >= target_count:
                settle_until = time.time() + settle_seconds
    if relevant_seen < target_count:
        raise TimeoutError(
            f"saw {relevant_seen}/{target_count} relevant events within {deadline_seconds}s"
        )
    return events


def assert_batch_complete(events, normal_hashes, marker_hash):
    """Assert the exact event set for `normal_hashes` + one hash-only marker.

    No event lost, no duplicates, each invoice's PutCkbInvoiceStatus(Open)
    precedes its PutPreimage, and the marker's Open is the last relevant
    event. Events outside this batch (e.g. leftovers from an earlier batch)
    are neither counted nor treated as failures.
    """
    normal = set(normal_hashes)
    assert len(normal) == len(
        normal_hashes
    ), "duplicate payment_hash in created invoices"
    relevant = [(t, h, p) for t, h, p in events if h in normal or h == marker_hash]

    counts = Counter((t, h) for t, h, _ in relevant)
    duplicates = {k: v for k, v in counts.items() if v > 1}
    assert not duplicates, f"duplicate store-change events: {list(duplicates)[:5]}"

    for t, h, p in relevant:
        if t == "PutCkbInvoiceStatus":
            assert (
                p.get("invoice_status") == "Open"
            ), f"unexpected status event: {t} {h} {p}"

    missing = []
    for h in normal_hashes:
        for t in ("PutCkbInvoiceStatus", "PutPreimage"):
            if (t, h) not in counts:
                missing.append((t, h))
    if marker_hash is not None and ("PutCkbInvoiceStatus", marker_hash) not in counts:
        missing.append(("PutCkbInvoiceStatus", marker_hash))
    assert not missing, f"missing {len(missing)} events, e.g. {missing[:3]}"

    expected_total = 2 * len(normal_hashes) + (1 if marker_hash is not None else 0)
    assert (
        len(relevant) == expected_total
    ), f"expected {expected_total} relevant events, got {len(relevant)}"

    first_index = {}
    for idx, (t, h, _) in enumerate(relevant):
        first_index.setdefault((t, h), idx)
    for h in normal_hashes:
        assert (
            first_index[("PutCkbInvoiceStatus", h)] < first_index[("PutPreimage", h)]
        ), f"PutPreimage arrived before PutCkbInvoiceStatus for {h}"
    if marker_hash is not None:
        last = relevant[-1]
        assert (last[0], last[1]) == (
            "PutCkbInvoiceStatus",
            marker_hash,
        ), f"marker event is not the last relevant event: {last}"


class TestStoreChangeSubscriptionBurst(FiberTest):
    """
    PR-1718 regression: a burst of store changes must not kill or starve
    store-change subscriptions; subscribers do not affect each other.
    """

    BURST_THREADS = 16

    def _new_invoice(self, description):
        return self.fiber1.get_client().new_invoice(
            {
                "amount": hex(1 * 100000000),
                "currency": "Fibd",
                "description": description,
            }
        )

    def _burst_invoices(self, total, tag):
        """Create `total` invoices over worker threads; return their payment hashes.

        Every RPC must succeed: any worker exception propagates through
        future.result() and fails the test.
        """
        per_thread = total // self.BURST_THREADS
        assert per_thread * self.BURST_THREADS == total

        def worker(tid):
            return [
                self._new_invoice(f"{tag} {tid}-{i}")["invoice"]["data"]["payment_hash"]
                for i in range(per_thread)
            ]

        with ThreadPoolExecutor(max_workers=self.BURST_THREADS) as pool:
            futures = [pool.submit(worker, t) for t in range(self.BURST_THREADS)]
            results = [future.result() for future in futures]
        hashes = [h for result in results for h in result]
        assert len(hashes) == total, f"only {len(hashes)}/{total} invoices created"
        assert len(set(hashes)) == total, "duplicate payment_hash returned"
        return hashes

    def _marker_invoice(self):
        """One hash-only invoice with a known payment hash, used to prove liveness."""
        marker_preimage = self.generate_random_preimage()
        marker_hash = sha256_hex(marker_preimage)
        self.fiber1.get_client().new_invoice(
            {
                "amount": hex(1 * 100000000),
                "currency": "Fibd",
                "description": "marker invoice after burst",
                "payment_hash": marker_hash,
                "hash_algorithm": "sha256",
            }
        )
        return marker_hash

    # TEST-MAP: PR1718-01
    def test_subscription_survives_store_change_burst(self):
        ws, _sub_id = subscribe_store_changes(self.fiber1.rpc_port)
        try:
            burst_hashes = self._burst_invoices(160, "burst invoice")
            marker_hash = self._marker_invoice()

            events = collect_events(ws, set(burst_hashes) | {marker_hash}, 321)
            assert_batch_complete(events, burst_hashes, marker_hash)
        finally:
            ws.close()

    # TEST-MAP: PR1718-02
    def test_closing_one_subscription_keeps_the_other_alive(self):
        ws_a, _sub_a = subscribe_store_changes(self.fiber1.rpc_port)
        ws_b, _sub_b = subscribe_store_changes(self.fiber1.rpc_port)
        try:
            batch1 = self._burst_invoices(80, "batch1 invoice")
            # barrier: both subscribers received all of batch 1 before A closes
            events_a = collect_events(ws_a, set(batch1), 160)
            assert_batch_complete(events_a, batch1, None)
            events_b1 = collect_events(ws_b, set(batch1), 160)
            assert_batch_complete(events_b1, batch1, None)

            ws_a.close()

            batch2 = self._burst_invoices(80, "batch2 invoice")
            marker_hash = self._marker_invoice()
            events_b2 = collect_events(ws_b, set(batch2) | {marker_hash}, 161)
            # batch-1 leftovers on B are neither counted nor a failure
            assert_batch_complete(events_b2, batch2, marker_hash)
        finally:
            ws_b.close()
