"""Tracker rules, one at a time, driven by hand-built semantic observations."""

import pytest

from naust.agent.observations import (
    AbandonedZdoObserved,
    CharacterObserved,
    DisconnectMarkerObserved,
    JoinCodeObserved,
    ServerReadyObserved,
    SocketClosedObserved,
    WorldSavedObserved,
    ZdoId,
)
from naust.agent.presence import PresenceSnapshot, PresenceTracker, PresenceTransition

A_OWNER = -900000000000000011
B_OWNER = 900000000000000012


def join(name: str, owner: int, object_id: int = 1) -> CharacterObserved:
    return CharacterObserved(name, ZdoId(owner, object_id))


def death(name: str) -> CharacterObserved:
    return CharacterObserved(name, ZdoId(0, 0))


def cleanup(owner: int, object_id: int = 43) -> AbandonedZdoObserved:
    return AbandonedZdoObserved(ZdoId(owner, object_id), owner=owner)


def leave(tracker: PresenceTracker, owner: int, connection_id: int = 1) -> list:
    """The full disconnect sequence as the server emits it."""

    return [
        tracker.observe(DisconnectMarkerObserved()),
        tracker.observe(cleanup(owner, 43)),
        tracker.observe(cleanup(owner, 42)),
        tracker.observe(SocketClosedObserved(connection_id)),
    ]


def test_fresh_tracker_is_empty() -> None:
    tracker = PresenceTracker()

    assert tracker.snapshot == PresenceSnapshot(frozenset())
    assert tracker.count == 0


def test_join_emits_one_transition() -> None:
    tracker = PresenceTracker()

    transition = tracker.observe(join("PLAYER_A", A_OWNER))

    assert transition == PresenceTransition(
        before=PresenceSnapshot(frozenset()),
        after=PresenceSnapshot(frozenset({"PLAYER_A"})),
    )
    assert transition.joined == {"PLAYER_A"}
    assert transition.left == frozenset()
    assert transition.count == 1


def test_death_is_not_a_leave() -> None:
    tracker = PresenceTracker()
    tracker.observe(join("PLAYER_B", B_OWNER))

    assert tracker.observe(death("PLAYER_B")) is None
    assert tracker.snapshot.players == {"PLAYER_B"}


def test_respawn_is_not_a_second_join() -> None:
    tracker = PresenceTracker()
    tracker.observe(join("PLAYER_B", B_OWNER, 1))
    tracker.observe(death("PLAYER_B"))

    assert tracker.observe(join("PLAYER_B", B_OWNER, 3)) is None
    assert tracker.count == 1


def test_repeated_join_evidence_is_silent() -> None:
    tracker = PresenceTracker()
    tracker.observe(join("PLAYER_A", A_OWNER))

    assert tracker.observe(join("PLAYER_A", A_OWNER)) is None
    assert tracker.count == 1


def test_disconnect_with_cleanup_removes_exactly_that_player() -> None:
    tracker = PresenceTracker()
    tracker.observe(join("PLAYER_A", A_OWNER))
    tracker.observe(join("PLAYER_B", B_OWNER))

    transitions = leave(tracker, B_OWNER)

    assert [t for t in transitions if t is not None] == [
        PresenceTransition(
            before=PresenceSnapshot(frozenset({"PLAYER_A", "PLAYER_B"})),
            after=PresenceSnapshot(frozenset({"PLAYER_A"})),
        )
    ]
    assert tracker.snapshot.players == {"PLAYER_A"}


def test_repeated_cleanup_cannot_remove_twice() -> None:
    tracker = PresenceTracker()
    tracker.observe(join("PLAYER_A", A_OWNER))
    tracker.observe(DisconnectMarkerObserved())
    assert tracker.observe(cleanup(A_OWNER, 1)) is not None

    for object_id in range(2, 12):
        assert tracker.observe(cleanup(A_OWNER, object_id)) is None
    assert tracker.count == 0


def test_failed_login_cannot_evict_a_player() -> None:
    """The dangerous naive rule: 'on RPC_Disconnect, remove someone'."""

    tracker = PresenceTracker()
    tracker.observe(join("PLAYER_A", A_OWNER))

    assert tracker.observe(DisconnectMarkerObserved()) is None
    assert tracker.observe(SocketClosedObserved(900000000000000008)) is None
    assert tracker.snapshot.players == {"PLAYER_A"}

    # PLAYER_A carries on as if nothing happened, then leaves normally.
    assert tracker.observe(death("PLAYER_A")) is None
    assert any(leave(tracker, A_OWNER))
    assert tracker.count == 0


def test_disconnect_marker_alone_never_evicts() -> None:
    tracker = PresenceTracker()
    tracker.observe(join("PLAYER_A", A_OWNER))

    assert tracker.observe(DisconnectMarkerObserved()) is None
    assert tracker.count == 1


def test_stale_disconnect_does_not_authorise_later_cleanup() -> None:
    """The socket boundary closes correlation; cleanup after it is not a leave."""

    tracker = PresenceTracker()
    tracker.observe(join("PLAYER_A", A_OWNER))
    tracker.observe(DisconnectMarkerObserved())
    tracker.observe(SocketClosedObserved(7))

    assert tracker.observe(cleanup(A_OWNER)) is None
    assert tracker.snapshot.players == {"PLAYER_A"}


def test_cleanup_without_disconnect_is_not_a_leave() -> None:
    tracker = PresenceTracker()
    tracker.observe(join("PLAYER_A", A_OWNER))

    assert tracker.observe(cleanup(A_OWNER)) is None
    assert tracker.count == 1


def test_one_disconnect_resolves_at_most_one_player() -> None:
    tracker = PresenceTracker()
    tracker.observe(join("PLAYER_A", A_OWNER))
    tracker.observe(join("PLAYER_B", B_OWNER))
    tracker.observe(DisconnectMarkerObserved())
    tracker.observe(cleanup(B_OWNER))

    # A second owner inside the same sequence would be a log we have never
    # seen. Do not let it evict a second player on one marker.
    assert tracker.observe(cleanup(A_OWNER)) is None
    assert tracker.snapshot.players == {"PLAYER_A"}


def test_reconnect_refreshes_owner_without_transition() -> None:
    tracker = PresenceTracker()
    tracker.observe(join("PLAYER_A", A_OWNER))

    assert tracker.observe(join("PLAYER_A", 42)) is None
    # The new owner is now the one that identifies the leave.
    assert any(leave(tracker, 42))
    assert tracker.count == 0


@pytest.mark.parametrize(
    "observation",
    [
        death("UNSEEN"),
        cleanup(12345),
        DisconnectMarkerObserved(),
        SocketClosedObserved(1),
        ServerReadyObserved(),
        WorldSavedObserved(1.0),
        JoinCodeObserved("123456"),
    ],
)
def test_mid_stream_input_is_a_safe_no_op(observation) -> None:
    tracker = PresenceTracker()

    assert tracker.observe(observation) is None
    assert tracker.count == 0


def test_mid_stream_positive_evidence_is_the_first_fact() -> None:
    tracker = PresenceTracker()
    tracker.observe(DisconnectMarkerObserved())
    tracker.observe(cleanup(999))
    tracker.observe(SocketClosedObserved(1))

    assert tracker.observe(join("PLAYER_A", A_OWNER)) is not None
    assert tracker.snapshot.players == {"PLAYER_A"}


def test_non_presence_observations_never_change_the_set() -> None:
    tracker = PresenceTracker()
    tracker.observe(join("PLAYER_A", A_OWNER))

    for observation in (ServerReadyObserved(), WorldSavedObserved(61.5), JoinCodeObserved("1")):
        assert tracker.observe(observation) is None
    assert tracker.count == 1


def test_interleaved_players() -> None:
    tracker = PresenceTracker()
    counts: list[int] = []
    script = [
        join("PLAYER_A", A_OWNER),
        join("PLAYER_B", B_OWNER),
        death("PLAYER_B"),
        join("PLAYER_B", B_OWNER, 3),
        death("PLAYER_A"),
        DisconnectMarkerObserved(),
        cleanup(B_OWNER),
        SocketClosedObserved(8),
        join("PLAYER_A", A_OWNER, 9),
        DisconnectMarkerObserved(),
        cleanup(A_OWNER),
        SocketClosedObserved(7),
    ]
    for observation in script:
        transition = tracker.observe(observation)
        if transition is not None:
            counts.append(transition.count)

    assert counts == [1, 2, 1, 0]


def test_join_beyond_max_players_is_rejected_not_illegal() -> None:
    tracker = PresenceTracker(max_players=1)
    tracker.observe(join("PLAYER_A", A_OWNER))

    assert tracker.observe(join("PLAYER_B", B_OWNER)) is None
    assert tracker.count == 1
    assert tracker.rejected_joins == 1


def test_max_players_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_players"):
        PresenceTracker(max_players=0)


def test_snapshot_is_detached_from_tracker_state() -> None:
    tracker = PresenceTracker()
    tracker.observe(join("PLAYER_A", A_OWNER))
    snapshot = tracker.snapshot

    leave(tracker, A_OWNER)

    assert snapshot.players == {"PLAYER_A"}
    assert tracker.count == 0
