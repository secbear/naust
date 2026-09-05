"""The Agent runtime: one world, one backend, from launch to verified drain.

In a single-node deployment there is no separate Control process, so the
Agent also owns the idle timer described in Product §6.2. That decision is
recorded in ``docs/decisions/0003-single-node-agent.md``.
"""

import asyncio
import contextlib
import signal
import time
from dataclasses import dataclass, field

import structlog

from naust.agent.config import AgentConfig
from naust.agent.files import marker_path, preflight, write_marker
from naust.agent.presence import PresenceTracker, PresenceTransition
from naust.agent.supervisor import (
    BackendCommand,
    BackendSupervisor,
    DrainPolicy,
    SaveFiles,
    StartupFailed,
)
from naust.domain.world import WorldConfig
from naust.games.facts import BackendVersion, Fact, JoinInfo, SaveCompleted
from naust.games.profile import GameProfile
from naust.games.registry import get_profile

EXIT_OK = 0
EXIT_FAILED = 1


@dataclass(slots=True)
class _IdleClock:
    """When did the world last have nobody in it? ``None`` means somebody is here."""

    idle_since: float | None = field(default_factory=time.monotonic)

    def note(self, transition: PresenceTransition) -> None:
        self.idle_since = None if transition.count > 0 else time.monotonic()


async def run_world(
    world: WorldConfig,
    config: AgentConfig,
    *,
    profile: GameProfile | None = None,
    command: BackendCommand | None = None,
    files: SaveFiles | None = None,
    policy: DrainPolicy | None = None,
    stop: asyncio.Event | None = None,
) -> int:
    """Supervise one world until it drains. Returns a process exit code.

    ``stop`` is the operator's drain request; SIGTERM and SIGINT set it too.
    The idle timer sets it once the world has been empty for
    ``world.idle_timeout`` after the connection grace period has passed. The
    grace period is the larger of the world's own and the profile's minimum
    for inferred presence.
    """

    profile = profile or get_profile(world.game)
    log = structlog.get_logger("naust.agent").bind(world=world.id, game=profile.name)
    launch = config.backend
    stop = stop or asyncio.Event()
    clock = _IdleClock()

    def on_transition(transition: PresenceTransition) -> None:
        clock.note(transition)
        log.info(
            "presence.changed",
            count=transition.count,
            players=sorted(transition.after.players),
            joined=sorted(transition.joined),
            left=sorted(transition.left),
        )

    def on_fact(fact: Fact) -> None:
        match fact:
            case SaveCompleted(duration_ms=duration_ms):
                log.info("backend.saved", duration_ms=duration_ms)
            case JoinInfo(code=code, address=address, port=port):
                log.info("backend.join", kind=fact.kind, code=code, address=address, port=port)
            case BackendVersion(version=version):
                log.info("backend.version", version=version)
            case _:
                pass

    supervisor = BackendSupervisor(
        command or profile.build_command(world, launch),
        profile.observer(),
        profile.resolver(),
        files or profile.save_files(world, launch),
        policy=policy or profile.drain_policy(launch),
        tracker=PresenceTracker(max_players=launch.max_players),
        on_transition=on_transition,
        on_fact=on_fact,
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        return await _supervise(supervisor, world, profile, config, clock, stop, log)
    finally:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)


async def _supervise(
    supervisor: BackendSupervisor,
    world: WorldConfig,
    profile: GameProfile,
    config: AgentConfig,
    clock: _IdleClock,
    stop: asyncio.Event,
    log: structlog.stdlib.BoundLogger,
) -> int:
    marker = marker_path(config.state_dir, world.id)
    problem = preflight(supervisor.save_files, marker)
    if problem is not None:
        log.error("backend.refused", reason=problem, marker=str(marker))
        return EXIT_FAILED

    log.info("backend.starting", argv=_redacted(supervisor.command.argv))
    await supervisor.start()
    try:
        await supervisor.wait_ready(config.backend.ready_timeout)
    except StartupFailed as failure:
        log.error(
            "backend.startup_failed",
            reason=failure.reason,
            exit_code=failure.exit_code,
            recent_output=list(failure.recent_output),
        )
        await supervisor.terminate()
        return EXIT_FAILED
    ready_at = time.monotonic()
    clock.idle_since = ready_at
    log.info(
        "backend.ready",
        pid=supervisor.pid,
        version=supervisor.version,
        join=None if supervisor.join_info is None else supervisor.join_info.code,
    )

    idle = asyncio.create_task(
        _idle_watch(supervisor, world, profile, config, clock, ready_at, stop, log),
        name="naust-idle",
    )
    stop_requested = asyncio.create_task(stop.wait(), name="naust-stop")
    exited = asyncio.create_task(supervisor.wait_exit(), name="naust-exit")
    try:
        await asyncio.wait({stop_requested, exited}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (idle, stop_requested):
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await idle

    if exited.done() and not stop.is_set():
        log.error(
            "backend.exited",
            exit_code=exited.result(),
            recent_output=list(supervisor.recent_output),
        )
        return EXIT_FAILED
    exited.cancel()

    log.info("drain.starting", players=sorted(supervisor.tracker.snapshot.players))
    report = await supervisor.drain()
    if report.succeeded:
        write_marker(supervisor.save_files, marker)
    event = log.info if report.succeeded else log.error
    event(
        "drain.finished",
        outcome=report.outcome,
        detail=report.detail,
        exit_code=report.exit_code,
        backend_alive=supervisor.alive,
    )
    return EXIT_OK if report.succeeded else EXIT_FAILED


async def _idle_watch(
    supervisor: BackendSupervisor,
    world: WorldConfig,
    profile: GameProfile,
    config: AgentConfig,
    clock: _IdleClock,
    ready_at: float,
    stop: asyncio.Event,
    log: structlog.stdlib.BoundLogger,
) -> None:
    if world.idle_timeout is None:
        return  # orchestrator mode: report and obey, never decide
    grace = max(world.connection_grace_period, profile.minimum_connection_grace).total_seconds()
    idle_timeout = world.idle_timeout.total_seconds()
    interval = config.idle_check_interval.total_seconds()
    while not stop.is_set():
        await asyncio.sleep(interval)
        now = time.monotonic()
        if supervisor.tracker.count > 0 or clock.idle_since is None:
            continue
        if now - ready_at < grace:
            continue
        if now - clock.idle_since >= idle_timeout:
            log.info("idle.timeout", idle_seconds=round(now - clock.idle_since))
            stop.set()
            return


def _redacted(argv: tuple[str, ...]) -> list[str]:
    """Hide the value that follows ``-password`` in logged command lines."""

    out: list[str] = []
    hide_next = False
    for arg in argv:
        out.append("***" if hide_next else arg)
        hide_next = arg == "-password"
    return out


def run_agent(config: AgentConfig) -> None:
    """Project 0 stub retained for ``naust agent`` without ``--world``."""
