"""Grammar tests for the Valheim adapter: strings in, observations out."""

import pytest

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
from naust.agent.valheim import ValheimAdapter

adapter = ValheimAdapter()

PREFIX = "01/01/2026 23:10:38: "


@pytest.mark.parametrize(
    "line",
    [
        "",
        "   ",
        "\n",
        "Unloading 4 Unused Serialized files (Serialized files now loaded: 8)",
        f"{PREFIX}Got connection SteamID 900000000000000007",
        f"{PREFIX}Network version check, their:36, mine:36",
        f"{PREFIX}Server: New peer connected,sending global keys",
        f"{PREFIX} Connections 0 ZDOS:16260  sent:0 recv:0",
        f"{PREFIX}World save writing starting",
        "ZPlayFabMatchmaking::UnregisterServer - unregistering server now.",
        "> bash start_server.local.sh",
        "  at PlayFab.Party.PlayFabMultiplayerManager.Start () [0x00000] in <9>:0",
    ],
)
def test_noise_yields_no_observation(line: str) -> None:
    assert adapter.parse_line(line) is None


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (
            f"{PREFIX}Got character ZDOID from PLAYER_B : 900000000000000012:1",
            CharacterObserved("PLAYER_B", ZdoId(900000000000000012, 1)),
        ),
        (
            f"{PREFIX}Got character ZDOID from PLAYER_A : -900000000000000011:1",
            CharacterObserved("PLAYER_A", ZdoId(-900000000000000011, 1)),
        ),
        (
            f"{PREFIX}Got character ZDOID from PLAYER_B : 0:0",
            CharacterObserved("PLAYER_B", ZdoId(0, 0)),
        ),
        (
            f"{PREFIX}Got character ZDOID from Two Words : 5:7",
            CharacterObserved("Two Words", ZdoId(5, 7)),
        ),
        (
            "Got character ZDOID from NoPrefix : 5:7",
            CharacterObserved("NoPrefix", ZdoId(5, 7)),
        ),
        (f"{PREFIX}RPC_Disconnect", DisconnectMarkerObserved()),
        (
            f"{PREFIX}Destroying abandoned non persistent zdo 900000000000000012:43 "
            "owner 900000000000000012",
            AbandonedZdoObserved(ZdoId(900000000000000012, 43), owner=900000000000000012),
        ),
        (
            f"{PREFIX}Destroying abandoned non persistent zdo -900000000000000011:195 "
            "owner -900000000000000011",
            AbandonedZdoObserved(ZdoId(-900000000000000011, 195), owner=-900000000000000011),
        ),
        (
            f"{PREFIX}Closing socket 900000000000000008",
            SocketClosedObserved(connection_id=900000000000000008),
        ),
        (f"{PREFIX}Game server connected", ServerReadyObserved()),
        (f"{PREFIX}World saved ( 61.499ms )", WorldSavedObserved(duration_ms=61.499)),
        (f"{PREFIX}World saved ( 3216ms )", WorldSavedObserved(duration_ms=3216.0)),
        (
            f'{PREFIX}Session "Midgard" with join code 604510 and IP 1.2.3.4:2456 '
            "is active with 0 player(s)",
            JoinCodeObserved(code="604510"),
        ),
    ],
)
def test_supported_lines(line: str, expected: Observation) -> None:
    assert adapter.parse_line(line) == expected


def test_null_zdoid_is_explicit() -> None:
    assert ZdoId(0, 0).is_null
    assert not ZdoId(0, 1).is_null
    assert not ZdoId(-3, 0).is_null


@pytest.mark.parametrize(
    "line",
    [
        # character line: missing name, missing halves, non-numeric, negative object
        f"{PREFIX}Got character ZDOID from  : 5:7",
        f"{PREFIX}Got character ZDOID from PLAYER_A : 5",
        f"{PREFIX}Got character ZDOID from PLAYER_A : :7",
        f"{PREFIX}Got character ZDOID from PLAYER_A : x:7",
        f"{PREFIX}Got character ZDOID from PLAYER_A : 5:-7",
        f"{PREFIX}Got character ZDOID from PLAYER_A : 5:7 trailing",
        f"{PREFIX}Got character ZDOID from PLAYER_A :",
        # truncated prefixes
        f"{PREFIX}Got character ZDOID fro",
        f"{PREFIX}RPC_Disconnec",
        f"{PREFIX}RPC_Disconnected",
        f"{PREFIX}Destroying abandoned non persistent zdo 9:43 owner",
        f"{PREFIX}Destroying abandoned non persistent zdo 9:43 owner x",
        f"{PREFIX}Closing socket",
        f"{PREFIX}Closing socket -1",
        f"{PREFIX}World saved ( ms )",
        f"{PREFIX}World saved ( 61.499 )",
        f"{PREFIX}Game server connected to nothing",
        # join code must be exactly six digits
        f"{PREFIX}Session x with join code 60451 and IP 1.2.3.4",
        f"{PREFIX}Session x with join code 6045101 and IP 1.2.3.4",
        # arbitrary unicode
        "ᚱᚢᚾᛖᛋ ᚨᚱᛖ ᚾᛟᛏ ᛚᛟᚷ ᛚᛁᚾᛖᛋ",
        "\x00\xff garbage",
    ],
)
def test_malformed_neighbours_are_noise(line: str) -> None:
    assert adapter.parse_line(line) is None


def test_adapter_is_stateless() -> None:
    line = f"{PREFIX}Got character ZDOID from PLAYER_A : 5:7"
    assert adapter.parse_line(line) == adapter.parse_line(line)
    assert ValheimAdapter() == adapter
