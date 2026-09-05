"""The recorded session and the game boundary, replayed end to end."""

from pathlib import Path

from naust.agent.presence import PresenceTracker
from naust.agent.replay import ReplayEvent, replay
from naust.games.facts import (
    BackendReady,
    BackendVersion,
    JoinInfo,
    PlayerJoined,
    PlayerLeft,
    SaveCompleted,
)
from naust.games.valheim.observer import CharacterObserved, ValheimObserver
from naust.games.valheim.resolver import ValheimResolver

FIXTURE = Path(__file__).parent / "fixtures" / "valheim" / "presence-session.log"


def _replay_fixture() -> list[ReplayEvent]:
    with FIXTURE.open(encoding="utf-8", errors="replace") as lines:
        return list(replay(lines, ValheimObserver(), ValheimResolver(), PresenceTracker()))


def _facts(events: list[ReplayEvent], kind: type) -> list:
    return [f for e in events for f in e.facts if isinstance(f, kind)]


def test_recorded_session_presence_timeline() -> None:
    events = _replay_fixture()
    transitions = [t for e in events for t in e.transitions]

    assert [t.count for t in transitions] == [1, 2, 1, 0]
    assert transitions[0].joined == {"PLAYER_A"}
    assert transitions[1].joined == {"PLAYER_B"}
    assert transitions[2].left == {"PLAYER_B"}
    assert transitions[3].left == {"PLAYER_A"}
    assert _facts(events, PlayerJoined) == [PlayerJoined("PLAYER_A"), PlayerJoined("PLAYER_B")]
    assert _facts(events, PlayerLeft) == [PlayerLeft("PLAYER_B"), PlayerLeft("PLAYER_A")]


def test_recorded_session_lifecycle_facts() -> None:
    events = _replay_fixture()

    assert _facts(events, BackendReady) == [BackendReady()]
    assert _facts(events, SaveCompleted) == [SaveCompleted(61.499)]
    assert _facts(events, BackendVersion) == [BackendVersion("l-0.221.12")]
    assert _facts(events, JoinInfo) == []
    ready_line = next(e.line_number for e in events if BackendReady() in e.facts)
    saved_line = next(e.line_number for e in events if SaveCompleted(61.499) in e.facts)
    assert ready_line < saved_line


def test_recorded_session_deaths_and_failed_logins_are_silent() -> None:
    events = _replay_fixture()

    deaths = [
        e
        for e in events
        if isinstance(e.observation, CharacterObserved) and e.observation.zdoid.is_null
    ]
    assert len(deaths) == 2
    assert all(e.facts == () and e.transitions == () for e in deaths)

    first_join = next(e for e in events if isinstance(e.observation, CharacterObserved))
    assert first_join.observation.name == "PLAYER_A"
    assert first_join.transitions
    assert all(not e.transitions for e in events if e.line_number < first_join.line_number)


def test_replay_works_with_a_non_valheim_game() -> None:
    """The loop and tracker do not know any game's grammar."""

    class ToyObserver:
        def parse_line(self, line: str):
            head, _, rest = line.partition(" ")
            return (head, rest) if head in {"LOGIN", "LOGOUT", "DIED"} else None

    class ToyResolver:
        def resolve(self, observation):
            head, name = observation
            match head:
                case "LOGIN":
                    return (PlayerJoined(name),)
                case "LOGOUT":
                    return (PlayerLeft(name),)
                case _:
                    return ()

    events = list(
        replay(
            ["LOGIN alice", "noise", "DIED alice", "LOGIN bob", "LOGOUT alice"],
            ToyObserver(),
            ToyResolver(),
            PresenceTracker(),
        )
    )

    assert [e.line_number for e in events] == [1, 3, 4, 5]
    assert [t.count for e in events for t in e.transitions] == [1, 2, 1]


def test_replay_of_empty_and_garbage_streams_is_clean() -> None:
    assert list(replay([], ValheimObserver(), ValheimResolver(), PresenceTracker())) == []
    garbage = ["", "\x00", "RPC_Disconnect now", "Got character ZDOID from : 0:0", "ᚱᚢᚾᛖᛋ"]
    assert list(replay(garbage, ValheimObserver(), ValheimResolver(), PresenceTracker())) == []
