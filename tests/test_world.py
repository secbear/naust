from datetime import timedelta

import pytest
from pydantic import TypeAdapter, ValidationError

from naust.domain.world import CrossplayWorldConfig, SteamDirectWorldConfig, WorldConfig

world_adapter = TypeAdapter(WorldConfig)


def _base_world() -> dict[str, object]:
    return {"id": "midgard", "name": "Midgard", "owner": "friends"}


def test_steam_direct_derives_query_port() -> None:
    world = world_adapter.validate_python(
        {**_base_world(), "mode": "steam-direct", "game_port": 2456}
    )

    assert isinstance(world, SteamDirectWorldConfig)
    assert world.query_port == 2457


def test_crossplay_has_no_public_network_fields() -> None:
    world = world_adapter.validate_python({**_base_world(), "mode": "crossplay"})

    assert isinstance(world, CrossplayWorldConfig)
    assert not hasattr(world, "game_port")
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        world_adapter.validate_python({**_base_world(), "mode": "crossplay", "game_port": 2456})


def test_idle_timeout_may_be_disabled_for_an_orchestrator() -> None:
    world = world_adapter.validate_python(
        {**_base_world(), "mode": "crossplay", "idle_timeout": None}
    )

    assert world.idle_timeout is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "Not URL Safe"),
        ("name", ""),
        ("owner", ""),
        ("idle_timeout", timedelta(0)),
        ("connection_grace_period", timedelta(0)),
        ("game_port", 65_535),
    ],
)
def test_world_constraints_reject_boundaries(field: str, value: object) -> None:
    data = {**_base_world(), "mode": "steam-direct", field: value}

    with pytest.raises(ValidationError):
        world_adapter.validate_python(data)
