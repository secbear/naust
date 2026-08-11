import json

import pytest
from typer.testing import CliRunner

from naust.cli import app

runner = CliRunner()


def _events(output: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for line in output.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


@pytest.mark.parametrize("command", [None, "agent", "control", "gateway"])
def test_help_lists_or_describes_commands(command: str | None) -> None:
    args = ["--help"] if command is None else [command, "--help"]
    result = runner.invoke(app, args)

    assert result.exit_code == 0
    if command is None:
        assert all(name in result.output for name in ("agent", "control", "gateway"))


@pytest.mark.parametrize("component", ["agent", "control", "gateway"])
def test_component_logs_safe_resolved_config_and_exits(
    component: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NAUST_STORAGE__ACCESS_KEY", "public-but-sensitive")
    monkeypatch.setenv("NAUST_STORAGE__SECRET_KEY", "definitely-secret")

    result = runner.invoke(app, [component])

    assert result.exit_code == 0
    events = _events(result.output)
    assert len(events) == 1
    event = events[0]
    assert event["event"] == "component.starting"
    assert event["component"] == component
    config = event["config"]
    assert isinstance(config, dict)
    assert set(config) == {"agent", "control", "gateway", "log_level", "storage", "worlds"}
    assert "access_key" not in config["storage"]
    assert "secret_key" not in config["storage"]
    assert "public-but-sensitive" not in result.output
    assert "definitely-secret" not in result.output


def test_cli_override_beats_environment_and_toml(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "naust.toml").write_text("[control]\nbind_port = 8100\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NAUST_CONTROL__BIND_PORT", "8200")

    result = runner.invoke(app, ["control", "--port", "8300"])

    assert result.exit_code == 0
    config = _events(result.output)[0]["config"]
    assert isinstance(config, dict)
    assert config["control"]["bind_port"] == 8300


def test_invalid_cli_override_is_a_clean_error() -> None:
    result = runner.invoke(app, ["control", "--port", "70000"])

    assert result.exit_code == 2
    assert "control.bind_port" in result.output
    assert "Traceback" not in result.output
