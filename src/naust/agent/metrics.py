"""Prometheus metrics derived from the status document."""

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    Info,
    generate_latest,
)

from naust import __version__
from naust.agent.status import AgentStatus
from naust.agent.supervisor import BackendState

CONTENT_TYPE = CONTENT_TYPE_LATEST


class AgentMetrics:
    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()
        r = self.registry
        self.state = Gauge(
            "naust_backend_state", "1 for the current state", ["world", "state"], registry=r
        )
        self.players = Gauge("naust_players", "players present", ["world"], registry=r)
        self.ready = Gauge("naust_backend_ready", "1 when accepting players", ["world"], registry=r)
        self.last_save = Gauge(
            "naust_last_save_timestamp_seconds", "unix time of the last save", ["world"], registry=r
        )
        self.start_time = Gauge(
            "naust_backend_start_timestamp_seconds",
            "unix time the backend started",
            ["world"],
            registry=r,
        )
        self.file_bytes = Gauge(
            "naust_world_file_bytes", "size of each world file", ["world", "file"], registry=r
        )
        self.save_duration = Histogram(
            "naust_save_duration_seconds",
            "time the game reported for a save",
            ["world"],
            buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30),
            registry=r,
        )
        self.events = Counter("naust_events_total", "events emitted", ["world", "type"], registry=r)
        self.delivery_failures = Gauge(
            "naust_event_delivery_failures",
            "events a sink failed to deliver after retries",
            ["world", "sink"],
            registry=r,
        )
        self.events_dropped = Gauge(
            "naust_events_dropped",
            "events dropped because a sink's queue was full",
            ["world", "sink"],
            registry=r,
        )
        self.build = Info("naust_build", "naust and game versions", ["world"], registry=r)

    def update(
        self, status: AgentStatus, *, last_save_unix: float | None, start_unix: float | None
    ) -> None:
        w = status.world
        for state in BackendState:
            self.state.labels(w, state.value).set(1 if status.state is state else 0)
        self.players.labels(w).set(status.count)
        self.ready.labels(w).set(1 if status.conditions["Ready"].status == "True" else 0)
        if last_save_unix is not None:
            self.last_save.labels(w).set(last_save_unix)
        if start_unix is not None:
            self.start_time.labels(w).set(start_unix)
        for entry in status.file_sizes():
            if entry["bytes"] is not None:
                self.file_bytes.labels(w, entry["path"]).set(entry["bytes"])
        self.build.labels(w).info(
            {
                "version": __version__,
                "game": status.game,
                "game_version": status.version or "unknown",
            }
        )

    def render(self) -> bytes:
        return generate_latest(self.registry)
