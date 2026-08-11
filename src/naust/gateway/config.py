"""Static startup configuration for the always-on Gateway process."""

from pydantic import BaseModel, ConfigDict, HttpUrl, IPvAnyAddress


class GatewayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    bind_host: IPvAnyAddress = IPvAnyAddress("0.0.0.0")
    control_url: HttpUrl = HttpUrl("http://127.0.0.1:8000")
