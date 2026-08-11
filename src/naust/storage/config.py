"""Installation-wide S3-compatible object-store configuration."""

from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    SecretStr,
    StringConstraints,
    model_validator,
)

BucketName = Annotated[
    str,
    StringConstraints(
        min_length=3,
        max_length=63,
        pattern=r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])$",
        strip_whitespace=True,
    ),
]


class S3StorageConfig(BaseModel):
    """Provider-neutral S3 API settings shared by all configured worlds."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    endpoint: HttpUrl = HttpUrl("http://127.0.0.1:9000")
    bucket: BucketName = "naust-worlds"
    region: str = Field(default="us-east-1", min_length=1)
    access_key: SecretStr | None = None
    secret_key: SecretStr | None = None

    @model_validator(mode="after")
    def credentials_are_a_pair(self) -> Self:
        if (self.access_key is None) != (self.secret_key is None):
            raise ValueError("access_key and secret_key must be supplied together")
        for name, secret in (
            ("access_key", self.access_key),
            ("secret_key", self.secret_key),
        ):
            if secret is not None and not secret.get_secret_value():
                raise ValueError(f"{name} must not be empty")
        return self
