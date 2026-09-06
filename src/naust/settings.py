"""External configuration boundary and source precedence for Naust."""

from typing import Any, Self

from pydantic import Field, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from naust.agent.config import AgentConfig
from naust.domain.world import SteamDirectWorldConfig, WorldConfig
from naust.log import LogLevel


class NaustSettings(BaseSettings):
    """Resolved desired configuration; observed state never appears here."""

    log_level: LogLevel = LogLevel.INFO
    worlds: tuple[WorldConfig, ...] = ()
    agent: AgentConfig = Field(default_factory=AgentConfig)

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
        seen: set[str] = set()
        for world in self.worlds:
            if world.id in seen:
                raise ValueError(f"duplicate world id: {world.id!r}")
            seen.add(world.id)

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

    def resolved_config(self) -> dict[str, Any]:
        """Return a JSON-ready view with the backend password removed entirely."""

        return self.model_dump(mode="json", exclude={"agent": {"backend": {"password"}}})
