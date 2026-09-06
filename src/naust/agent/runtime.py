"""The Agent runtime: one world, one backend, from launch to verified drain.

Composes the supervisor, the status document, events and sinks, the local
surface, and the one policy the agent ships (idle drain), and speaks the
host contract: exit status, sd_notify, status, events, metrics.

In a single-node deployment there is no separate orchestrator, so the idle
timer lives here by default; a world with ``idle_timeout = null`` is in
orchestrator mode and the agent only reports and obeys.
"""

import asyncio
import contextlib
import signal
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TextIO

import structlog

from naust.agent import notify
from naust.agent.config import AgentConfig, SinkConfig
from naust.agent.events import Event, EventFactory
from naust.agent.files import marker_path, preflight, write_marker
from naust.agent.metrics import AgentMetrics
from naust.agent.presence import PresenceTracker, PresenceTransition
from naust.agent.sinks import DiscordSink, Dispatcher, Sink, WebhookSink
from naust.agent.status import AgentStatus, now_iso
from naust.agent.supervisor import (
    BackendCommand,
    BackendState,
    BackendSupervisor,
    DrainPolicy,
    SaveFiles,
    StartupFailed,
)
from naust.agent.surface import Surface
from naust.domain.world import WorldConfig
from naust.games.facts import BackendVersion, Fact, JoinInfo, SaveCompleted
from naust.games.profile import GameProfile
from naust.games.registry import get_profile

EXIT_OK = 0
EXIT_FAILED = 1


@dataclass(slots=True)
class _Session:
    """What one run of a backend cost, for the drain.finished summary."""

    started_monotonic: float = field(default_factory=time.monotonic)
    started_unix: float = field(default_factory=time.time)
    ready_monotonic: float | None = None
    peak_players: int = 0
    last_save_unix: float | None = None
    idle_since: float | None = None

    def summary(self, supervisor: BackendSupervisor) -> dict[str, Any]:
        return {
            "durationSeconds": round(time.monotonic() - self.started_monotonic),
            "peakPlayers": self.peak_players,
            "saves": supervisor.saves,
            "lastSaveDurationMs": supervisor.last_save_ms,
        }


class WorldRuntime:
    def __init__(
        self,
        world: WorldConfig,
        config: AgentConfig,
        *,
        profile: GameProfile | None = None,
        command: BackendCommand | None = None,
        files: SaveFiles | None = None,
        policy: DrainPolicy | None = None,
        stop: asyncio.Event | None = None,
        sinks: list[Sink] | None = None,
    ) -> None:
        self.world = world
        self.config = config
        self.profile = profile or get_profile(world.game)
        launch = config.backend
        self.stop = stop or asyncio.Event()
        self.stop_reason: str | None = None
        self.log = structlog.get_logger("naust.agent").bind(world=world.id, game=self.profile.name)
        self.status = AgentStatus(
            world=world.id,
            game=self.profile.name,
            capabilities=self.profile.capabilities.as_dict(),
            max_players=launch.max_players,
            save_files=files or self.profile.save_files(world, launch),
        )
        self.metrics = AgentMetrics()
        self.events = EventFactory.for_world(config.source_host, world.id)
        self.dispatcher = Dispatcher(sinks if sinks is not None else _build_sinks(config.sinks))
        self._raw_log: TextIO | None = _open_raw_log(config, world.id)
        self.supervisor = BackendSupervisor(
            (command or self.profile.build_command(world, launch)).wrapped(launch.wrapper),
            self.profile.observer(),
            self.profile.resolver(),
            self.status.save_files,
            policy=policy or self.profile.drain_policy(launch),
            tracker=PresenceTracker(max_players=launch.max_players),
            on_transition=self._on_transition,
            on_fact=self._on_fact,
            raw_log=self._raw_log,
        )
        socket_path = None
        if config.surface.socket_dir is not None:
            socket_path = config.surface.socket_dir / f"{world.id}.sock"
        tcp = None
        if config.surface.metrics_port is not None:
            tcp = (config.surface.metrics_host, config.surface.metrics_port)
        self.surface = Surface(
            self.status,
            self.metrics,
            request_drain=self.request_stop,
            save_kind=self.profile.save.kind,
            socket_path=socket_path,
            tcp=tcp,
        )
        self.session = _Session()
        self.started = asyncio.Event()
        self._marker = marker_path(config.state_dir, world.id)

    # ---- contract in -----------------------------------------------------

    def request_stop(self, reason: str) -> None:
        if not self.stop.is_set():
            self.stop_reason = reason
            self.stop.set()

    # ---- lifecycle -------------------------------------------------------

    async def run(self) -> int:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.request_stop, "signal")
        await self.dispatcher.start()
        await self.surface.start()
        self.started.set()
        try:
            return await self._run()
        finally:
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.remove_signal_handler(sig)
            await self.surface.stop()
            await self.dispatcher.close(self.config.event_flush_timeout.total_seconds())
            if self._raw_log is not None:
                self._raw_log.close()

    async def _run(self) -> int:
        sup = self.supervisor
        problem = preflight(sup.save_files, self._marker)
        if problem is not None:
            self._fail("backend.refused", reason=problem, marker=str(self._marker))
            return EXIT_FAILED

        self._emit(
            "backend.starting",
            argv=_redacted(sup.command.argv),
            profile=self.profile.name,
            capabilities=self.status.capabilities,
        )
        await sup.start()
        self.status.pid = sup.pid
        self.status.started_at = now_iso()
        self._sync()
        try:
            await sup.wait_ready(self.config.backend.ready_timeout)
        except StartupFailed as failure:
            self._fail(
                "backend.failed",
                reason=failure.reason,
                exit_code=failure.exit_code,
                recent_output=list(failure.recent_output),
            )
            await sup.terminate()
            self._sync()
            return EXIT_FAILED

        self.session.ready_monotonic = time.monotonic()
        self.session.idle_since = self.session.ready_monotonic
        self.status.idle_since = now_iso()  # empty since ready, until a transition says otherwise
        self.status.set_condition("Ready", "True", "BackendReady")
        self._sync()
        notify.ready()
        notify.status("ready, 0 players")
        self._emit(
            "backend.ready",
            pid=sup.pid,
            version=sup.version,
            startupSeconds=round(self.session.ready_monotonic - self.session.started_monotonic, 1),
        )

        idle = asyncio.create_task(self._idle_watch(), name="naust-idle")
        stop_requested = asyncio.create_task(self.stop.wait(), name="naust-stop")
        exited = asyncio.create_task(sup.wait_exit(), name="naust-exit")
        try:
            await asyncio.wait({stop_requested, exited}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (idle, stop_requested):
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await idle

        if exited.done() and not self.stop.is_set():
            self.status.state = BackendState.FAILED
            self.status.set_condition("Ready", "False", "BackendExited")
            self._fail(
                "backend.exited", exit_code=exited.result(), recent_output=list(sup.recent_output)
            )
            return EXIT_FAILED
        exited.cancel()
        return await self._drain()

    async def _drain(self) -> int:
        sup = self.supervisor
        trigger = self.stop_reason or "external"
        self.status.set_condition("Draining", "True", trigger)
        self.status.set_condition("Ready", "False", "Draining")
        self.status.state = BackendState.DRAINING
        self._sync()
        notify.stopping()
        notify.extend_timeout(self.supervisor.policy.save_timeout.total_seconds() + 60)
        self._emit("drain.started", trigger=trigger, players=sorted(sup.tracker.snapshot.players))

        report = await sup.drain()
        if report.succeeded:
            write_marker(sup.save_files, self._marker)
            self.status.set_condition("SaveVerified", "True", "DrainSave")
        else:
            self.status.set_condition("SaveVerified", "False", report.outcome.value)
        self.status.set_condition("Draining", "False", report.outcome.value)
        self.status.state = sup.state
        self._sync()
        self._emit(
            "drain.finished",
            succeeded=report.succeeded,
            outcome=report.outcome.value,
            detail=report.detail,
            exit_code=report.exit_code,
            backend_alive=sup.alive,
            session=self.session.summary(sup),
            files=self.status.file_sizes(),
            level="info" if report.succeeded else "error",
        )
        return EXIT_OK if report.succeeded else EXIT_FAILED

    async def _idle_watch(self) -> None:
        world, profile = self.world, self.profile
        if world.idle_timeout is None:
            return  # orchestrator mode: report and obey, never decide
        grace = max(world.connection_grace_period, profile.minimum_connection_grace).total_seconds()
        idle_timeout = world.idle_timeout.total_seconds()
        interval = self.config.idle_check_interval.total_seconds()
        ready_at = self.session.ready_monotonic or time.monotonic()
        while not self.stop.is_set():
            await asyncio.sleep(interval)
            now = time.monotonic()
            idle_since = self.session.idle_since
            if self.supervisor.tracker.count > 0 or idle_since is None:
                continue
            if now - ready_at < grace:
                continue
            if now - idle_since >= idle_timeout:
                self.log.info("idle.timeout", idle_seconds=round(now - idle_since))
                self.request_stop("idle")
                return

    # ---- facts in --------------------------------------------------------

    def _on_transition(self, transition: PresenceTransition) -> None:
        self.session.idle_since = None if transition.count > 0 else time.monotonic()
        self.session.peak_players = max(self.session.peak_players, transition.count)
        self.status.apply_transition(transition)
        self._sync()
        notify.status(f"ready, {transition.count} players")
        self._emit(
            "presence.changed",
            count=transition.count,
            players=sorted(transition.after.players),
            joined=sorted(transition.joined),
            left=sorted(transition.left),
            quality=self.status.capabilities.get("presence"),
        )

    def _on_fact(self, fact: Fact) -> None:
        match fact:
            case SaveCompleted(duration_ms=duration_ms):
                self.session.last_save_unix = time.time()
                self.status.note_save(duration_ms)
                if duration_ms is not None:
                    self.metrics.save_duration.labels(self.world.id).observe(duration_ms / 1000)
                self._sync()
                self._emit(
                    "save.completed", duration_ms=duration_ms, files=self.status.file_sizes()
                )
            case JoinInfo(code=code, address=address, port=port):
                self.status.join = fact
                self._sync()
                self._emit("backend.join", kind=fact.kind, code=code, address=address, port=port)
            case BackendVersion(version=version):
                self.status.version = version
                self.status.set_condition("VersionKnown", "True", "LogLine")
                self._sync()
                self._emit("backend.version", version=version)
            case _:
                pass

    # ---- contract out ----------------------------------------------------

    def _sync(self) -> None:
        if self.status.state is not BackendState.FAILED:
            self.status.state = self.supervisor.state
        self.metrics.update(
            self.status,
            last_save_unix=self.session.last_save_unix,
            start_unix=self.session.started_unix,
        )

    def _emit(self, type_: str, *, level: str = "info", **data: Any) -> None:
        data = {"world": self.world.id, **data}
        sequence = self.status.bump()
        cloudevent = self.events.cloudevent(Event(type_, data), sequence)
        self.dispatcher.publish(cloudevent)
        self.metrics.events.labels(self.world.id, type_).inc()
        for sink in self.dispatcher.sinks:
            self.metrics.delivery_failures.labels(self.world.id, sink.name).set(
                self.dispatcher.failed[sink.name]
            )
            self.metrics.events_dropped.labels(self.world.id, sink.name).set(
                self.dispatcher.dropped[sink.name]
            )
        getattr(self.log, level)(
            type_, sequence=sequence, **{k: v for k, v in data.items() if k != "world"}
        )

    def _fail(self, type_: str, **data: Any) -> None:
        self.status.state = BackendState.FAILED
        self._sync()
        self._emit(type_, level="error", **data)


def _open_raw_log(config: AgentConfig, world_id: str) -> TextIO | None:
    if config.raw_log_dir is None:
        return None
    config.raw_log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return (config.raw_log_dir / f"{world_id}-{stamp}.log").open("a", encoding="utf-8")


def _build_sinks(configs: tuple[SinkConfig, ...]) -> list[Sink]:
    sinks: list[Sink] = []
    for index, config in enumerate(configs):
        name = f"{config.kind}-{index}" if index else config.kind
        if config.kind == "webhook":
            sinks.append(WebhookSink(config.resolve_url(), config.resolve_token(), name=name))
        else:
            sinks.append(DiscordSink(config.resolve_url(), name=name))
    return sinks


def _redacted(argv: tuple[str, ...]) -> list[str]:
    """Hide the value that follows ``-password`` in logged command lines."""

    out: list[str] = []
    hide_next = False
    for arg in argv:
        out.append("***" if hide_next else arg)
        hide_next = arg == "-password"
    return out


async def run_world(
    world: WorldConfig,
    config: AgentConfig,
    *,
    profile: GameProfile | None = None,
    command: BackendCommand | None = None,
    files: SaveFiles | None = None,
    policy: DrainPolicy | None = None,
    stop: asyncio.Event | None = None,
    sinks: list[Sink] | None = None,
) -> int:
    """Supervise one world until it drains. Returns a process exit code."""

    runtime = WorldRuntime(
        world,
        config,
        profile=profile,
        command=command,
        files=files,
        policy=policy,
        stop=stop,
        sinks=sinks,
    )
    return await runtime.run()
