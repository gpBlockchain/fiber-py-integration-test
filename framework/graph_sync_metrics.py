import os
import time

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


def sample_nodes_graph_sync_until_stable(
    targets,
    sample_interval_seconds=5,
    stable_seconds=60,
    max_duration_seconds=7200,
):
    """Poll multiple fiber nodes until each graph stops growing.

    For every target, query list_peers / graph_channels / graph_nodes every
    ``sample_interval_seconds``. When both graph_channels and graph_nodes
    counts are unchanged for ``stable_seconds``, that target is considered
    synced and its final counts + elapsed time are recorded.

    Args:
        targets: iterable of dicts with keys:
            - client: FiberRPCClient
            - label: str, e.g. "main_net" / "test_net"
        sample_interval_seconds: poll interval (default 5s)
        stable_seconds: no graph growth window to declare done (default 60s)
        max_duration_seconds: hard stop safety net

    Returns:
        dict label -> summary
    """
    start = time.time()
    deadline = start + max_duration_seconds
    states = {}
    for target in targets:
        label = target["label"]
        states[label] = {
            "client": target["client"],
            "label": label,
            "done": False,
            "last_channels": None,
            "last_nodes": None,
            "last_peers": 0,
            "last_change_at": start,
            "samples": [],
            "summary": None,
        }

    while True:
        now = time.time()
        elapsed = now - start
        all_done = True

        for label, state in states.items():
            if state["done"]:
                continue
            all_done = False
            client = state["client"]
            try:
                channels_count, nodes_count = _graph_counts(client)
                peers_count = _list_peers_count(client)
            except Exception as exc:
                print(
                    "[{}] elapsed={:.2f}s query failed: {}".format(label, elapsed, exc)
                )
                continue

            sample = {
                "elapsed_seconds": round(elapsed, 3),
                "graph_channels_count": channels_count,
                "graph_nodes_count": nodes_count,
                "list_peers_count": peers_count,
            }
            state["samples"].append(sample)
            state["last_peers"] = peers_count
            _print_sample(
                label,
                elapsed,
                channels_count,
                nodes_count,
                peers_count,
            )

            graph_changed = (
                state["last_channels"] != channels_count
                or state["last_nodes"] != nodes_count
            )
            if state["last_channels"] is None or graph_changed:
                state["last_channels"] = channels_count
                state["last_nodes"] = nodes_count
                state["last_change_at"] = now
                continue

            if now - state["last_change_at"] >= stable_seconds:
                summary = {
                    "label": label,
                    "elapsed_seconds": round(elapsed, 3),
                    "stable_seconds": stable_seconds,
                    "sample_interval_seconds": sample_interval_seconds,
                    "final_graph_channels_count": channels_count,
                    "final_graph_nodes_count": nodes_count,
                    "final_list_peers_count": peers_count,
                    "samples": state["samples"],
                    "reason": "graph_stable",
                }
                state["summary"] = summary
                state["done"] = True
                print(
                    "[{}] stable for {}s, final graph_channels={} "
                    "graph_nodes={} list_peers={} elapsed={:.2f}s".format(
                        label,
                        stable_seconds,
                        channels_count,
                        nodes_count,
                        peers_count,
                        elapsed,
                    )
                )

        if all_done:
            break
        if now >= deadline:
            for label, state in states.items():
                if state["done"]:
                    continue
                summary = {
                    "label": label,
                    "elapsed_seconds": round(now - start, 3),
                    "stable_seconds": stable_seconds,
                    "sample_interval_seconds": sample_interval_seconds,
                    "final_graph_channels_count": state["last_channels"] or 0,
                    "final_graph_nodes_count": state["last_nodes"] or 0,
                    "final_list_peers_count": state["last_peers"],
                    "samples": state["samples"],
                    "reason": "max_duration_reached",
                }
                state["summary"] = summary
                state["done"] = True
                print(
                    "[{}] max duration {}s reached, final graph_channels={} "
                    "graph_nodes={} list_peers={} elapsed={:.2f}s".format(
                        label,
                        max_duration_seconds,
                        summary["final_graph_channels_count"],
                        summary["final_graph_nodes_count"],
                        summary["final_list_peers_count"],
                        summary["elapsed_seconds"],
                    )
                )
            break

        time.sleep(sample_interval_seconds)

    result = {label: state["summary"] for label, state in states.items()}
    print("[sync_state] all targets finished: {}".format(result))
    return result
