"""The Agent runtime end to end against the fake backend."""

import asyncio
import dataclasses
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr

from naust.agent.config import AgentConfig, BackendLaunchConfig
from naust.agent.service import EXIT_FAILED, EXIT_OK, run_world
from naust.agent.supervisor import BackendCommand, DrainPolicy
from naust.domain.world import CrossplayWorldConfig, SteamDirectWorldConfig
from naust.games import registry
from naust.games.valheim import profile as valheim

FAKE_BACKEND = Path(__file__).parent / "fake_backend.py"


def world(idle: float = 0.6, grace: float = 0.3) -> CrossplayWorldConfig:
    return CrossplayWorldConfig(
        id="testworld",
        name="Test World",
        owner="tests",
        idle_timeout=timedelta(seconds=idle),
        connection_grace_period=timedelta(seconds=grace),
    )


def config(tmp_path: Path) -> AgentConfig:
    return AgentConfig(
        state_dir=tmp_path / "state",
        backend=BackendLaunchConfig(
            save_dir=tmp_path,
            ready_timeout=timedelta(seconds=10),
            save_timeout=timedelta(seconds=3),
            exit_grace=timedelta(seconds=2),
            stop_timeout=timedelta(seconds=1),
            kill_timeout=timedelta(seconds=3),
        ),
        idle_check_interval=timedelta(milliseconds=100),
    )


def fake(tmp_path: Path, *args: str) -> BackendCommand:
    return BackendCommand((sys.executable, str(FAKE_BACKEND), "--save-dir", str(tmp_path), *args))


def policy() -> DrainPolicy:
    return DrainPolicy(
        save_timeout=timedelta(seconds=3),
        exit_grace=timedelta(seconds=2),
        stop_timeout=timedelta(seconds=1),
        kill_timeout=timedelta(seconds=3),
    )


# Valheim's profile demands three minutes of grace for its inferred presence;
# tests would wait that long before an idle drain, so they shrink it.
FAST_PROFILE = dataclasses.replace(valheim.VALHEIM, inferred_presence_grace=timedelta(0))


async def test_empty_world_drains_on_idle_timeout(tmp_path: Path) -> None:
    w = world()
    files = valheim.save_files(w, config(tmp_path).backend)

    code = await run_world(
        w,
        config(tmp_path),
        profile=FAST_PROFILE,
        command=fake(tmp_path),
        files=files,
        policy=policy(),
    )

    assert code == EXIT_OK
    for path in files.paths:
        assert path.stat().st_size > 0
    marker = tmp_path / "state" / "testworld" / "last-verified.json"
    assert marker.exists()


async def test_half_present_world_is_refused_before_start(tmp_path: Path) -> None:
    w = world()
    files = valheim.save_files(w, config(tmp_path).backend)
    files.paths[0].parent.mkdir(parents=True)
    files.paths[0].write_bytes(b"only the db")

    code = await run_world(
        w,
        config(tmp_path),
        profile=FAST_PROFILE,
        command=fake(tmp_path),
        files=files,
        policy=policy(),
    )

    assert code == EXIT_FAILED
    assert not files.paths[1].exists(), "nothing was started, nothing was written"


async def test_operator_stop_drains_immediately(tmp_path: Path) -> None:
    w = world(idle=60, grace=60)
    stop = asyncio.Event()
    files = valheim.save_files(w, config(tmp_path).backend)

    async def request_stop() -> None:
        await asyncio.sleep(0.5)
        stop.set()

    requester = asyncio.create_task(request_stop())
    code = await asyncio.wait_for(
        run_world(
            w,
            config(tmp_path),
            profile=FAST_PROFILE,
            command=fake(tmp_path),
            files=files,
            policy=policy(),
            stop=stop,
        ),
        timeout=15,
    )

    await requester
    assert code == EXIT_OK
    assert files.paths[0].exists()


async def test_startup_failure_exits_nonzero(tmp_path: Path) -> None:
    w = world()

    code = await run_world(
        w,
        config(tmp_path),
        command=fake(tmp_path, "--behaviour", "crash-before-ready"),
        files=valheim.save_files(w, config(tmp_path).backend),
        policy=policy(),
    )

    assert code == EXIT_FAILED


async def test_unexpected_exit_is_reported(tmp_path: Path) -> None:
    w = world(idle=60, grace=60)

    code = await run_world(
        w,
        config(tmp_path),
        command=fake(tmp_path, "--behaviour", "crash-after-ready"),
        files=valheim.save_files(w, config(tmp_path).backend),
        policy=policy(),
    )

    assert code == EXIT_FAILED


async def test_profile_minimum_grace_delays_idle_drain(tmp_path: Path) -> None:
    """Inferred presence must not be trusted at zero immediately after start."""

    w = world(idle=0.2, grace=0.1)
    slow = dataclasses.replace(valheim.VALHEIM, inferred_presence_grace=timedelta(seconds=2))
    stop = asyncio.Event()
    started = asyncio.get_running_loop().time()

    code = await run_world(
        w,
        config(tmp_path),
        profile=slow,
        command=fake(tmp_path),
        files=valheim.save_files(w, config(tmp_path).backend),
        policy=policy(),
        stop=stop,
    )

    assert code == EXIT_OK
    assert asyncio.get_running_loop().time() - started >= 2.0


async def test_orchestrator_mode_never_drains_on_its_own(tmp_path: Path) -> None:
    w = CrossplayWorldConfig(
        id="testworld",
        name="Test World",
        owner="tests",
        idle_timeout=None,
        connection_grace_period=timedelta(milliseconds=100),
    )
    stop = asyncio.Event()

    async def operator() -> None:
        await asyncio.sleep(1.5)
        stop.set()

    requester = asyncio.create_task(operator())
    code = await run_world(
        w,
        config(tmp_path),
        profile=FAST_PROFILE,
        command=fake(tmp_path),
        files=valheim.save_files(w, config(tmp_path).backend),
        policy=policy(),
        stop=stop,
    )
    await requester

    assert code == EXIT_OK


async def test_failed_drain_exits_nonzero(tmp_path: Path) -> None:
    w = world()

    code = await run_world(
        w,
        config(tmp_path),
        profile=FAST_PROFILE,
        command=fake(tmp_path, "--behaviour", "corrupt-save"),
        files=valheim.save_files(w, config(tmp_path).backend),
        policy=policy(),
    )

    assert code == EXIT_FAILED


# ---- Valheim launch --------------------------------------------------------


def test_build_command_for_crossplay(tmp_path: Path) -> None:
    exe = tmp_path / "server" / "valheim_server.x86_64"
    launch = BackendLaunchConfig(
        executable=exe, save_dir=tmp_path / "saves", password=SecretStr("hunter22")
    )

    command = valheim.build_command(world(), launch)

    assert command.argv == (
        str(exe),
        "-nographics",
        "-batchmode",
        "-name",
        "Test World",
        "-world",
        "testworld",
        "-savedir",
        str(tmp_path / "saves"),
        "-password",
        "hunter22",
        "-crossplay",
    )
    assert command.cwd == exe.parent
    assert command.env is not None
    assert command.env["SteamAppId"] == "892970"
    assert command.env["LD_LIBRARY_PATH"].startswith(str(exe.parent / "linux64"))


def test_build_command_for_steam_direct(tmp_path: Path) -> None:
    exe = tmp_path / "valheim_server.x86_64"
    launch = BackendLaunchConfig(executable=exe, extra_args=("-preset", "hard"))
    w = SteamDirectWorldConfig(id="midgard", name="Midgard", owner="tests", game_port=2500)

    argv = valheim.build_command(w, launch).argv

    assert "-password" not in argv
    assert "-crossplay" not in argv
    assert argv[-6:] == ("-port", "2500", "-public", "0", "-preset", "hard")


def test_build_command_requires_executable() -> None:
    with pytest.raises(ValueError, match="executable"):
        valheim.build_command(world(), BackendLaunchConfig())


def test_save_files_follow_savedir_layout(tmp_path: Path) -> None:
    files = valheim.save_files(world(), BackendLaunchConfig(save_dir=tmp_path))

    assert files.paths == (
        tmp_path / "worlds_local" / "testworld.db",
        tmp_path / "worlds_local" / "testworld.fwl",
    )


def test_drain_policy_comes_from_launch_config() -> None:
    launch = BackendLaunchConfig(save_timeout=timedelta(seconds=7))

    assert valheim.drain_policy(launch).save_timeout == timedelta(seconds=7)
    assert valheim.drain_policy(launch).stop_timeout == launch.stop_timeout


def test_valheim_profile_publishes_its_capabilities() -> None:
    profile = registry.get_profile("valheim")

    assert profile.capabilities.presence == "inferred"
    assert profile.capabilities.join == "code"
    assert profile.save.kind == "signal"
    assert profile.minimum_connection_grace == timedelta(minutes=3)
    assert profile.capabilities.as_dict()["query"] is None


def test_unknown_game_is_an_error() -> None:
    with pytest.raises(ValueError, match="unknown game"):
        registry.get_profile("minecraft")


# ---- the host contract, end to end -------------------------------------------


async def test_runtime_speaks_the_contract(tmp_path: Path, capture, socket_dir: Path) -> None:
    import aiohttp

    from naust.agent.config import SinkConfig, SurfaceConfig
    from naust.agent.runtime import WorldRuntime

    w = world(idle=60, grace=60)
    base = config(tmp_path)
    cfg = base.model_copy(
        update={
            "sinks": (
                SinkConfig(kind="webhook", url=f"{capture.url}/events", token="t0k"),
                SinkConfig(kind="discord", url=f"{capture.url}/discord"),
            ),
            "surface": SurfaceConfig(socket_dir=socket_dir, metrics_port=0),
            "source_host": "test-host",
        }
    )
    runtime = WorldRuntime(
        w,
        cfg,
        profile=FAST_PROFILE,
        command=fake(tmp_path),
        files=valheim.save_files(w, cfg.backend),
        policy=policy(),
    )
    run = asyncio.create_task(runtime.run())
    await asyncio.wait_for(runtime.started.wait(), 5)
    unix = aiohttp.UnixConnector(path=str(socket_dir / "testworld.sock"))

    async with aiohttp.ClientSession(connector=unix) as control:
        for _ in range(100):
            async with control.get("http://naust/readyz") as r:
                if r.status == 200:
                    break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("never ready")

        await runtime.supervisor.write_stdin("join Alice 5\n")
        for _ in range(100):
            if runtime.status.count == 1:
                break
            await asyncio.sleep(0.02)

        metrics_url = f"http://127.0.0.1:{runtime.surface.tcp_port}/metrics"
        async with aiohttp.ClientSession() as http, http.get(metrics_url) as r:
            body = await r.text()
        assert 'naust_players{world="testworld"} 1.0' in body
        assert 'naust_backend_state{state="READY",world="testworld"} 1.0' in body

        async with control.get("http://naust/v1/status") as r:
            document = await r.json()
        assert document["state"] == "READY"
        assert document["presence"]["players"][0]["id"] == "Alice"
        assert document["capabilities"]["presence"] == "inferred"

        async with control.post("http://naust/v1/drain") as r:
            assert r.status == 202

    assert await asyncio.wait_for(run, 20) == EXIT_OK

    events = [r["json"] for r in capture.by_path("/events")]
    types = [e["type"].removeprefix("io.naust.") for e in events]
    assert types[0] == "backend.starting"
    assert types[-1] == "drain.finished"
    assert "backend.ready" in types
    assert "presence.changed" in types
    assert "drain.started" in types
    sequences = [e["naustsequence"] for e in events]
    assert sequences == sorted(sequences) and len(set(sequences)) == len(sequences)
    assert all(e["source"] == "naust://test-host/worlds/testworld" for e in events)
    assert all(r["headers"]["Authorization"] == "Bearer t0k" for r in capture.by_path("/events"))
    started = next(e for e in events if e["type"].endswith("drain.started"))
    assert started["data"]["trigger"] == "command"
    finished = events[-1]
    assert finished["data"]["succeeded"] is True
    assert finished["data"]["session"]["peakPlayers"] == 1
    assert finished["data"]["session"]["saves"] == 1
    discord = [r["json"]["content"] for r in capture.by_path("/discord")]
    assert any("Alice joined" in m for m in discord)
    assert any("saved and stopped" in m for m in discord)
    assert not (socket_dir / "testworld.sock").exists()
