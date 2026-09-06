"""The operator's description of one world: the desired configuration.

Observed state never lives here; that is the agent's status document. A
world's networking ``mode`` is a real fork, not a flag: Steam-direct owns
public ports that an orchestrator may route to, crossplay owns nothing
inbound and is reachable only through a join code that changes on every
start.
"""

from datetime import timedelta
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, computed_field

type WorldId = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=63,
        pattern=r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$",
        strip_whitespace=True,
    ),
]
type DisplayName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=80, strip_whitespace=True),
]
type OwnerLabel = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, strip_whitespace=True),
]
type PositiveDuration = Annotated[timedelta, Field(gt=timedelta(0))]
type GameName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=32, pattern=r"^[a-z0-9-]+$"),
]
type GamePort = Annotated[int, Field(ge=1, le=65_534)]


class _DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WorldMode(StrEnum):
    STEAM_DIRECT = "steam-direct"
    CROSSPLAY = "crossplay"


class _WorldConfigBase(_DomainModel):
    """Fields shared by both mutually exclusive networking modes."""

    id: WorldId
    """Immutable and URL-safe: file names, socket names, and event sources use it."""
    name: DisplayName
    """What players see; renaming a world does not move anything on disk."""
    owner: OwnerLabel
    """A label for humans, not an authorization principal."""
    game: GameName = "valheim"
    idle_timeout: PositiveDuration | None = timedelta(minutes=15)
    """``None`` hands the drain decision to whoever orchestrates the agent."""
    connection_grace_period: PositiveDuration = timedelta(minutes=3)


class SteamDirectWorldConfig(_WorldConfigBase):
    mode: Literal[WorldMode.STEAM_DIRECT] = WorldMode.STEAM_DIRECT
    game_port: GamePort = 2_456

    @computed_field
    @property
    def query_port(self) -> int:
        """Steam's query port is a derived invariant, never independent input."""

        return self.game_port + 1


class CrossplayWorldConfig(_WorldConfigBase):
    mode: Literal[WorldMode.CROSSPLAY] = WorldMode.CROSSPLAY


type WorldConfig = Annotated[
    SteamDirectWorldConfig | CrossplayWorldConfig,
    Field(discriminator="mode"),
]
