"""Generated sequences search the combinations the example tests did not name."""

from hypothesis import given, settings
from hypothesis import strategies as st

from naust.agent.observations import (
    AbandonedZdoObserved,
    CharacterObserved,
    DisconnectMarkerObserved,
    JoinCodeObserved,
    Observation,
    ServerReadyObserved,
    SocketClosedObserved,
    WorldSavedObserved,
    ZdoId,
)
from naust.agent.presence import PresenceTracker
from naust.agent.valheim import ValheimAdapter

NAMES = st.sampled_from(["A", "B", "C", "D", "E"])
OWNERS = st.sampled_from([-3, -2, -1, 1, 2, 3, 4])
OBJECT_IDS = st.integers(min_value=1, max_value=50)


def _character() -> st.SearchStrategy[CharacterObserved]:
    live = st.builds(
        CharacterObserved,
        name=NAMES,
        zdoid=st.builds(ZdoId, owner=OWNERS, object_id=OBJECT_IDS),
    )
    dead = st.builds(CharacterObserved, name=NAMES, zdoid=st.just(ZdoId(0, 0)))
    return st.one_of(live, dead)


OBSERVATIONS: st.SearchStrategy[Observation] = st.one_of(
    _character(),
    st.just(DisconnectMarkerObserved()),
    st.builds(AbandonedZdoObserved, zdoid=st.builds(ZdoId, OWNERS, OBJECT_IDS), owner=OWNERS),
    st.builds(SocketClosedObserved, connection_id=st.integers(min_value=1, max_value=9)),
    st.just(ServerReadyObserved()),
    st.builds(WorldSavedObserved, duration_ms=st.floats(min_value=0, max_value=1e6)),
    st.builds(JoinCodeObserved, code=st.text(alphabet="0123456789", min_size=6, max_size=6)),
)


@settings(max_examples=300)
@given(st.lists(OBSERVATIONS, max_size=60), st.integers(min_value=1, max_value=5))
def test_tracker_invariants_hold_after_every_observation(
    observations: list[Observation], max_players: int
) -> None:
    tracker = PresenceTracker(max_players=max_players)
    previous = tracker.snapshot

    for observation in observations:
        transition = tracker.observe(observation)
        current = tracker.snapshot

        assert 0 <= tracker.count <= max_players
        assert tracker.count == current.count == len(current.players)
        if transition is None:
            assert current == previous
        else:
            assert transition.before == previous
            assert transition.after == current
            assert current != previous
            assert transition.joined == current.players - previous.players
            assert transition.left == previous.players - current.players
        previous = current


@settings(max_examples=200)
@given(st.lists(OBSERVATIONS, max_size=60))
def test_one_disconnect_marker_removes_at_most_one_player(
    observations: list[Observation],
) -> None:
    tracker = PresenceTracker()
    left_since_marker = 0

    for observation in observations:
        if isinstance(observation, DisconnectMarkerObserved):
            left_since_marker = 0
        transition = tracker.observe(observation)
        if transition is not None:
            left_since_marker += len(transition.left)
        assert left_since_marker <= 1


@settings(max_examples=200)
@given(st.lists(OBSERVATIONS, max_size=60))
def test_repeated_character_evidence_is_idempotent(observations: list[Observation]) -> None:
    tracker = PresenceTracker()
    for observation in observations:
        tracker.observe(observation)
        if isinstance(observation, CharacterObserved):
            assert tracker.observe(observation) is None


@given(
    st.dictionaries(NAMES, OWNERS, min_size=1, max_size=5),
    st.integers().filter(lambda owner: owner not in {-3, -2, -1, 1, 2, 3, 4}),
)
def test_unknown_cleanup_cannot_remove_a_known_player(
    present: dict[str, int], stranger: int
) -> None:
    tracker = PresenceTracker()
    for name, owner in present.items():
        tracker.observe(CharacterObserved(name, ZdoId(owner, 1)))
    before = tracker.snapshot

    tracker.observe(DisconnectMarkerObserved())
    assert tracker.observe(AbandonedZdoObserved(ZdoId(stranger, 1), owner=stranger)) is None
    assert tracker.snapshot == before


@settings(max_examples=500)
@given(st.text())
def test_parser_never_raises_on_arbitrary_text(line: str) -> None:
    result = ValheimAdapter().parse_line(line)
    assert result is None or isinstance(
        result,
        CharacterObserved
        | DisconnectMarkerObserved
        | AbandonedZdoObserved
        | SocketClosedObserved
        | ServerReadyObserved
        | WorldSavedObserved
        | JoinCodeObserved,
    )


@given(
    st.text(
        alphabet=st.characters(blacklist_categories=("Cc", "Cs")),
        min_size=1,
    ).filter(lambda name: " : " not in name and name == name.strip()),
    st.integers(min_value=-(2**63), max_value=2**63 - 1),
    st.integers(min_value=0, max_value=2**31),
)
def test_character_lines_round_trip(name: str, owner: int, object_id: int) -> None:
    line = f"01/01/2026 23:10:38: Got character ZDOID from {name} : {owner}:{object_id}"

    assert ValheimAdapter().parse_line(line) == CharacterObserved(
        name, ZdoId(owner=owner, object_id=object_id)
    )
