"""Tracker rules over game-neutral facts."""

import pytest

from naust.agent.presence import PresenceSnapshot, PresenceTracker, PresenceTransition
from naust.games.facts import (
    BackendReady,
    BackendVersion,
    JoinInfo,
    PlayerCount,
    PlayerJoined,
    PlayerLeft,
    SaveCompleted,
)


def test_fresh_tracker_is_empty() -> None:
    tracker = PresenceTracker()

    assert tracker.snapshot == PresenceSnapshot(frozenset(), 0)
    assert tracker.count == 0


def test_join_emits_one_transition() -> None:
    tracker = PresenceTracker()

    transition = tracker.observe(PlayerJoined("A"))

    assert transition == PresenceTransition(
        before=PresenceSnapshot(frozenset(), 0),
        after=PresenceSnapshot(frozenset({"A"}), 1),
    )
    assert transition.joined == {"A"}
    assert transition.left == frozenset()
    assert transition.count == 1


def test_repeated_join_is_silent() -> None:
    tracker = PresenceTracker()
    tracker.observe(PlayerJoined("A"))

    assert tracker.observe(PlayerJoined("A")) is None
    assert tracker.count == 1


def test_leave_removes_exactly_that_player() -> None:
    tracker = PresenceTracker()
    tracker.observe(PlayerJoined("A"))
    tracker.observe(PlayerJoined("B"))

    transition = tracker.observe(PlayerLeft("B"))

    assert transition is not None
    assert transition.left == {"B"}
    assert tracker.snapshot.players == {"A"}


def test_leave_of_unknown_player_is_silent() -> None:
    tracker = PresenceTracker()
    tracker.observe(PlayerJoined("A"))

    assert tracker.observe(PlayerLeft("Z")) is None
    assert tracker.observe(PlayerLeft("A")) is not None
    assert tracker.observe(PlayerLeft("A")) is None
    assert tracker.count == 0


def test_count_only_games_report_a_count_without_identities() -> None:
    tracker = PresenceTracker(max_players=4)

    first = tracker.observe(PlayerCount(2))
    same = tracker.observe(PlayerCount(2))
    clipped = tracker.observe(PlayerCount(9))

    assert first is not None and first.count == 2
    assert same is None
    assert clipped is not None and clipped.count == 4
    assert tracker.snapshot == PresenceSnapshot(frozenset(), 4)


def test_identities_take_precedence_over_counts() -> None:
    tracker = PresenceTracker()
    tracker.observe(PlayerCount(3))
    tracker.observe(PlayerJoined("A"))

    assert tracker.count == 1
    assert tracker.observe(PlayerCount(5)) is None


@pytest.mark.parametrize(
    "fact",
    [BackendReady(), SaveCompleted(1.0), JoinInfo(code="123456"), BackendVersion("1.0")],
)
def test_non_presence_facts_never_change_the_set(fact) -> None:
    tracker = PresenceTracker()
    tracker.observe(PlayerJoined("A"))

    assert tracker.observe(fact) is None
    assert tracker.count == 1


def test_join_beyond_max_players_is_rejected_not_illegal() -> None:
    tracker = PresenceTracker(max_players=1)
    tracker.observe(PlayerJoined("A"))

    assert tracker.observe(PlayerJoined("B")) is None
    assert tracker.count == 1
    assert tracker.rejected_joins == 1


def test_max_players_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_players"):
        PresenceTracker(max_players=0)


def test_snapshot_is_detached_from_tracker_state() -> None:
    tracker = PresenceTracker()
    tracker.observe(PlayerJoined("A"))
    snapshot = tracker.snapshot

    tracker.observe(PlayerLeft("A"))

    assert snapshot.players == {"A"}
    assert tracker.count == 0
