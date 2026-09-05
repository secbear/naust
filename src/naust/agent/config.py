"""Static startup configuration for the per-backend Agent process."""

from datetime import timedelta
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr

from naust.domain.world import PositiveDuration


class BackendLaunchConfig(BaseModel):
    """How the Agent launches and drains a backend on this host.

    ``password`` is the only secret here. It is passed to the game on its
    command line because the game offers no other way, so it is visible in
    the process table of the host; it is never logged by Naust.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    executable: Path | None = None
    save_dir: Path = Path("/var/lib/naust/worlds")
    password: SecretStr | None = None
    extra_args: tuple[str, ...] = ()
    max_players: int = Field(default=10, ge=1, le=64)
    ready_timeout: PositiveDuration = timedelta(minutes=5)
    save_timeout: PositiveDuration = timedelta(minutes=2)
    exit_grace: PositiveDuration = timedelta(seconds=15)
    stop_timeout: PositiveDuration = timedelta(seconds=30)
    kill_timeout: PositiveDuration = timedelta(seconds=10)


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    control_url: HttpUrl = HttpUrl("http://127.0.0.1:8000")
    backend: BackendLaunchConfig = Field(default_factory=BackendLaunchConfig)
    state_dir: Path = Path("/var/lib/naust/state")
    idle_check_interval: PositiveDuration = timedelta(seconds=5)
