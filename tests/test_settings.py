from pathlib import Path

import pytest
from pydantic import ValidationError

from naust.settings import NaustSettings


def test_source_precedence_peels_to_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NAUST_CONTROL__BIND_PORT", raising=False)
    assert NaustSettings().control.bind_port == 8000

    (tmp_path / "naust.toml").write_text("[control]\nbind_port = 8100\n")
    assert NaustSettings().control.bind_port == 8100

    monkeypatch.setenv("NAUST_CONTROL__BIND_PORT", "8200")
    assert NaustSettings().control.bind_port == 8200

    settings = NaustSettings(control={"bind_port": 8300})
    assert settings.control.bind_port == 8300


def test_registry_rejects_duplicate_world_ids() -> None:
    world = {
        "id": "midgard",
        "name": "Midgard",
        "owner": "friends",
        "mode": "crossplay",
        "storage_prefix": "worlds/midgard",
    }
    duplicate = {**world, "storage_prefix": "worlds/other"}

    with pytest.raises(ValidationError, match="duplicate world id"):
        NaustSettings(worlds=[world, duplicate])


def test_registry_rejects_duplicate_storage_prefixes() -> None:
    worlds = [
        {
            "id": "midgard",
            "name": "Midgard",
            "owner": "friends",
            "mode": "crossplay",
            "storage_prefix": "worlds/shared",
        },
        {
            "id": "yggdrasil",
            "name": "Yggdrasil",
            "owner": "friends",
            "mode": "crossplay",
            "storage_prefix": "worlds/shared",
        },
    ]

    with pytest.raises(ValidationError, match="duplicate storage prefix"):
        NaustSettings(worlds=worlds)


def test_registry_rejects_overlapping_public_ports() -> None:
    worlds = [
        {
            "id": "midgard",
            "name": "Midgard",
            "owner": "friends",
            "mode": "steam-direct",
            "game_port": 2456,
            "storage_prefix": "worlds/midgard",
        },
        {
            "id": "yggdrasil",
            "name": "Yggdrasil",
            "owner": "friends",
            "mode": "steam-direct",
            "game_port": 2457,
            "storage_prefix": "worlds/yggdrasil",
        },
    ]

    with pytest.raises(ValidationError, match="public port 2457"):
        NaustSettings(worlds=worlds)
