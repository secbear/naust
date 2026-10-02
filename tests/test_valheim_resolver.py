"""Valheim's arriving-and-leaving grammar, resolved into facts one rule at a time."""

import pytest

from naust.games.facts import (
    BackendReady,
    BackendVersion,
    JoinInfo,
    PlayerCount,
    PlayerJoined,
    PlayerLeft,
    SaveCompleted,
)
from naust.games.valheim.observer import (
    AbandonedZdoObserved,
    CharacterObserved,
    JoinCodeObserved,
    PeerCountObserved,
    ServerReadyObserved,
    VersionObserved,
    WorldSavedObserved,
    ZdoId,
)
from naust.games.valheim.resolver import ValheimResolver

A_OWNER = -900000000000000011
B_OWNER = 900000000000000012


def join(name: str, owner: int, object_id: int = 1) -> CharacterObserved:
    return CharacterObserved(name, ZdoId(owner, object_id))


def death(name: str) -> CharacterObserved:
    return CharacterObserved(name, ZdoId(0, 0))


def cleanup(owner: int, object_id: int = 43) -> AbandonedZdoObserved:
    return AbandonedZdoObserved(ZdoId(owner, object_id), owner=owner)


def sweep(resolver: ValheimResolver, owner: int, *object_ids: int) -> list:
    """The removal sweep for one departed owner: several cleanup lines."""
    facts: list = []
    for object_id in object_ids or (43, 42):
        facts.extend(resolver.resolve(cleanup(owner, object_id)))
    return facts


def test_first_character_is_a_join() -> None:
    assert ValheimResolver().resolve(join("PLAYER_A", A_OWNER)) == (PlayerJoined("PLAYER_A"),)


def test_death_and_respawn_are_silent() -> None:
    resolver = ValheimResolver()
    resolver.resolve(join("PLAYER_B", B_OWNER, 1))

    assert resolver.resolve(death("PLAYER_B")) == ()
    assert resolver.resolve(join("PLAYER_B", B_OWNER, 3)) == ()


def test_cleanup_of_a_present_owner_is_exactly_one_leave() -> None:
    """Quit, timeout, or kick: every removal ends in this sweep, and it only
    touches owners who are no longer connected."""

    resolver = ValheimResolver()
    resolver.resolve(join("PLAYER_A", A_OWNER))
    resolver.resolve(join("PLAYER_B", B_OWNER))

    assert sweep(resolver, B_OWNER, 43, 42, 32, 40) == [PlayerLeft("PLAYER_B")]


def test_two_timeouts_back_to_back_are_two_leaves() -> None:
    """The 2026-09-29 incident: both crossplay players dropped two seconds
    apart and neither removal was preceded by RPC_Disconnect."""

    resolver = ValheimResolver()
    resolver.resolve(join("PLAYER_A", A_OWNER))
    resolver.resolve(join("PLAYER_B", B_OWNER))

    assert sweep(resolver, B_OWNER, 5385, 13118) == [PlayerLeft("PLAYER_B")]
    assert sweep(resolver, A_OWNER, 7035) == [PlayerLeft("PLAYER_A")]


@pytest.mark.parametrize("stranger", [12345, 0])
def test_cleanup_of_a_stranger_or_of_nobody_removes_no_one(stranger: int) -> None:
    """Mid-stream starts and failed logins leave owners nobody joined as;
    unowned objects are swept with owner 0."""

    resolver = ValheimResolver()
    resolver.resolve(join("PLAYER_A", A_OWNER))

    assert sweep(resolver, stranger) == []
    assert resolver.resolve(death("PLAYER_A")) == ()
    assert sweep(resolver, A_OWNER) == [PlayerLeft("PLAYER_A")]


def test_a_player_who_quits_while_dead_still_leaves() -> None:
    resolver = ValheimResolver()
    resolver.resolve(join("PLAYER_A", A_OWNER, 7))
    resolver.resolve(death("PLAYER_A"))

    assert sweep(resolver, A_OWNER, 12) == [PlayerLeft("PLAYER_A")]


def test_reconnect_refreshes_owner() -> None:
    resolver = ValheimResolver()
    resolver.resolve(join("PLAYER_A", A_OWNER))

    assert resolver.resolve(join("PLAYER_A", 42)) == ()
    assert sweep(resolver, 42) == [PlayerLeft("PLAYER_A")]


def test_late_cleanup_of_an_old_connection_cannot_evict_the_new_one() -> None:
    """A player reconnects before the server times out their old peer."""

    resolver = ValheimResolver()
    resolver.resolve(join("PLAYER_A", A_OWNER))
    resolver.resolve(join("PLAYER_A", 42))

    assert sweep(resolver, A_OWNER) == []


def test_player_can_rejoin_after_leaving() -> None:
    resolver = ValheimResolver()
    resolver.resolve(join("PLAYER_A", A_OWNER))
    sweep(resolver, A_OWNER)

    assert resolver.resolve(join("PLAYER_A", A_OWNER, 9)) == (PlayerJoined("PLAYER_A"),)


def test_a_zero_peer_count_names_everybody() -> None:
    """The backstop for any removal whose cleanup the log never showed."""

    resolver = ValheimResolver()
    resolver.resolve(join("PLAYER_B", B_OWNER))
    resolver.resolve(join("PLAYER_A", A_OWNER))

    assert resolver.resolve(PeerCountObserved(0)) == (
        PlayerLeft("PLAYER_A"),
        PlayerLeft("PLAYER_B"),
        PlayerCount(0),
    )
    assert sweep(resolver, A_OWNER) == []
    assert resolver.resolve(join("PLAYER_A", A_OWNER, 2)) == (PlayerJoined("PLAYER_A"),)


def test_a_nonzero_peer_count_names_nobody() -> None:
    resolver = ValheimResolver()
    resolver.resolve(join("PLAYER_A", A_OWNER))
    resolver.resolve(join("PLAYER_B", B_OWNER))

    assert resolver.resolve(PeerCountObserved(1)) == (PlayerCount(1),)
    assert sweep(resolver, B_OWNER) == [PlayerLeft("PLAYER_B")]


@pytest.mark.parametrize("observation", [death("UNSEEN"), cleanup(12345), cleanup(0)])
def test_mid_stream_input_is_silent(observation) -> None:
    assert ValheimResolver().resolve(observation) == ()


@pytest.mark.parametrize(("count"), [0, 3])
def test_mid_stream_peer_count_is_passed_on(count: int) -> None:
    assert ValheimResolver().resolve(PeerCountObserved(count)) == (PlayerCount(count),)


@pytest.mark.parametrize(
    ("observation", "fact"),
    [
        (ServerReadyObserved(), BackendReady()),
        (WorldSavedObserved(61.5), SaveCompleted(61.5)),
        (JoinCodeObserved("604510"), JoinInfo(code="604510")),
        (VersionObserved("l-0.221.12", 36), BackendVersion("l-0.221.12")),
    ],
)
def test_lifecycle_observations_map_to_facts(observation, fact) -> None:
    assert ValheimResolver().resolve(observation) == (fact,)


def test_unknown_observation_is_ignored() -> None:
    assert ValheimResolver().resolve(object()) == ()
