"""Static startup configuration for the Control process."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, IPvAnyAddress

Port = Annotated[int, Field(ge=1, le=65_535)]


class ControlConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    bind_host: IPvAnyAddress = IPvAnyAddress("127.0.0.1")
    bind_port: Port = 8_000
