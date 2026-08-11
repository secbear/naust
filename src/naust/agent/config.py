"""Static startup configuration for the per-backend Agent process."""

from pydantic import BaseModel, ConfigDict, HttpUrl


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    control_url: HttpUrl = HttpUrl("http://127.0.0.1:8000")
