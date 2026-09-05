"""The Agent runtime end to end against the fake backend."""

import asyncio
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr

from naust.agent import valheim
from naust.agent.config import AgentConfig, BackendLaunchConfig
from naust.agent.service import EXIT_FAILED, EXIT_OK, run_world
from naust.agent.supervisor import BackendCommand, DrainPolicy
from naust.domain.world import CrossplayWorldConfig, SteamDirectWorldConfig

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


async def test_empty_world_drains_on_idle_timeout(tmp_path: Path) -> None:
    w = world()
    files = valheim.save_files(w, config(tmp_path).backend)

    code = await run_world(
        w, config(tmp_path), command=fake(tmp_path), files=files, policy=policy()
    )

    assert code == EXIT_OK
    for path in files.paths:
        assert path.stat().st_size > 0


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
            w, config(tmp_path), command=fake(tmp_path), files=files, policy=policy(), stop=stop
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


async def test_failed_drain_exits_nonzero(tmp_path: Path) -> None:
    w = world()

    code = await run_world(
        w,
        config(tmp_path),
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
