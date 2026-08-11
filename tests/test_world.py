from datetime import timedelta

import pytest
from pydantic import TypeAdapter, ValidationError

from naust.domain.world import (
    CrossplayWorldConfig,
    SteamDirectWorldConfig,
    WorldConfig,
    WorldState,
    WorldStatus,
)

world_adapter = TypeAdapter(WorldConfig)


def _base_world() -> dict[str, object]:
    return {
        "id": "midgard",
        "name": "Midgard",
        "owner": "friends",
    }


def test_storage_prefix_defaults_from_stable_world_id() -> None:
    world = world_adapter.validate_python({**_base_world(), "mode": "crossplay"})

    assert world.storage_prefix == "worlds/midgard"


def test_storage_prefix_can_be_overridden() -> None:
    world = world_adapter.validate_python(
        {**_base_world(), "mode": "crossplay", "storage_prefix": "imports/midgard"}
    )

    assert world.storage_prefix == "imports/midgard"


def test_steam_direct_derives_query_port() -> None:
    world = world_adapter.validate_python(
        {**_base_world(), "mode": "steam-direct", "game_port": 2456}
    )

    assert isinstance(world, SteamDirectWorldConfig)
    assert world.query_port == 2457


def test_crossplay_has_no_stable_public_network_fields() -> None:
    world = world_adapter.validate_python({**_base_world(), "mode": "crossplay"})

    assert isinstance(world, CrossplayWorldConfig)
    assert not hasattr(world, "game_port")
    assert not hasattr(world, "query_port")


@pytest.mark.parametrize("steam_only_field", ["game_port", "bepinex_enabled"])
def test_crossplay_rejects_steam_only_fields(steam_only_field: str) -> None:
    data = {**_base_world(), "mode": "crossplay", steam_only_field: 2456}

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        world_adapter.validate_python(data)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "Not URL Safe"),
        ("name", ""),
        ("owner", ""),
        ("storage_prefix", "../world"),
        ("idle_timeout", timedelta(0)),
        ("connection_grace_period", timedelta(0)),
        ("game_port", 65_535),
        ("resources", {"cpu_millicores": 0, "memory_mib": 2048}),
        ("resources", {"cpu_millicores": 1000, "memory_mib": 0}),
    ],
)
def test_world_constraints_reject_boundaries(field: str, value: object) -> None:
    data = {**_base_world(), "mode": "steam-direct", field: value}

    with pytest.raises(ValidationError):
        world_adapter.validate_python(data)


def test_lifecycle_vocabulary_is_exact_and_rejects_unknown_values() -> None:
    assert {state.value for state in WorldState} == {
        "SLEEPING",
        "WAKING",
        "AWAKE",
        "DRAINING",
        "FAILED",
    }
    assert WorldStatus(state=WorldState.SLEEPING).model_dump(mode="json") == {"state": "SLEEPING"}

    with pytest.raises(ValidationError):
        WorldStatus.model_validate({"state": "RUNNING"})
