"""External configuration boundary and source precedence for Naust."""

from collections.abc import Hashable, Iterable
from typing import Any, Self

from pydantic import Field, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from naust.agent.config import AgentConfig
from naust.control.config import ControlConfig
from naust.domain.world import SteamDirectWorldConfig, WorldConfig
from naust.gateway.config import GatewayConfig
from naust.log import LogLevel
from naust.storage.config import S3StorageConfig


class NaustSettings(BaseSettings):
    """Resolved desired configuration; observed state never appears here."""

    log_level: LogLevel = LogLevel.INFO
    storage: S3StorageConfig = Field(default_factory=S3StorageConfig)
    worlds: tuple[WorldConfig, ...] = ()
    agent: AgentConfig = Field(default_factory=AgentConfig)
    control: ControlConfig = Field(default_factory=ControlConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)

    model_config = SettingsConfigDict(
        env_prefix="NAUST_",
        env_nested_delimiter="__",
        extra="forbid",
        toml_file="naust.toml",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """CLI/init overrides > environment > TOML > model defaults."""

        return (
            init_settings,
            env_settings,
            TomlConfigSettingsSource(settings_cls),
        )

    @model_validator(mode="after")
    def registry_invariants(self) -> Self:
        self._require_unique("world id", (world.id for world in self.worlds))
        self._require_unique("storage prefix", (world.storage_prefix for world in self.worlds))

        allocated_ports: dict[int, str] = {}
        for world in self.worlds:
            if not isinstance(world, SteamDirectWorldConfig):
                continue
            for port in (world.game_port, world.query_port):
                previous = allocated_ports.get(port)
                if previous is not None:
                    raise ValueError(
                        f"public port {port} is shared by worlds {previous!r} and {world.id!r}"
                    )
                allocated_ports[port] = world.id
        return self

    @staticmethod
    def _require_unique(label: str, values: Iterable[Hashable]) -> None:
        seen: set[Hashable] = set()
        for value in values:
            if value in seen:
                raise ValueError(f"duplicate {label}: {value!r}")
            seen.add(value)

    def resolved_config(self) -> dict[str, Any]:
        """Return a JSON-ready view with credential fields removed entirely."""

        return self.model_dump(
            mode="json",
            exclude={"storage": {"access_key", "secret_key"}},
        )
