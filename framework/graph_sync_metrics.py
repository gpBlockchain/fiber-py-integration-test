import os
import time
from datetime import datetime, timezone


GRAPH_LIMIT = "0xffff"


def read_positive_int_env(name, default):
    try:
        value = int(os.getenv(name, ""))
    except ValueError:
        return default
    if value <= 0:
        return default
    return value


def _graph_counts(client):
    channels = client.graph_channels({"limit": GRAPH_LIMIT}).get("channels", [])
    nodes = client.graph_nodes({"limit": GRAPH_LIMIT}).get("nodes", [])
    return len(channels), len(nodes)


def _list_peers_count(client):
    peers = client.list_peers().get("peers", [])
    return len(peers)


def _rate_per_minute(delta, elapsed_seconds):
    if elapsed_seconds <= 0:
        return 0.0
    return round(delta * 60.0 / elapsed_seconds, 3)


def _print_sample(label, elapsed_seconds, channels_count, nodes_count, peers_count):
    print(
        "[{}] elapsed={:.2f}s graph_channels={} graph_nodes={} list_peers={}".format(
            label,
            elapsed_seconds,
            channels_count,
            nodes_count,
            peers_count,
        )
    )


def sample_graph_sync(client, duration_seconds, sample_interval_seconds, label):
    start = time.time()
    deadline = start + duration_seconds
    samples = []

    while True:
        now = time.time()
        channels_count, nodes_count = _graph_counts(client)
        peers_count = _list_peers_count(client)
        elapsed_seconds = round(now - start, 3)
        sample = {
            "elapsed_seconds": elapsed_seconds,
            "graph_channels_count": channels_count,
            "graph_nodes_count": nodes_count,
            "list_peers_count": peers_count,
        }
        samples.append(sample)
        _print_sample(
            label,
            elapsed_seconds,
            channels_count,
            nodes_count,
            peers_count,
        )

        if now >= deadline:
            break
        time.sleep(min(sample_interval_seconds, max(0, deadline - now)))

    first = samples[0]
    last = samples[-1]
    elapsed_seconds = max(last["elapsed_seconds"], duration_seconds)
    channel_delta = last["graph_channels_count"] - first["graph_channels_count"]
    node_delta = last["graph_nodes_count"] - first["graph_nodes_count"]
    summary = {
        "label": label,
        "duration_seconds": duration_seconds,
        "sample_interval_seconds": sample_interval_seconds,
        "initial_graph_channels_count": first["graph_channels_count"],
        "final_graph_channels_count": last["graph_channels_count"],
        "graph_channels_delta": channel_delta,
        "graph_channels_rate_per_minute": _rate_per_minute(
            channel_delta,
            elapsed_seconds,
        ),
        "initial_graph_nodes_count": first["graph_nodes_count"],
        "final_graph_nodes_count": last["graph_nodes_count"],
        "graph_nodes_delta": node_delta,
        "graph_nodes_rate_per_minute": _rate_per_minute(
            node_delta,
            elapsed_seconds,
        ),
        "final_list_peers_count": last["list_peers_count"],
        "samples": samples,
    }
    print("[{}] graph sync summary: {}".format(label, summary))
    return summary


def _utc_timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _timed_rpc(method_name, call):
    started = time.monotonic()
    try:
        return call(), round(time.monotonic() - started, 3), None
    except Exception as exc:
        return None, round(time.monotonic() - started, 3), str(exc)


def _query_sync_snapshot(client):
    """Query each diagnostic RPC independently so one failure stays visible."""
    calls = (
        ("graph_channels", lambda: client.graph_channels({"limit": GRAPH_LIMIT})),
        ("graph_nodes", lambda: client.graph_nodes({"limit": GRAPH_LIMIT})),
        ("list_peers", client.list_peers),
        ("node_info", client.node_info),
    )
    results = {}
    rpc_seconds = {}
    errors = {}
    for method_name, call in calls:
        result, duration, error = _timed_rpc(method_name, call)
        results[method_name] = result
        rpc_seconds[method_name] = duration
        if error is not None:
            errors[method_name] = error

    channels_result = results["graph_channels"] or {}
    nodes_result = results["graph_nodes"] or {}
    peers_result = results["list_peers"] or {}
    node_info = results["node_info"] or {}
    peers = peers_result.get("peers", [])
    peer_keys = sorted(
        str(peer.get("pubkey") or peer.get("peer_id") or peer.get("address") or "?")
        for peer in peers
    )
    return {
        "graph_channels_count": len(channels_result.get("channels", [])),
        "graph_nodes_count": len(nodes_result.get("nodes", [])),
        "list_peers_count": len(peers),
        "peer_keys": peer_keys,
        "network_sync_status": node_info.get("network_sync_status", "unknown"),
        "node_version": node_info.get("version", "unknown"),
        "node_commit_hash": node_info.get("commit_hash", "unknown"),
        "rpc_seconds": rpc_seconds,
        "rpc_total_seconds": round(sum(rpc_seconds.values()), 3),
        "errors": errors,
    }


def _new_rpc_stats():
    return {
        name: {"calls": 0, "errors": 0, "total_seconds": 0.0, "max_seconds": 0.0}
        for name in ("graph_channels", "graph_nodes", "list_peers", "node_info")
    }


def _update_rpc_stats(state, snapshot):
    for method_name, duration in snapshot["rpc_seconds"].items():
        stats = state["rpc_stats"][method_name]
        stats["calls"] += 1
        stats["total_seconds"] += duration
        stats["max_seconds"] = max(stats["max_seconds"], duration)
        if method_name in snapshot["errors"]:
            stats["errors"] += 1


def _final_rpc_stats(state):
    result = {}
    for method_name, stats in state["rpc_stats"].items():
        calls = stats["calls"]
        result[method_name] = {
            "calls": calls,
            "errors": stats["errors"],
            "average_seconds": round(
                stats["total_seconds"] / calls if calls else 0.0,
                3,
            ),
            "max_seconds": round(stats["max_seconds"], 3),
        }
    return result


def _format_rpc_seconds(rpc_seconds):
    return ",".join(
        "{}:{:.3f}s".format(method_name, duration)
        for method_name, duration in rpc_seconds.items()
    )


def _compact_error(error):
    return " ".join(str(error).split())[:240]


def _diagnostic_signals(state, slow_rpc_threshold_seconds):
    signals = []
    if state["query_failures"]:
        signals.append("rpc_errors")
    if state["peer_unavailable_samples"]:
        signals.append("peer_unavailable")
    if state["peer_change_events"]:
        signals.append("peer_set_changed")
    if state["regression_events"]:
        signals.append("graph_count_regressed")
    if any(
        stats["max_seconds"] >= slow_rpc_threshold_seconds
        for stats in state["rpc_stats"].values()
    ):
        signals.append("slow_rpc")
    if state["graph_change_events"]:
        signals.append("graph_updates_trickled")
    if not signals:
        signals.append("initial_snapshot_stable")
    return signals


def _build_summary(
    state,
    now,
    start,
    stable_seconds,
    sample_interval_seconds,
    slow_rpc_threshold_seconds,
    reason,
):
    elapsed = now - start
    first_channels = state["first_channels"] or 0
    first_nodes = state["first_nodes"] or 0
    final_channels = state["last_channels"] or 0
    final_nodes = state["last_nodes"] or 0
    last_change_elapsed = max(0.0, state["last_change_at"] - start)
    stable_for = max(0.0, now - state["last_change_at"])
    signals = _diagnostic_signals(state, slow_rpc_threshold_seconds)
    return {
        "label": state["label"],
        "elapsed_seconds": round(elapsed, 3),
        "stable_seconds": stable_seconds,
        "stable_for_seconds": round(stable_for, 3),
        "sample_interval_seconds": sample_interval_seconds,
        "slow_rpc_threshold_seconds": slow_rpc_threshold_seconds,
        "initial_graph_channels_count": first_channels,
        "final_graph_channels_count": final_channels,
        "graph_channels_delta": final_channels - first_channels,
        "graph_channels_added": state["channels_added"],
        "graph_channels_removed": state["channels_removed"],
        "initial_graph_nodes_count": first_nodes,
        "final_graph_nodes_count": final_nodes,
        "graph_nodes_delta": final_nodes - first_nodes,
        "graph_nodes_added": state["nodes_added"],
        "graph_nodes_removed": state["nodes_removed"],
        "final_list_peers_count": state["last_peers"],
        "final_peer_keys": state["last_peer_keys"],
        "final_network_sync_status": state["last_network_sync_status"],
        "node_version": state["node_version"],
        "node_commit_hash": state["node_commit_hash"],
        "graph_change_events": state["graph_change_events"],
        "growth_events": state["growth_events"],
        "regression_events": state["regression_events"],
        "peer_change_events": state["peer_change_events"],
        "peer_unavailable_samples": state["peer_unavailable_samples"],
        "query_failure_count": len(state["query_failures"]),
        "query_failures": state["query_failures"],
        "last_change_elapsed_seconds": round(last_change_elapsed, 3),
        "longest_change_gap_seconds": round(state["longest_change_gap"], 3),
        "rpc_stats": _final_rpc_stats(state),
        "diagnostic_signals": signals,
        "samples": state["samples"],
        "reason": reason,
    }


def _print_diagnostic_summary(summary):
    rpc_max = max(
        (stats["max_seconds"] for stats in summary["rpc_stats"].values()),
        default=0.0,
    )
    print(
        "[{label}] DIAGNOSTIC elapsed={elapsed:.2f}s last_change={last_change:.2f}s "
        "stable_for={stable_for:.2f}s changes={changes} growth={growth} "
        "regressions={regressions} longest_change_gap={gap:.2f}s "
        "peer_changes={peer_changes} peer_zero_samples={peer_zero} "
        "rpc_failures={rpc_failures} rpc_max={rpc_max:.3f}s signals={signals}".format(
            label=summary["label"],
            elapsed=summary["elapsed_seconds"],
            last_change=summary["last_change_elapsed_seconds"],
            stable_for=summary["stable_for_seconds"],
            changes=summary["graph_change_events"],
            growth=summary["growth_events"],
            regressions=summary["regression_events"],
            gap=summary["longest_change_gap_seconds"],
            peer_changes=summary["peer_change_events"],
            peer_zero=summary["peer_unavailable_samples"],
            rpc_failures=summary["query_failure_count"],
            rpc_max=rpc_max,
            signals=",".join(summary["diagnostic_signals"]),
        )
    )


def sample_nodes_graph_sync_until_stable(
    targets,
    sample_interval_seconds=5,
    stable_seconds=60,
    max_duration_seconds=7200,
):
    """Poll Fiber nodes until graph counts stabilize, with diagnosis-ready logs.

    Every poll records per-RPC latency, graph deltas, time since the last graph
    change, peer identity changes, network sync status, and RPC errors. This
    makes a slow run distinguishable as graph trickle, peer loss, RPC latency,
    or RPC failure from the CI log alone.
    """
    start = time.monotonic()
    deadline = start + max_duration_seconds
    slow_rpc_threshold_seconds = max(1.0, sample_interval_seconds / 2.0)
    states = {}
    for target in targets:
        label = target["label"]
        states[label] = {
            "client": target["client"],
            "label": label,
            "done": False,
            "first_channels": None,
            "first_nodes": None,
            "last_channels": None,
            "last_nodes": None,
            "last_peers": 0,
            "last_peer_keys": [],
            "last_network_sync_status": "unknown",
            "node_version": "unknown",
            "node_commit_hash": "unknown",
            "last_change_at": start,
            "last_sample_started_at": None,
            "longest_change_gap": 0.0,
            "graph_change_events": 0,
            "growth_events": 0,
            "regression_events": 0,
            "channels_added": 0,
            "channels_removed": 0,
            "nodes_added": 0,
            "nodes_removed": 0,
            "peer_change_events": 0,
            "peer_unavailable_samples": 0,
            "query_failures": [],
            "rpc_stats": _new_rpc_stats(),
            "samples": [],
            "summary": None,
        }

    while True:
        for label, state in states.items():
            if state["done"]:
                continue

            sample_started = time.monotonic()
            snapshot = _query_sync_snapshot(state["client"])
            sample_finished = time.monotonic()
            elapsed = sample_finished - start
            poll_gap = (
                0.0
                if state["last_sample_started_at"] is None
                else sample_started - state["last_sample_started_at"]
            )
            state["last_sample_started_at"] = sample_started
            _update_rpc_stats(state, snapshot)

            if snapshot["errors"]:
                error_record = {
                    "elapsed_seconds": round(elapsed, 3),
                    "utc": _utc_timestamp(),
                    "errors": snapshot["errors"],
                }
                state["query_failures"].append(error_record)
                print(
                    "[{}] QUERY_ERROR elapsed={:.2f}s methods={} rpc={} errors={}".format(
                        label,
                        elapsed,
                        ",".join(snapshot["errors"]),
                        _format_rpc_seconds(snapshot["rpc_seconds"]),
                        ";".join(
                            "{}:{}".format(name, _compact_error(error))
                            for name, error in snapshot["errors"].items()
                        ),
                    )
                )

            core_query_failed = any(
                name in snapshot["errors"]
                for name in ("graph_channels", "graph_nodes")
            )
            if core_query_failed:
                continue

            channels_count = snapshot["graph_channels_count"]
            nodes_count = snapshot["graph_nodes_count"]
            peers_count = snapshot["list_peers_count"]
            peer_keys = snapshot["peer_keys"]
            network_sync_status = snapshot["network_sync_status"]
            previous_channels = state["last_channels"]
            previous_nodes = state["last_nodes"]
            channels_delta = (
                0 if previous_channels is None else channels_count - previous_channels
            )
            nodes_delta = 0 if previous_nodes is None else nodes_count - previous_nodes
            graph_changed = (
                previous_channels is not None
                and (channels_delta != 0 or nodes_delta != 0)
            )

            if state["first_channels"] is None:
                state["first_channels"] = channels_count
                state["first_nodes"] = nodes_count
                state["last_change_at"] = sample_finished
            elif graph_changed:
                state["longest_change_gap"] = max(
                    state["longest_change_gap"],
                    sample_finished - state["last_change_at"],
                )
                state["last_change_at"] = sample_finished
                state["graph_change_events"] += 1
                if channels_delta > 0 or nodes_delta > 0:
                    state["growth_events"] += 1
                if channels_delta < 0 or nodes_delta < 0:
                    state["regression_events"] += 1
                state["channels_added"] += max(0, channels_delta)
                state["channels_removed"] += max(0, -channels_delta)
                state["nodes_added"] += max(0, nodes_delta)
                state["nodes_removed"] += max(0, -nodes_delta)

            if state["samples"] and peer_keys != state["last_peer_keys"]:
                state["peer_change_events"] += 1
                print(
                    "[{}] PEER_CHANGE elapsed={:.2f}s before={} after={}".format(
                        label,
                        elapsed,
                        ",".join(state["last_peer_keys"]) or "none",
                        ",".join(peer_keys) or "none",
                    )
                )
            if peers_count == 0:
                state["peer_unavailable_samples"] += 1
            if (
                state["samples"]
                and network_sync_status != state["last_network_sync_status"]
            ):
                print(
                    "[{}] SYNC_STATUS_CHANGE elapsed={:.2f}s before={} after={}".format(
                        label,
                        elapsed,
                        state["last_network_sync_status"],
                        network_sync_status,
                    )
                )

            state["last_channels"] = channels_count
            state["last_nodes"] = nodes_count
            state["last_peers"] = peers_count
            state["last_peer_keys"] = peer_keys
            state["last_network_sync_status"] = network_sync_status
            state["node_version"] = snapshot["node_version"]
            state["node_commit_hash"] = snapshot["node_commit_hash"]
            stable_for = sample_finished - state["last_change_at"]
            sample = {
                "sequence": len(state["samples"]) + 1,
                "utc": _utc_timestamp(),
                "elapsed_seconds": round(elapsed, 3),
                "poll_gap_seconds": round(poll_gap, 3),
                "rpc_total_seconds": snapshot["rpc_total_seconds"],
                "rpc_seconds": snapshot["rpc_seconds"],
                "graph_channels_count": channels_count,
                "graph_channels_delta": channels_delta,
                "graph_nodes_count": nodes_count,
                "graph_nodes_delta": nodes_delta,
                "list_peers_count": peers_count,
                "peer_keys": peer_keys,
                "network_sync_status": network_sync_status,
                "stable_for_seconds": round(stable_for, 3),
            }
            state["samples"].append(sample)
            print(
                "[{label}] SAMPLE seq={sequence} utc={utc} elapsed={elapsed:.2f}s "
                "poll_gap={poll_gap:.2f}s rpc_total={rpc_total:.3f}s rpc={rpc} "
                "graph_channels={channels} delta_channels={channels_delta:+d} "
                "graph_nodes={nodes} delta_nodes={nodes_delta:+d} "
                "list_peers={peers} peer_set={peer_set} sync_status={sync_status} "
                "stable_for={stable_for:.2f}s".format(
                    label=label,
                    sequence=sample["sequence"],
                    utc=sample["utc"],
                    elapsed=elapsed,
                    poll_gap=poll_gap,
                    rpc_total=snapshot["rpc_total_seconds"],
                    rpc=_format_rpc_seconds(snapshot["rpc_seconds"]),
                    channels=channels_count,
                    channels_delta=channels_delta,
                    nodes=nodes_count,
                    nodes_delta=nodes_delta,
                    peers=peers_count,
                    peer_set=",".join(peer_keys) or "none",
                    sync_status=network_sync_status,
                    stable_for=stable_for,
                )
            )
            slow_methods = [
                "{}={:.3f}s".format(name, duration)
                for name, duration in snapshot["rpc_seconds"].items()
                if duration >= slow_rpc_threshold_seconds
            ]
            if slow_methods:
                print(
                    "[{}] SLOW_RPC elapsed={:.2f}s threshold={:.2f}s methods={}".format(
                        label,
                        elapsed,
                        slow_rpc_threshold_seconds,
                        ",".join(slow_methods),
                    )
                )

            if stable_for >= stable_seconds:
                summary = _build_summary(
                    state,
                    sample_finished,
                    start,
                    stable_seconds,
                    sample_interval_seconds,
                    slow_rpc_threshold_seconds,
                    "graph_stable",
                )
                state["summary"] = summary
                state["done"] = True
                _print_diagnostic_summary(summary)

        if all(state["done"] for state in states.values()):
            break

        now = time.monotonic()
        if now >= deadline:
            for state in states.values():
                if state["done"]:
                    continue
                summary = _build_summary(
                    state,
                    now,
                    start,
                    stable_seconds,
                    sample_interval_seconds,
                    slow_rpc_threshold_seconds,
                    "max_duration_reached",
                )
                state["summary"] = summary
                state["done"] = True
                _print_diagnostic_summary(summary)
            break

        time.sleep(min(sample_interval_seconds, max(0, deadline - now)))

    result = {label: state["summary"] for label, state in states.items()}
    print(
        "[sync_state] all targets finished: {}".format(
            {
                label: {
                    key: summary[key]
                    for key in (
                        "elapsed_seconds",
                        "final_graph_channels_count",
                        "final_graph_nodes_count",
                        "final_list_peers_count",
                        "last_change_elapsed_seconds",
                        "diagnostic_signals",
                        "reason",
                    )
                }
                for label, summary in result.items()
            }
        )
    )
    return result
