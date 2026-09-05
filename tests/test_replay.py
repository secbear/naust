"""The recorded session and the adapter boundary, replayed end to end."""

from pathlib import Path

from naust.agent.observations import (
    CharacterObserved,
    JoinCodeObserved,
    ServerReadyObserved,
    WorldSavedObserved,
    ZdoId,
)
from naust.agent.presence import PresenceTracker
from naust.agent.replay import ReplayEvent, replay
from naust.agent.valheim import ValheimAdapter

FIXTURE = Path(__file__).parent / "fixtures" / "valheim" / "presence-session.log"


def _replay_fixture() -> list[ReplayEvent]:
    with FIXTURE.open(encoding="utf-8", errors="replace") as lines:
        return list(replay(lines, ValheimAdapter(), PresenceTracker()))


def test_recorded_session_presence_timeline() -> None:
    events = _replay_fixture()
    transitions = [e.transition for e in events if e.transition is not None]

    assert [t.count for t in transitions] == [1, 2, 1, 0]
    assert transitions[0].joined == {"PLAYER_A"}
    assert transitions[1].joined == {"PLAYER_B"}
    assert transitions[2].left == {"PLAYER_B"}
    assert transitions[3].left == {"PLAYER_A"}


def test_recorded_session_ready_and_save_observed_once() -> None:
    events = _replay_fixture()

    ready = [e for e in events if isinstance(e.observation, ServerReadyObserved)]
    saved = [e for e in events if isinstance(e.observation, WorldSavedObserved)]
    assert len(ready) == 1
    assert len(saved) == 1
    assert saved[0].observation == WorldSavedObserved(61.499)
    assert ready[0].line_number < saved[0].line_number
    assert not any(isinstance(e.observation, JoinCodeObserved) for e in events)


def test_recorded_session_deaths_and_failed_logins_are_silent() -> None:
    events = _replay_fixture()

    deaths = [
        e
        for e in events
        if isinstance(e.observation, CharacterObserved) and e.observation.zdoid.is_null
    ]
    assert len(deaths) == 2
    assert all(e.transition is None for e in deaths)

    # The first character line is PLAYER_A; two disconnect markers precede it
    # (failed logins) and one more occurs while PLAYER_A is alone. None of
    # those produced a transition.
    first_join = next(e for e in events if isinstance(e.observation, CharacterObserved))
    assert first_join.observation.name == "PLAYER_A"
    assert first_join.transition is not None
    silent_before_join = [e for e in events if e.line_number < first_join.line_number]
    assert all(e.transition is None for e in silent_before_join)


def test_replay_works_with_a_non_valheim_adapter() -> None:
    """The loop and tracker do not know Valheim's grammar."""

    class ToyAdapter:
        def parse_line(self, line: str):
            head, _, rest = line.partition(" ")
            match head:
                case "LOGIN":
                    name, _, owner = rest.partition(" ")
                    return CharacterObserved(name, ZdoId(int(owner), 1))
                case "DIED":
                    return CharacterObserved(rest, ZdoId(0, 0))
                case _:
                    return None

    events = list(
        replay(
            ["LOGIN alice 1", "noise", "DIED alice", "LOGIN bob 2"],
            ToyAdapter(),
            PresenceTracker(),
        )
    )

    assert [e.line_number for e in events] == [1, 3, 4]
    assert [e.transition.count for e in events if e.transition] == [1, 2]


def test_replay_of_empty_and_garbage_streams_is_clean() -> None:
    assert list(replay([], ValheimAdapter(), PresenceTracker())) == []
    garbage = ["", "\x00", "RPC_Disconnect now", "Got character ZDOID from : 0:0", "ᚱᚢᚾᛖᛋ"]
    assert list(replay(garbage, ValheimAdapter(), PresenceTracker())) == []
