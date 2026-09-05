"""Launching Valheim, and the profile that ties observer, resolver and launch together."""

import os
import signal
from typing import Final

from naust.agent.config import BackendLaunchConfig
from naust.agent.supervisor import BackendCommand, DrainPolicy, SaveFiles
from naust.domain.world import CrossplayWorldConfig, SteamDirectWorldConfig, WorldConfig
from naust.games.profile import Capabilities, GameProfile, SaveMethod
from naust.games.valheim.observer import ValheimObserver
from naust.games.valheim.resolver import ValheimResolver

STEAM_APP_ID: Final = "892970"
"""The game's app id, which the stock start_server.sh exports as SteamAppId."""

DEDICATED_SERVER_APP_ID: Final = 896660
"""The app steamcmd installs."""


def build_command(world: WorldConfig, launch: BackendLaunchConfig) -> BackendCommand:
    """The argv, cwd, and environment the stock ``start_server.sh`` would use.

    The server runs from its own directory with ``linux64`` on the library
    path, as Iron Gate's script does. ``-savedir`` pins the world files under
    ``launch.save_dir`` so the supervisor knows what to verify. The password
    rides on the command line because the game accepts it nowhere else.
    """

    if launch.executable is None:
        raise ValueError("agent.backend.executable is required to run a world")
    executable = launch.executable.expanduser().resolve()
    argv: list[str] = [
        str(executable),
        "-nographics",
        "-batchmode",
        "-name",
        world.name,
        "-world",
        world.id,
        "-savedir",
        str(launch.save_dir),
    ]
    if launch.password is not None:
        argv += ["-password", launch.password.get_secret_value()]
    match world:
        case SteamDirectWorldConfig(game_port=game_port):
            argv += ["-port", str(game_port), "-public", "0"]
        case CrossplayWorldConfig():
            argv += ["-crossplay"]
    argv += list(launch.extra_args)

    server_dir = executable.parent
    library_path = str(server_dir / "linux64")
    if existing := os.environ.get("LD_LIBRARY_PATH"):
        library_path = f"{library_path}:{existing}"
    env = {**os.environ, "SteamAppId": STEAM_APP_ID, "LD_LIBRARY_PATH": library_path}
    return BackendCommand(argv=tuple(argv), cwd=server_dir, env=env)


def save_files(world: WorldConfig, launch: BackendLaunchConfig) -> SaveFiles:
    """The ``.db`` and ``.fwl`` pair that must travel together."""

    worlds = launch.save_dir / "worlds_local"
    return SaveFiles((worlds / f"{world.id}.db", worlds / f"{world.id}.fwl"))


def drain_policy(launch: BackendLaunchConfig) -> DrainPolicy:
    return DrainPolicy(
        save_signal=signal.SIGINT,
        save_timeout=launch.save_timeout,
        exit_grace=launch.exit_grace,
        stop_timeout=launch.stop_timeout,
        kill_timeout=launch.kill_timeout,
    )


VALHEIM: Final = GameProfile(
    name="valheim",
    steam_app_id=DEDICATED_SERVER_APP_ID,
    capabilities=Capabilities(
        presence="inferred",
        identity="name",
        save="signal",
        join="code",
        version="log",
        query=None,
    ),
    save=SaveMethod.by_signal(signal.SIGINT),
    build_command=build_command,
    save_files=save_files,
    drain_policy=drain_policy,
    observer=ValheimObserver,
    resolver=ValheimResolver,
)
