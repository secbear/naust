import pytest
from pydantic import ValidationError

from naust.storage.config import S3StorageConfig


def test_storage_accepts_a_custom_s3_provider() -> None:
    config = S3StorageConfig(
        endpoint="https://objects.example.net",
        bucket="valheim-worlds",
        region="provider-region-1",
    )

    assert str(config.endpoint) == "https://objects.example.net/"
    assert config.region == "provider-region-1"


@pytest.mark.parametrize(
    "values",
    [
        {"endpoint": "not-a-url"},
        {"bucket": "NO"},
        {"region": ""},
        {"access_key": "only-one-half"},
        {"access_key": "", "secret_key": "secret"},
    ],
)
def test_storage_constraints_reject_boundaries(values: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        S3StorageConfig(**values)
