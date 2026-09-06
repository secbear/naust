"""How a game is described to the agent.

A profile is declarative where it can be and code where it must be. The
capabilities it publishes are part of the agent's status document: they tell
whoever orchestrates the agent how much of what it reports can be believed,
and they gate what the agent will do on its own.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from signal import Signals
from typing import Literal

from naust.agent.config import BackendLaunchConfig
from naust.agent.supervisor import BackendCommand, DrainPolicy, SaveFiles
from naust.domain.world import WorldConfig
from naust.games.facts import Observer, Resolver


@dataclass(frozen=True, slots=True)
class Capabilities:
    presence: Literal["exact", "inferred", "count-only", "none"]
    identity: Literal["stable-id", "name", "none"]
    save: Literal["signal", "command", "autosave-only"]
    join: Literal["code", "address"]
    version: Literal["log", "query", "none"]
    query: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "presence": self.presence,
            "identity": self.identity,
            "save": self.save,
            "join": self.join,
            "version": self.version,
            "query": self.query,
        }


@dataclass(frozen=True, slots=True)
class SaveMethod:
    """How the agent asks the game to save: a signal, a command, or it cannot."""

    signal: Signals | None = None
    command: str | None = None

    @classmethod
    def by_signal(cls, sig: Signals) -> "SaveMethod":
        return cls(signal=sig)

    @property
    def kind(self) -> Literal["signal", "command", "autosave-only"]:
        if self.signal is not None:
            return "signal"
        if self.command is not None:
            return "command"
        return "autosave-only"


@dataclass(frozen=True, slots=True)
class GameProfile:
    name: str
    capabilities: Capabilities
    save: SaveMethod
    build_command: Callable[[WorldConfig, BackendLaunchConfig], BackendCommand]
    save_files: Callable[[WorldConfig, BackendLaunchConfig], SaveFiles]
    drain_policy: Callable[[BackendLaunchConfig], DrainPolicy]
    observer: Callable[[], Observer]
    resolver: Callable[[], Resolver]
    steam_app_id: int | None = None
    inferred_presence_grace: timedelta = timedelta(minutes=3)
    """The least grace the agent grants after start when presence is inferred."""

    @property
    def minimum_connection_grace(self) -> timedelta:
        if self.capabilities.presence == "inferred":
            return self.inferred_presence_grace
        return timedelta(0)
