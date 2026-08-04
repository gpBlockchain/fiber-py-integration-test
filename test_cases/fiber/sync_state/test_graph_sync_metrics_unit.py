from framework import graph_sync_metrics


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class SequencedClient:
    def __init__(self, snapshots, clock=None):
        self.snapshots = snapshots
        self.index = 0
        self.clock = clock

    @property
    def current(self):
        return self.snapshots[min(self.index, len(self.snapshots) - 1)]

    def advance(self, method_name):
        if self.clock is not None:
            self.clock.now += self.current.get(f"{method_name}_seconds", 0)

    def graph_channels(self, _params):
        self.advance("graph_channels")
        error = self.current.get("graph_channels_error")
        if error:
            raise Exception(error)
        return {"channels": [{}] * self.current["channels"]}

    def graph_nodes(self, _params):
        self.advance("graph_nodes")
        error = self.current.get("graph_nodes_error")
        if error:
            raise Exception(error)
        return {"nodes": [{}] * self.current["nodes"]}

    def list_peers(self):
        self.advance("list_peers")
        return {
            "peers": [{"pubkey": pubkey} for pubkey in self.current.get("peers", [])]
        }

    def node_info(self):
        self.advance("node_info")
        result = {
            "network_sync_status": self.current.get("status", "Running"),
            "version": "test-version",
            "commit_hash": "test-commit",
        }
        self.index += 1
        return result


def patch_clock(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(graph_sync_metrics.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(graph_sync_metrics.time, "sleep", clock.sleep)
    monkeypatch.setattr(
        graph_sync_metrics,
        "_utc_timestamp",
        lambda: "2026-08-04T00:00:00.000+00:00",
    )
    return clock


def test_diagnostics_show_graph_trickle_and_last_change(monkeypatch, capsys):
    patch_clock(monkeypatch)
    client = SequencedClient(
        [
            {"channels": 1, "nodes": 1, "peers": ["peer-a"]},
            {"channels": 4, "nodes": 2, "peers": ["peer-a"]},
            {"channels": 4, "nodes": 2, "peers": ["peer-a"]},
            {"channels": 4, "nodes": 2, "peers": ["peer-a"]},
        ]
    )

    summaries = graph_sync_metrics.sample_nodes_graph_sync_until_stable(
        [{"client": client, "label": "test_net"}],
        sample_interval_seconds=5,
        stable_seconds=10,
        max_duration_seconds=60,
    )

    summary = summaries["test_net"]
    assert summary["reason"] == "graph_stable"
    assert summary["elapsed_seconds"] == 15
    assert summary["last_change_elapsed_seconds"] == 5
    assert summary["stable_for_seconds"] == 10
    assert summary["graph_channels_delta"] == 3
    assert summary["graph_nodes_delta"] == 1
    assert summary["graph_change_events"] == 1
    assert summary["diagnostic_signals"] == ["graph_updates_trickled"]
    assert summary["samples"][1]["graph_channels_delta"] == 3
    assert "delta_channels=+3" in capsys.readouterr().out


def test_rpc_error_and_missing_peer_are_retained_in_summary(monkeypatch, capsys):
    patch_clock(monkeypatch)
    client = SequencedClient(
        [
            {
                "channels": 1,
                "nodes": 1,
                "peers": [],
                "graph_nodes_error": "temporary graph_nodes failure",
            },
            {"channels": 2, "nodes": 2, "peers": []},
            {"channels": 2, "nodes": 2, "peers": []},
        ]
    )

    summary = graph_sync_metrics.sample_nodes_graph_sync_until_stable(
        [{"client": client, "label": "test_net"}],
        sample_interval_seconds=5,
        stable_seconds=5,
        max_duration_seconds=30,
    )["test_net"]

    assert summary["reason"] == "graph_stable"
    assert summary["query_failure_count"] == 1
    assert summary["rpc_stats"]["graph_nodes"]["errors"] == 1
    assert summary["peer_unavailable_samples"] == 2
    assert "rpc_errors" in summary["diagnostic_signals"]
    assert "peer_unavailable" in summary["diagnostic_signals"]
    output = capsys.readouterr().out
    assert "QUERY_ERROR" in output
    assert "temporary graph_nodes failure" in output


def test_slow_rpc_is_timed_and_flagged(monkeypatch, capsys):
    clock = patch_clock(monkeypatch)
    client = SequencedClient(
        [
            {
                "channels": 2,
                "nodes": 2,
                "peers": ["peer-a"],
                "graph_channels_seconds": 3,
            }
        ],
        clock=clock,
    )

    summary = graph_sync_metrics.sample_nodes_graph_sync_until_stable(
        [{"client": client, "label": "test_net"}],
        sample_interval_seconds=5,
        stable_seconds=5,
        max_duration_seconds=30,
    )["test_net"]

    assert summary["rpc_stats"]["graph_channels"]["max_seconds"] == 3
    assert "slow_rpc" in summary["diagnostic_signals"]
    output = capsys.readouterr().out
    assert "SLOW_RPC" in output
    assert "graph_channels=3.000s" in output
