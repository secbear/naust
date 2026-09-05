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


def test_backend_password_is_removed_from_resolved_config(monkeypatch) -> None:
    monkeypatch.setenv("NAUST_AGENT__BACKEND__PASSWORD", "hunter22")

    resolved = NaustSettings().resolved_config()

    assert "password" not in resolved["agent"]["backend"]
    assert "hunter22" not in str(resolved)


def test_sink_secrets_are_masked_in_resolved_config(monkeypatch) -> None:
    monkeypatch.setenv(
        "NAUST_AGENT__SINKS",
        '[{"kind": "discord", "url": "https://discord.com/api/webhooks/1/s3cret"}]',
    )

    resolved = NaustSettings().resolved_config()

    assert "s3cret" not in str(resolved)
    assert resolved["agent"]["sinks"][0]["kind"] == "discord"


def test_sink_requires_exactly_one_url_source(tmp_path) -> None:
    from naust.agent.config import SinkConfig

    with pytest.raises(ValidationError, match="exactly one"):
        SinkConfig(kind="webhook")
    with pytest.raises(ValidationError, match="exactly one"):
        SinkConfig(kind="webhook", url="http://x", url_file=tmp_path / "u")
    url_file = tmp_path / "u"
    url_file.write_text("http://from-file\n")
    token_file = tmp_path / "t"
    token_file.write_text("tok\n")
    sink = SinkConfig(kind="webhook", url_file=url_file, token_file=token_file)
    assert sink.resolve_url() == "http://from-file"
    assert sink.resolve_token() == "tok"
    assert SinkConfig(kind="discord", url="http://x").resolve_token() is None
