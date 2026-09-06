"""Static startup configuration for the per-backend Agent process."""

import socket
from datetime import timedelta
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr, model_validator

from naust.domain.world import PositiveDuration


class BackendLaunchConfig(BaseModel):
    """How the Agent launches and drains a backend on this host.

    ``password`` is the only secret here. It is passed to the game on its
    command line because the game offers no other way, so it is visible in
    the process table of the host; it is never logged by Naust.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    executable: Path | None = None
    wrapper: tuple[str, ...] = ()
    """Command prefix the game runs under, e.g. ``("steam-run",)`` on NixOS.

    Only the game is wrapped; the agent itself is not, so it stays the
    service's main process and signals reach the game through the wrapper.
    """
    save_dir: Path = Path("/var/lib/naust/worlds")
    password: SecretStr | None = None
    extra_args: tuple[str, ...] = ()
    max_players: int = Field(default=10, ge=1, le=64)
    ready_timeout: PositiveDuration = timedelta(minutes=5)
    save_timeout: PositiveDuration = timedelta(minutes=2)
    exit_grace: PositiveDuration = timedelta(seconds=15)
    stop_timeout: PositiveDuration = timedelta(seconds=30)
    kill_timeout: PositiveDuration = timedelta(seconds=10)


class SinkConfig(BaseModel):
    """Where events go. The URL is a secret for Discord, so it may come from a file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["webhook", "discord"]
    url: SecretStr | None = None
    url_file: Path | None = None
    token: SecretStr | None = None
    token_file: Path | None = None

    @model_validator(mode="after")
    def exactly_one_url(self) -> Self:
        if (self.url is None) == (self.url_file is None):
            raise ValueError("set exactly one of url and url_file")
        if self.token is not None and self.token_file is not None:
            raise ValueError("set at most one of token and token_file")
        return self

    def resolve_url(self) -> str:
        if self.url is not None:
            return self.url.get_secret_value()
        assert self.url_file is not None
        return self.url_file.read_text(encoding="utf-8").strip()

    def resolve_token(self) -> str | None:
        if self.token is not None:
            return self.token.get_secret_value()
        if self.token_file is not None:
            return self.token_file.read_text(encoding="utf-8").strip()
        return None


class SurfaceConfig(BaseModel):
    """Listeners for status, probes, commands, and metrics."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    socket_dir: Path | None = None
    """Directory for ``<world>.sock``; commands are only accepted here."""
    metrics_host: str = "127.0.0.1"
    metrics_port: int | None = Field(default=9701, ge=0, le=65_535)
    """Read-only listener for metrics and probes; ``null`` disables it."""


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    control_url: HttpUrl = HttpUrl("http://127.0.0.1:8000")
    backend: BackendLaunchConfig = Field(default_factory=BackendLaunchConfig)
    state_dir: Path = Path("/var/lib/naust/state")
    raw_log_dir: Path | None = None
    """Copy every backend output line to ``<dir>/<world>-<start time>.log``.

    Raw game output is the evidence adapters are written from; the journal
    only carries naust's own events. Off by default because it grows without
    bound; the host is expected to prune the directory.
    """
    idle_check_interval: PositiveDuration = timedelta(seconds=5)
    sinks: tuple[SinkConfig, ...] = ()
    surface: SurfaceConfig = Field(default_factory=SurfaceConfig)
    source_host: str = Field(default_factory=socket.gethostname)
    """The host part of the CloudEvents source, ``naust://<host>/worlds/<id>``."""
    event_flush_timeout: PositiveDuration = timedelta(seconds=10)
