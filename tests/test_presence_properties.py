"""Generated sequences search the combinations the example tests did not name."""

from hypothesis import given, settings
from hypothesis import strategies as st

from naust.agent.presence import PresenceTracker
from naust.games.facts import (
    BackendReady,
    BackendVersion,
    Fact,
    JoinInfo,
    PlayerCount,
    PlayerJoined,
    PlayerLeft,
    SaveCompleted,
)
from naust.games.valheim.observer import (
    AbandonedZdoObserved,
    CharacterObserved,
    DisconnectMarkerObserved,
    SocketClosedObserved,
    ValheimObserver,
    ZdoId,
)
from naust.games.valheim.resolver import ValheimResolver

NAMES = st.sampled_from(["A", "B", "C", "D", "E"])
OWNERS = st.sampled_from([-3, -2, -1, 1, 2, 3, 4])
OBJECT_IDS = st.integers(min_value=1, max_value=50)

FACTS: st.SearchStrategy[Fact] = st.one_of(
    st.builds(PlayerJoined, NAMES),
    st.builds(PlayerLeft, NAMES),
    st.builds(PlayerCount, st.integers(min_value=-2, max_value=12)),
    st.just(BackendReady()),
    st.builds(SaveCompleted, st.floats(min_value=0, max_value=1e6)),
    st.builds(JoinInfo, code=st.text(alphabet="0123456789", min_size=6, max_size=6)),
    st.builds(BackendVersion, st.text(min_size=1, max_size=8)),
)

VALHEIM_OBSERVATIONS = st.one_of(
    st.builds(CharacterObserved, NAMES, st.builds(ZdoId, OWNERS, OBJECT_IDS)),
    st.builds(CharacterObserved, NAMES, st.just(ZdoId(0, 0))),
    st.just(DisconnectMarkerObserved()),
    st.builds(AbandonedZdoObserved, st.builds(ZdoId, OWNERS, OBJECT_IDS), OWNERS),
    st.builds(SocketClosedObserved, st.integers(min_value=1, max_value=9)),
)


@settings(max_examples=300)
@given(st.lists(FACTS, max_size=60), st.integers(min_value=1, max_value=5))
def test_tracker_invariants_hold_after_every_fact(facts: list[Fact], max_players: int) -> None:
    tracker = PresenceTracker(max_players=max_players)
    previous = tracker.snapshot

    for fact in facts:
        transition = tracker.observe(fact)
        current = tracker.snapshot

        assert 0 <= tracker.count <= max_players
        assert tracker.count == current.count
        assert len(current.players) <= current.count
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
@given(st.lists(FACTS, max_size=60))
def test_repeated_facts_are_idempotent(facts: list[Fact]) -> None:
    tracker = PresenceTracker()
    for fact in facts:
        tracker.observe(fact)
        assert tracker.observe(fact) is None


@settings(max_examples=300)
@given(st.lists(VALHEIM_OBSERVATIONS, max_size=60))
def test_one_disconnect_marker_removes_at_most_one_player(observations) -> None:
    resolver = ValheimResolver()
    tracker = PresenceTracker()
    left_since_marker = 0

    for observation in observations:
        if isinstance(observation, DisconnectMarkerObserved):
            left_since_marker = 0
        for fact in resolver.resolve(observation):
            transition = tracker.observe(fact)
            if transition is not None:
                left_since_marker += len(transition.left)
        assert left_since_marker <= 1
        assert 0 <= tracker.count <= tracker.max_players


@given(
    st.dictionaries(NAMES, OWNERS, min_size=1, max_size=5),
    st.integers().filter(lambda owner: owner not in {-3, -2, -1, 1, 2, 3, 4}),
)
def test_unknown_cleanup_cannot_remove_a_known_player(
    present: dict[str, int], stranger: int
) -> None:
    resolver = ValheimResolver()
    for name, owner in present.items():
        resolver.resolve(CharacterObserved(name, ZdoId(owner, 1)))

    resolver.resolve(DisconnectMarkerObserved())
    assert resolver.resolve(AbandonedZdoObserved(ZdoId(stranger, 1), owner=stranger)) == ()


@settings(max_examples=500)
@given(st.text())
def test_observer_never_raises_on_arbitrary_text(line: str) -> None:
    ValheimObserver().parse_line(line)


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

    assert ValheimObserver().parse_line(line) == CharacterObserved(
        name, ZdoId(owner=owner, object_id=object_id)
    )
