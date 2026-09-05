"""The supervisor against a fake backend that can misbehave in every required way."""

import asyncio
import signal
import sys
import time
from datetime import timedelta
from pathlib import Path

import pytest

from naust.agent.presence import PresenceTransition
from naust.agent.supervisor import (
    BackendCommand,
    BackendState,
    BackendSupervisor,
    DrainOutcome,
    DrainPolicy,
    SaveFiles,
    StartupFailed,
    verify_save,
)
from naust.agent.valheim import ValheimAdapter

FAKE_BACKEND = Path(__file__).parent / "fake_backend.py"
FAST = DrainPolicy(
    save_timeout=timedelta(seconds=3),
    exit_grace=timedelta(seconds=2),
    stop_timeout=timedelta(seconds=1),
    kill_timeout=timedelta(seconds=3),
)
READY_TIMEOUT = timedelta(seconds=10)


def fake_backend(save_dir: Path, *args: str) -> BackendCommand:
    return BackendCommand(
        argv=(sys.executable, str(FAKE_BACKEND), "--save-dir", str(save_dir), *args),
    )


def save_files(save_dir: Path, world: str = "testworld") -> SaveFiles:
    worlds = save_dir / "worlds_local"
    return SaveFiles((worlds / f"{world}.db", worlds / f"{world}.fwl"))


def supervisor(tmp_path: Path, *args: str, **kwargs) -> BackendSupervisor:
    return BackendSupervisor(
        fake_backend(tmp_path, *args),
        ValheimAdapter(),
        save_files(tmp_path),
        policy=FAST,
        **kwargs,
    )


async def until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


# ---- startup ---------------------------------------------------------------


async def test_start_and_ready(tmp_path: Path) -> None:
    sup = supervisor(tmp_path)
    await sup.start()
    assert sup.state is BackendState.STARTING

    await sup.wait_ready(READY_TIMEOUT)

    assert sup.state is BackendState.READY
    assert sup.alive
    assert any("Game server connected" in line for line in sup.recent_output)
    report = await sup.drain()
    assert report.succeeded


async def test_exit_before_ready_reports_last_output(tmp_path: Path) -> None:
    sup = supervisor(tmp_path, "--behaviour", "crash-before-ready")
    await sup.start()

    with pytest.raises(StartupFailed) as failure:
        await sup.wait_ready(READY_TIMEOUT)

    assert failure.value.exit_code == 3
    assert any("Failed to load world" in line for line in failure.value.recent_output)
    assert not sup.alive


async def test_ready_timeout_is_a_startup_failure(tmp_path: Path) -> None:
    sup = supervisor(tmp_path, "--ready-delay", "5")
    await sup.start()

    with pytest.raises(StartupFailed, match="not ready"):
        await sup.wait_ready(timedelta(milliseconds=300))

    assert sup.alive
    await sup.terminate()
    assert not sup.alive
    assert sup.state is BackendState.FAILED


async def test_start_twice_is_refused(tmp_path: Path) -> None:
    sup = supervisor(tmp_path)
    await sup.start()
    with pytest.raises(RuntimeError, match="already started"):
        await sup.start()
    await sup.drain()


async def test_methods_before_start_are_refused(tmp_path: Path) -> None:
    sup = supervisor(tmp_path)
    with pytest.raises(RuntimeError, match="not started"):
        await sup.drain()
    with pytest.raises(RuntimeError, match="not started"):
        await sup.wait_ready(READY_TIMEOUT)


# ---- running ---------------------------------------------------------------


async def test_output_feeds_presence_and_callbacks(tmp_path: Path) -> None:
    transitions: list[PresenceTransition] = []
    sup = supervisor(tmp_path, on_transition=transitions.append)
    await sup.start()
    await sup.wait_ready(READY_TIMEOUT)

    await sup.write_stdin("join Alice 5\n")
    await until(lambda: sup.tracker.count == 1)
    await sup.write_stdin("die Alice\njoin Alice 5\njoin Bob 6\n")
    await until(lambda: sup.tracker.count == 2)
    await sup.write_stdin("leave 5 9\n")
    await until(lambda: sup.tracker.count == 1)
    await sup.write_stdin("autosave\n")
    await until(lambda: sup.last_save_ms is not None)

    assert [t.count for t in transitions] == [1, 2, 1]
    assert sup.tracker.snapshot.players == {"Bob"}
    assert sup.last_save_ms == 5.0
    await sup.drain()


async def test_recent_output_is_bounded(tmp_path: Path) -> None:
    sup = supervisor(tmp_path, recent_lines=5)
    await sup.start()
    await sup.wait_ready(READY_TIMEOUT)
    await sup.write_stdin("".join(f"say line {i}\n" for i in range(20)))
    await until(lambda: "line 19" in sup.recent_output[-1])

    assert len(sup.recent_output) == 5
    assert sup.recent_output[0] == "line 15"
    await sup.drain()


async def test_crash_after_ready_is_observable(tmp_path: Path) -> None:
    sup = supervisor(tmp_path, "--behaviour", "crash-after-ready")
    await sup.start()
    await sup.wait_ready(READY_TIMEOUT)

    assert await sup.wait_exit() == 4
    assert not sup.alive
    report = await sup.drain()
    assert report.outcome is DrainOutcome.SAVE_NOT_OBSERVED
    assert sup.state is BackendState.FAILED


# ---- drain -----------------------------------------------------------------


async def test_clean_drain_saves_verifies_and_stops(tmp_path: Path) -> None:
    sup = supervisor(tmp_path)
    await sup.start()
    await sup.wait_ready(READY_TIMEOUT)
    await sup.write_stdin("autosave\n")
    await until(lambda: sup.last_save_ms is not None)
    first_mtime = save_files(tmp_path).paths[0].stat().st_mtime

    report = await sup.drain()

    assert report.outcome is DrainOutcome.STOPPED
    assert report.succeeded
    assert report.exit_code == 0
    assert sup.state is BackendState.STOPPED
    assert sup.last_save_ms == 61.499
    assert save_files(tmp_path).paths[0].stat().st_mtime >= first_mtime
    assert not sup.alive


async def test_drain_kills_only_after_verified_save(tmp_path: Path) -> None:
    sup = supervisor(tmp_path, "--behaviour", "ignore-term")
    await sup.start()
    await sup.wait_ready(READY_TIMEOUT)

    report = await sup.drain()

    assert report.outcome is DrainOutcome.KILLED
    assert report.succeeded
    assert report.exit_code == -signal.SIGKILL
    for path in save_files(tmp_path).paths:
        assert path.stat().st_size > 0


async def test_save_timeout_leaves_backend_running(tmp_path: Path) -> None:
    sup = supervisor(tmp_path, "--behaviour", "hang-after-ready")
    await sup.start()
    await sup.wait_ready(READY_TIMEOUT)

    report = await sup.drain()

    assert report.outcome is DrainOutcome.SAVE_TIMEOUT
    assert not report.succeeded
    assert sup.state is BackendState.FAILED
    assert sup.alive, "a hung backend must not be killed without a verified save"
    assert not save_files(tmp_path).paths[0].exists()
    await sup.terminate()


async def test_exit_without_save_is_a_failure_and_discards_nothing(tmp_path: Path) -> None:
    sup = supervisor(tmp_path, "--behaviour", "no-save")
    await sup.start()
    await sup.wait_ready(READY_TIMEOUT)
    await sup.write_stdin("autosave\n")
    await until(lambda: sup.last_save_ms is not None)
    before = {p: p.read_bytes() for p in save_files(tmp_path).paths}

    report = await sup.drain()

    assert report.outcome is DrainOutcome.SAVE_NOT_OBSERVED
    assert report.exit_code == 0
    assert {p: p.read_bytes() for p in save_files(tmp_path).paths} == before


async def test_corrupt_save_fails_verification(tmp_path: Path) -> None:
    sup = supervisor(tmp_path, "--behaviour", "corrupt-save")
    await sup.start()
    await sup.wait_ready(READY_TIMEOUT)

    report = await sup.drain()

    assert report.outcome is DrainOutcome.VERIFY_FAILED
    assert "is empty" in report.detail
    assert sup.state is BackendState.FAILED


async def test_shrunken_save_fails_verification(tmp_path: Path) -> None:
    sup = supervisor(tmp_path, "--save-size", "100")
    await sup.start()
    await sup.wait_ready(READY_TIMEOUT)
    # A previous, much larger save exists on disk.
    save_files(tmp_path).paths[0].parent.mkdir(parents=True, exist_ok=True)
    save_files(tmp_path).paths[0].write_bytes(b"x" * 100_000)

    report = await sup.drain()

    assert report.outcome is DrainOutcome.VERIFY_FAILED
    assert "shrank" in report.detail


async def test_slow_save_within_timeout_succeeds(tmp_path: Path) -> None:
    sup = supervisor(tmp_path, "--behaviour", "slow-save", "--slow-save-seconds", "0.5")
    await sup.start()
    await sup.wait_ready(READY_TIMEOUT)

    report = await sup.drain()

    assert report.outcome is DrainOutcome.STOPPED


# ---- verify_save in isolation -------------------------------------------------


def _write(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


def test_verify_save_accepts_fresh_complete_files(tmp_path: Path) -> None:
    files = save_files(tmp_path)
    requested_at = time.time()
    for path in files.paths:
        _write(path, 1000)

    assert verify_save(files, {p: 900 for p in files.paths}, requested_at, FAST) is None


@pytest.mark.parametrize(
    ("setup", "expected"),
    [
        (lambda files: None, "is missing"),
        (lambda files: [_write(p, 0) for p in files.paths], "is empty"),
        (lambda files: [_write(p, 10) for p in files.paths], "shrank"),
    ],
)
def test_verify_save_rejects(tmp_path: Path, setup, expected: str) -> None:
    files = save_files(tmp_path)
    requested_at = time.time()
    setup(files)

    problem = verify_save(files, dict.fromkeys(files.paths, 1000), requested_at, FAST)

    assert problem is not None
    assert expected in problem


def test_verify_save_rejects_stale_files(tmp_path: Path) -> None:
    files = save_files(tmp_path)
    for path in files.paths:
        _write(path, 1000)
    requested_at = time.time() + 60

    problem = verify_save(files, {}, requested_at, FAST)

    assert problem is not None
    assert "not written after" in problem


def test_verify_save_ignores_ratio_without_previous(tmp_path: Path) -> None:
    files = save_files(tmp_path)
    for path in files.paths:
        _write(path, 1)

    assert verify_save(files, {}, time.time(), FAST) is None
