"""Supervise one backend process through start, readiness, and drain.

The supervisor owns the subprocess and the only stream of truth about it: its
stdout. Lines go through the game's observer and resolver into a
:class:`PresenceTracker`; readiness, save completion, version, and join
information are recorded from the same facts. It contains no policy about *when* to drain —
that is the caller's decision — but it owns the drain sequence itself,
because that is the sequence that protects a hundred-hour base.

Drain contract (Product §6.3):

1. request a save and wait for the game to confirm it;
2. verify the save files on disk;
3. only then ask the process to stop, and only then escalate to SIGKILL.

Any failure in steps 1-2 leaves the process and the files exactly as they
are. Nothing is killed and nothing is discarded; the caller is told why.
"""

import asyncio
import contextlib
import os
import signal
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import TextIO

from naust.agent.presence import PresenceTracker, PresenceTransition
from naust.games.facts import (
    BackendReady,
    BackendVersion,
    Fact,
    JoinInfo,
    Observer,
    Resolver,
    SaveCompleted,
)


class BackendState(StrEnum):
    STARTING = "STARTING"
    READY = "READY"
    DRAINING = "DRAINING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class BackendCommand:
    """How to launch the backend. ``env`` of ``None`` inherits the parent's."""

    argv: tuple[str, ...]
    cwd: Path | None = None
    env: Mapping[str, str] | None = None


@dataclass(frozen=True, slots=True)
class SaveFiles:
    """The files that must exist, and travel, together."""

    paths: tuple[Path, ...]

    def sizes(self) -> dict[Path, int | None]:
        return {path: path.stat().st_size if path.exists() else None for path in self.paths}


@dataclass(frozen=True, slots=True)
class DrainPolicy:
    """Signals and timeouts for the drain sequence.

    Valheim saves on graceful shutdown, so the save request *is* SIGINT; the
    process normally exits on its own after saving. ``stop_signal`` is only
    sent if it is still alive ``exit_grace`` after a verified save.
    """

    save_signal: signal.Signals = signal.SIGINT
    save_timeout: timedelta = timedelta(seconds=120)
    exit_grace: timedelta = timedelta(seconds=10)
    stop_signal: signal.Signals = signal.SIGTERM
    stop_timeout: timedelta = timedelta(seconds=30)
    kill_timeout: timedelta = timedelta(seconds=10)
    min_size_ratio: float = 0.5
    # Filesystem timestamps and the wall clock are not the same clock.
    mtime_tolerance: timedelta = timedelta(seconds=1)


class DrainOutcome(StrEnum):
    STOPPED = "STOPPED"
    KILLED = "KILLED"
    SAVE_TIMEOUT = "SAVE_TIMEOUT"
    SAVE_NOT_OBSERVED = "SAVE_NOT_OBSERVED"
    VERIFY_FAILED = "VERIFY_FAILED"


@dataclass(frozen=True, slots=True)
class DrainReport:
    outcome: DrainOutcome
    detail: str
    exit_code: int | None

    @property
    def succeeded(self) -> bool:
        return self.outcome in (DrainOutcome.STOPPED, DrainOutcome.KILLED)


class StartupFailed(RuntimeError):
    """The backend exited or stalled before announcing readiness."""

    def __init__(self, reason: str, exit_code: int | None, recent_output: tuple[str, ...]):
        super().__init__(reason)
        self.reason = reason
        self.exit_code = exit_code
        self.recent_output = recent_output


def verify_save(
    files: SaveFiles,
    previous_sizes: Mapping[Path, int | None],
    requested_at: float,
    policy: DrainPolicy,
) -> str | None:
    """Return why the save is not trustworthy, or ``None`` if it is.

    Pure so it can be tested with temporary files and fake clocks.
    """

    earliest = requested_at - policy.mtime_tolerance.total_seconds()
    for path in files.paths:
        if not path.exists():
            return f"{path.name} is missing"
        stat = path.stat()
        if stat.st_size == 0:
            return f"{path.name} is empty"
        previous = previous_sizes.get(path)
        if previous and stat.st_size < previous * policy.min_size_ratio:
            return f"{path.name} shrank from {previous} to {stat.st_size} bytes"
        if stat.st_mtime < earliest:
            return f"{path.name} was not written after the save request"
    return None


@dataclass(slots=True)
class _Signals:
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    saved: asyncio.Event = field(default_factory=asyncio.Event)
    eof: asyncio.Event = field(default_factory=asyncio.Event)


class BackendSupervisor:
    """One backend, one process, one stream of evidence."""

    def __init__(
        self,
        command: BackendCommand,
        observer: Observer,
        resolver: Resolver,
        save_files: SaveFiles,
        *,
        policy: DrainPolicy | None = None,
        tracker: PresenceTracker | None = None,
        on_transition: Callable[[PresenceTransition], None] | None = None,
        on_fact: Callable[[Fact], None] | None = None,
        recent_lines: int = 200,
        raw_log: TextIO | None = None,
    ) -> None:
        self.command = command
        self.policy = policy or DrainPolicy()
        self.tracker = tracker or PresenceTracker()
        self.save_files = save_files
        self.state = BackendState.STARTING
        self.join_info: JoinInfo | None = None
        self.version: str | None = None
        self.last_save_ms: float | None = None
        self.saves: int = 0
        self._observer = observer
        self._resolver = resolver
        self._on_transition = on_transition
        self._on_fact = on_fact
        self._raw_log = raw_log
        self._recent: deque[str] = deque(maxlen=recent_lines)
        self._process: asyncio.subprocess.Process | None = None
        self._pump: asyncio.Task[None] | None = None
        self._signals: _Signals | None = None

    # ---- observation -----------------------------------------------------

    @property
    def recent_output(self) -> tuple[str, ...]:
        return tuple(self._recent)

    @property
    def pid(self) -> int | None:
        return None if self._process is None else self._process.pid

    @property
    def exit_code(self) -> int | None:
        return None if self._process is None else self._process.returncode

    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.returncode is None

    # ---- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        if self._process is not None:
            raise RuntimeError("backend already started")
        self._signals = _Signals()
        env = None if self.command.env is None else dict(self.command.env)
        self._process = await asyncio.create_subprocess_exec(
            *self.command.argv,
            cwd=self.command.cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            limit=1 << 20,
        )
        self._pump = asyncio.create_task(self._pump_output(), name="naust-backend-output")

    async def write_stdin(self, text: str) -> None:
        """Send text to the backend. Valheim ignores stdin; test backends read it."""

        process = self._require_process()
        if process.stdin is None or process.stdin.is_closing():
            return
        process.stdin.write(text.encode())
        await process.stdin.drain()

    async def wait_ready(self, timeout: timedelta) -> None:
        """Block until the game reports ready, else raise :class:`StartupFailed`."""

        process = self._require_process()
        signals = self._require_signals()
        ready = asyncio.create_task(signals.ready.wait())
        exited = asyncio.create_task(process.wait())
        try:
            await asyncio.wait(
                {ready, exited},
                timeout=timeout.total_seconds(),
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for task in (ready, exited):
                task.cancel()
        if signals.ready.is_set():
            return
        await self._drain_output_if_exited()
        if process.returncode is not None:
            raise StartupFailed(
                f"backend exited with code {process.returncode} before ready",
                process.returncode,
                self.recent_output,
            )
        raise StartupFailed(
            f"backend not ready after {timeout.total_seconds():.0f}s",
            None,
            self.recent_output,
        )

    async def wait_exit(self) -> int:
        process = self._require_process()
        code = await process.wait()
        await self._drain_output_if_exited()
        return code

    async def drain(self) -> DrainReport:
        """Save, verify, then stop. Never the other way round."""

        process = self._require_process()
        signals = self._require_signals()
        self.state = BackendState.DRAINING

        if process.returncode is not None:
            return self._fail(
                DrainOutcome.SAVE_NOT_OBSERVED,
                f"backend had already exited with code {process.returncode}",
            )

        previous_sizes = self.save_files.sizes()
        signals.saved.clear()
        requested_at = time.time()
        self._send(self.policy.save_signal)

        saved = await self._wait_for_save_or_exit(self.policy.save_timeout)
        if saved is None:
            return self._fail(
                DrainOutcome.SAVE_TIMEOUT,
                f"no save confirmed within {self.policy.save_timeout.total_seconds():.0f}s; "
                "backend left running",
            )
        if not saved:
            return self._fail(
                DrainOutcome.SAVE_NOT_OBSERVED,
                f"backend exited with code {process.returncode} without confirming a save",
            )

        problem = verify_save(self.save_files, previous_sizes, requested_at, self.policy)
        if problem is not None:
            return self._fail(DrainOutcome.VERIFY_FAILED, f"save verification failed: {problem}")

        outcome = await self._stop_process()
        self.state = BackendState.STOPPED
        return DrainReport(outcome, "save verified", process.returncode)

    async def terminate(self) -> int | None:
        """Stop without saving. Only for a backend that never became ready."""

        process = self._require_process()
        if process.returncode is None:
            self._send(self.policy.stop_signal)
            try:
                await asyncio.wait_for(process.wait(), self.policy.stop_timeout.total_seconds())
            except TimeoutError:
                self._send(signal.SIGKILL)
                await asyncio.wait_for(process.wait(), self.policy.kill_timeout.total_seconds())
            await self._drain_output_if_exited()
        self.state = BackendState.FAILED
        return process.returncode

    # ---- internals -------------------------------------------------------

    async def _pump_output(self) -> None:
        process = self._require_process()
        signals = self._require_signals()
        assert process.stdout is not None
        try:
            while True:
                raw = await process.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                self._recent.append(line)
                if self._raw_log is not None:
                    self._raw_log.write(line + "\n")
                    self._raw_log.flush()
                observation = self._observer.parse_line(line)
                if observation is None:
                    continue
                for fact in self._resolver.resolve(observation):
                    self._handle(fact)
        finally:
            signals.eof.set()

    def _handle(self, fact: Fact) -> None:
        signals = self._require_signals()
        match fact:
            case BackendReady():
                if self.state is BackendState.STARTING:
                    self.state = BackendState.READY
                signals.ready.set()
            case SaveCompleted(duration_ms=duration_ms):
                self.last_save_ms = duration_ms
                self.saves += 1
                signals.saved.set()
            case JoinInfo():
                self.join_info = fact
            case BackendVersion(version=version):
                self.version = version
            case _:
                pass
        if self._on_fact is not None:
            self._on_fact(fact)
        transition = self.tracker.observe(fact)
        if transition is not None and self._on_transition is not None:
            self._on_transition(transition)

    async def _wait_for_save_or_exit(self, timeout: timedelta) -> bool | None:
        """True: save confirmed. False: output ended without one. None: timed out."""

        signals = self._require_signals()
        saved = asyncio.create_task(signals.saved.wait())
        eof = asyncio.create_task(signals.eof.wait())
        try:
            done, _ = await asyncio.wait(
                {saved, eof}, timeout=timeout.total_seconds(), return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            saved.cancel()
            eof.cancel()
        if signals.saved.is_set():
            return True
        if eof in done:
            await self._require_process().wait()
            return False
        return None

    async def _stop_process(self) -> DrainOutcome:
        process = self._require_process()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(process.wait(), self.policy.exit_grace.total_seconds())
        if process.returncode is not None:
            await self._drain_output_if_exited()
            return DrainOutcome.STOPPED
        self._send(self.policy.stop_signal)
        try:
            await asyncio.wait_for(process.wait(), self.policy.stop_timeout.total_seconds())
        except TimeoutError:
            self._send(signal.SIGKILL)
            await asyncio.wait_for(process.wait(), self.policy.kill_timeout.total_seconds())
            await self._drain_output_if_exited()
            return DrainOutcome.KILLED
        await self._drain_output_if_exited()
        return DrainOutcome.STOPPED

    def _send(self, sig: signal.Signals) -> None:
        process = self._require_process()
        if process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            process.send_signal(sig)

    def _fail(self, outcome: DrainOutcome, detail: str) -> DrainReport:
        self.state = BackendState.FAILED
        return DrainReport(outcome, detail, self.exit_code)

    async def _drain_output_if_exited(self) -> None:
        if self._pump is not None and not self._pump.done():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(asyncio.shield(self._pump), 5)

    def _require_process(self) -> asyncio.subprocess.Process:
        if self._process is None:
            raise RuntimeError("backend not started")
        return self._process

    def _require_signals(self) -> _Signals:
        if self._signals is None:
            raise RuntimeError("backend not started")
        return self._signals


def inherit_env(**overrides: str) -> dict[str, str]:
    """The parent's environment plus overrides, for :class:`BackendCommand`."""

    return {**os.environ, **overrides}
