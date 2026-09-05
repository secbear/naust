"""The status document and its conditions."""

from pathlib import Path

from naust.agent.presence import PresenceSnapshot, PresenceTransition
from naust.agent.status import API_VERSION, AgentStatus, Condition
from naust.agent.supervisor import BackendState, SaveFiles
from naust.games.facts import JoinInfo


def status(tmp_path: Path) -> AgentStatus:
    return AgentStatus(
        world="midgard",
        game="valheim",
        capabilities={"presence": "inferred", "join": "code"},
        max_players=10,
        save_files=SaveFiles((tmp_path / "m.db", tmp_path / "m.fwl")),
    )


def test_condition_records_when_status_changes() -> None:
    condition = Condition("Ready", "False")

    assert condition.update("True", "BackendReady")
    first = condition.since
    assert first is not None
    assert not condition.update("True", "StillReady")
    assert condition.since == first
    assert condition.reason == "StillReady"
    assert condition.update("False")


def test_document_shape(tmp_path: Path) -> None:
    s = status(tmp_path)
    (tmp_path / "m.db").write_bytes(b"x" * 10)

    document = s.document()

    assert document["apiVersion"] == API_VERSION
    assert document["kind"] == "BackendStatus"
    assert document["state"] == "STARTING"
    assert {c["type"] for c in document["conditions"]} == {
        "Ready",
        "Draining",
        "SaveVerified",
        "VersionKnown",
    }
    assert document["presence"] == {
        "count": 0,
        "players": [],
        "quality": "inferred",
        "idleSince": None,
        "maxPlayers": 10,
    }
    assert document["join"] is None
    assert document["save"]["files"] == [
        {"path": str(tmp_path / "m.db"), "bytes": 10},
        {"path": str(tmp_path / "m.fwl"), "bytes": None},
    ]
    assert document["game_extension"] == {"valheim": {}}


def test_transitions_update_presence(tmp_path: Path) -> None:
    s = status(tmp_path)
    empty = PresenceSnapshot(frozenset(), 0)
    one = PresenceSnapshot(frozenset({"A"}), 1)

    s.apply_transition(PresenceTransition(empty, one))
    assert s.count == 1
    assert list(s.players) == ["A"]
    assert s.idle_since is None

    s.apply_transition(PresenceTransition(one, empty))
    assert s.count == 0
    assert s.players == {}
    assert s.idle_since is not None


def test_join_info_renders_code_or_address(tmp_path: Path) -> None:
    s = status(tmp_path)
    s.join = JoinInfo(code="604510")
    assert s.document()["join"] == {"kind": "code", "code": "604510"}
    s.join = JoinInfo(address="1.2.3.4", port=2456)
    assert s.document()["join"] == {"kind": "address", "address": "1.2.3.4", "port": 2456}


def test_sequence_and_state(tmp_path: Path) -> None:
    s = status(tmp_path)
    assert s.bump() == 1
    assert s.bump() == 2
    s.state = BackendState.READY
    assert s.document()["sequence"] == 2
    assert s.document()["state"] == "READY"
