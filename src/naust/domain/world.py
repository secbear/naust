"""Stable domain vocabulary for a Naust-managed world.

Desired configuration and observed status deliberately live in separate models:
operators write a ``WorldConfig`` while Control alone owns ``WorldStatus``.
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
type StoragePrefix = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=512,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$",
        strip_whitespace=True,
    ),
]
type PositiveDuration = Annotated[timedelta, Field(gt=timedelta(0))]
type GameName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=32, pattern=r"^[a-z0-9-]+$"),
]
type GamePort = Annotated[int, Field(ge=1, le=65_534)]


def _default_storage_prefix(validated_data: dict[str, object]) -> str:
    """Keep the conventional object layout tied to the world's stable ID."""

    return f"worlds/{validated_data['id']}"


class _DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WorldMode(StrEnum):
    STEAM_DIRECT = "steam-direct"
    CROSSPLAY = "crossplay"


class WorldState(StrEnum):
    SLEEPING = "SLEEPING"
    WAKING = "WAKING"
    AWAKE = "AWAKE"
    DRAINING = "DRAINING"
    FAILED = "FAILED"


class ResourceIntent(_DomainModel):
    """Per-world scheduler intent with explicit units."""

    cpu_millicores: int = Field(default=1_000, gt=0)
    memory_mib: int = Field(default=2_048, gt=0)


class _WorldConfigBase(_DomainModel):
    """Fields shared by both mutually exclusive networking modes."""

    id: WorldId
    name: DisplayName
    owner: OwnerLabel
    game: GameName = "valheim"
    storage_prefix: StoragePrefix = Field(default_factory=_default_storage_prefix)
    idle_timeout: PositiveDuration | None = timedelta(minutes=15)
    connection_grace_period: PositiveDuration = timedelta(minutes=3)
    resources: ResourceIntent = Field(default_factory=ResourceIntent)


class SteamDirectWorldConfig(_WorldConfigBase):
    mode: Literal[WorldMode.STEAM_DIRECT] = WorldMode.STEAM_DIRECT
    game_port: GamePort = 2_456
    bepinex_enabled: bool = False

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


class WorldStatus(_DomainModel):
    """Minimal Control-owned observed status for Project 0."""

    state: WorldState
