"""Valheim's arriving-and-leaving grammar, resolved into facts one rule at a time."""

import pytest

from naust.games.facts import (
    BackendReady,
    BackendVersion,
    JoinInfo,
    PlayerJoined,
    PlayerLeft,
    SaveCompleted,
)
from naust.games.valheim.observer import (
    AbandonedZdoObserved,
    CharacterObserved,
    DisconnectMarkerObserved,
    JoinCodeObserved,
    ServerReadyObserved,
    SocketClosedObserved,
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


def leave(resolver: ValheimResolver, owner: int, connection_id: int = 1) -> list:
    facts: list = []
    for observation in (
        DisconnectMarkerObserved(),
        cleanup(owner, 43),
        cleanup(owner, 42),
        SocketClosedObserved(connection_id),
    ):
        facts.extend(resolver.resolve(observation))
    return facts


def test_first_character_is_a_join() -> None:
    assert ValheimResolver().resolve(join("PLAYER_A", A_OWNER)) == (PlayerJoined("PLAYER_A"),)


def test_death_and_respawn_are_silent() -> None:
    resolver = ValheimResolver()
    resolver.resolve(join("PLAYER_B", B_OWNER, 1))

    assert resolver.resolve(death("PLAYER_B")) == ()
    assert resolver.resolve(join("PLAYER_B", B_OWNER, 3)) == ()


def test_disconnect_with_cleanup_resolves_exactly_one_leave() -> None:
    resolver = ValheimResolver()
    resolver.resolve(join("PLAYER_A", A_OWNER))
    resolver.resolve(join("PLAYER_B", B_OWNER))

    assert leave(resolver, B_OWNER) == [PlayerLeft("PLAYER_B")]


def test_failed_login_cannot_evict_a_player() -> None:
    """The dangerous naive rule: 'on RPC_Disconnect, remove someone'."""

    resolver = ValheimResolver()
    resolver.resolve(join("PLAYER_A", A_OWNER))

    assert resolver.resolve(DisconnectMarkerObserved()) == ()
    assert resolver.resolve(SocketClosedObserved(8)) == ()
    assert resolver.resolve(death("PLAYER_A")) == ()
    assert leave(resolver, A_OWNER) == [PlayerLeft("PLAYER_A")]


def test_stale_disconnect_does_not_authorise_later_cleanup() -> None:
    resolver = ValheimResolver()
    resolver.resolve(join("PLAYER_A", A_OWNER))
    resolver.resolve(DisconnectMarkerObserved())
    resolver.resolve(SocketClosedObserved(7))

    assert resolver.resolve(cleanup(A_OWNER)) == ()


def test_cleanup_without_disconnect_is_not_a_leave() -> None:
    resolver = ValheimResolver()
    resolver.resolve(join("PLAYER_A", A_OWNER))

    assert resolver.resolve(cleanup(A_OWNER)) == ()


def test_one_marker_resolves_at_most_one_player() -> None:
    resolver = ValheimResolver()
    resolver.resolve(join("PLAYER_A", A_OWNER))
    resolver.resolve(join("PLAYER_B", B_OWNER))
    resolver.resolve(DisconnectMarkerObserved())

    assert resolver.resolve(cleanup(B_OWNER)) == (PlayerLeft("PLAYER_B"),)
    assert resolver.resolve(cleanup(A_OWNER)) == ()


def test_reconnect_refreshes_owner() -> None:
    resolver = ValheimResolver()
    resolver.resolve(join("PLAYER_A", A_OWNER))

    assert resolver.resolve(join("PLAYER_A", 42)) == ()
    assert leave(resolver, 42) == [PlayerLeft("PLAYER_A")]


def test_player_can_rejoin_after_leaving() -> None:
    resolver = ValheimResolver()
    resolver.resolve(join("PLAYER_A", A_OWNER))
    leave(resolver, A_OWNER)

    assert resolver.resolve(join("PLAYER_A", A_OWNER, 9)) == (PlayerJoined("PLAYER_A"),)


@pytest.mark.parametrize(
    "observation",
    [death("UNSEEN"), cleanup(12345), DisconnectMarkerObserved(), SocketClosedObserved(1)],
)
def test_mid_stream_input_is_silent(observation) -> None:
    assert ValheimResolver().resolve(observation) == ()


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
