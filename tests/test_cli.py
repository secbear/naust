from pathlib import Path

import pytest
from typer.testing import CliRunner

from naust.cli import app

runner = CliRunner()


def test_help_lists_the_two_commands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "agent" in result.output
    assert "parse" in result.output


def test_agent_requires_a_world() -> None:
    result = runner.invoke(app, ["agent"])

    assert result.exit_code == 2
    assert "--world" in result.output


def test_invalid_configuration_is_a_clean_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NAUST_AGENT__BACKEND__MAX_PLAYERS", "0")

    result = runner.invoke(app, ["agent", "--world", "midgard"])

    assert result.exit_code == 2
    assert "agent.backend.max_players" in result.output
    assert "Traceback" not in result.output


FIXTURE = Path(__file__).parent / "fixtures" / "valheim" / "presence-session.log"


def test_parse_replays_the_recorded_fixture() -> None:
    result = runner.invoke(app, ["parse", str(FIXTURE)])

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    presence = [
        line.split(maxsplit=2)[2] for line in lines if "  join " in line or "  leave " in line
    ]
    assert presence == [
        "join  PLAYER_A -> PLAYER_A",
        "join  PLAYER_B -> PLAYER_A, PLAYER_B",
        "leave PLAYER_B -> PLAYER_A",
        "leave PLAYER_A -> -",
    ]
    assert sum("  ready" in line for line in lines) == 1
    assert sum("  saved " in line for line in lines) == 1
    assert sum("  version l-0.221.12" in line for line in lines) == 1
    assert lines[-1] == "present: 0 [-]"


def test_parse_short_timeline(tmp_path: Path) -> None:
    log = tmp_path / "session.log"
    log.write_text(
        "noise\n"
        "01/01/2026 23:07:32: Game server connected\n"
        "01/01/2026 23:10:38: Got character ZDOID from Alice : 5:1\n"
        "01/01/2026 23:11:00: Got character ZDOID from Alice : 0:0\n"
        "01/01/2026 23:11:03: Got character ZDOID from Alice : 5:3\n"
        "01/01/2026 23:12:00: RPC_Disconnect\n"
        "01/01/2026 23:12:00: Closing socket 9\n"
        "01/01/2026 23:13:00: Got character ZDOID from Bob : 6:1\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["parse", str(log)])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "L     2  ready",
        "L     3  join  Alice -> Alice",
        "L     8  join  Bob -> Alice, Bob",
        "present: 2 [Alice, Bob]",
    ]


def test_parse_rejects_unknown_game(tmp_path: Path) -> None:
    log = tmp_path / "session.log"
    log.write_text("")

    result = runner.invoke(app, ["parse", "--game", "minecraft", str(log)])

    assert result.exit_code == 2
    assert "unknown game" in result.output


def test_parse_empty_and_garbage_files_finish_cleanly(tmp_path: Path) -> None:
    empty = tmp_path / "empty.log"
    empty.write_bytes(b"")
    garbage = tmp_path / "garbage.log"
    garbage.write_bytes(b"\xff\xfe not utf-8 \x00 RPC_Disconnect\n\xc3\x28\n")

    for path in (empty, garbage):
        result = runner.invoke(app, ["parse", str(path)])
        assert result.exit_code == 0, result.output
        assert result.output.splitlines() == ["present: 0 [-]"]


def test_parse_rejects_missing_and_directory_paths(tmp_path: Path) -> None:
    missing = runner.invoke(app, ["parse", str(tmp_path / "nope.log")])
    directory = runner.invoke(app, ["parse", str(tmp_path)])

    assert missing.exit_code == 2
    assert directory.exit_code == 2
    assert "Traceback" not in missing.output + directory.output


def test_parse_reports_joins_beyond_max_players(tmp_path: Path) -> None:
    log = tmp_path / "session.log"
    log.write_text(
        "Got character ZDOID from Alice : 5:1\nGot character ZDOID from Bob : 6:1\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["parse", "--max-players", "1", str(log)])

    assert result.exit_code == 0
    assert "present: 1 [Alice]" in result.output
    assert "1 join(s) exceeded --max-players 1" in result.output


def test_agent_world_must_be_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "naust.toml").write_text(
        '[[worlds]]\nid = "midgard"\nname = "Midgard"\nowner = "x"\nmode = "crossplay"\n'
    )
    monkeypatch.chdir(tmp_path)

    unknown = runner.invoke(app, ["agent", "--world", "asgard"])
    no_executable = runner.invoke(app, ["agent", "--world", "midgard"])

    assert unknown.exit_code == 2
    assert "midgard" in unknown.output
    assert no_executable.exit_code == 2
    assert "executable" in no_executable.output
