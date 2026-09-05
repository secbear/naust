"""Valheim dedicated-server log adapter.

Every pattern here is an observation of a specific server build, not an API.
The recorded evidence is ``tests/fixtures/valheim/presence-session.log``
(server engine 6000.0.61f1, network version 36). Re-verify each pattern
against a fresh capture whenever the game updates.
"""

import re
from dataclasses import dataclass
from typing import Final

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

# ``01/01/2026 23:07:32: `` — the server prefixes most, but not all, lines with a
# local timestamp. The message is what carries meaning; the timestamp is
# stripped rather than parsed because no caller needs it yet.
_TIMESTAMP_PREFIX: Final = re.compile(r"^\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}:\s*")

_CHARACTER: Final = re.compile(
    r"Got character ZDOID from (?P<name>.+) : (?P<owner>-?\d+):(?P<object_id>\d+)"
)
_DISCONNECT_MARKER: Final = re.compile(r"RPC_Disconnect")
_ABANDONED_ZDO: Final = re.compile(
    r"Destroying abandoned non persistent zdo "
    r"(?P<owner>-?\d+):(?P<object_id>\d+) owner (?P<cleanup_owner>-?\d+)"
)
_SOCKET_CLOSED: Final = re.compile(r"Closing socket (?P<connection_id>\d+)")
_SERVER_READY: Final = re.compile(r"Game server connected")
_WORLD_SAVED: Final = re.compile(r"World saved \( (?P<duration_ms>\d+(?:\.\d+)?)ms \)")
# Not present in the recorded fixture, which ran without a working PlayFab
# plugin. The shape comes from crossplay hosting documentation:
#   Session "Name" with join code 123456 and IP 1.2.3.4:2456 is active with 0 player(s)
# Treat it as unverified until a crossplay capture confirms it.
_JOIN_CODE: Final = re.compile(r"with join code (?P<code>\d{6})(?!\d)")


def _parse_character(match: re.Match[str]) -> Observation:
    return CharacterObserved(
        name=match["name"],
        zdoid=ZdoId(owner=int(match["owner"]), object_id=int(match["object_id"])),
    )


def _parse_abandoned(match: re.Match[str]) -> Observation:
    return AbandonedZdoObserved(
        zdoid=ZdoId(owner=int(match["owner"]), object_id=int(match["object_id"])),
        owner=int(match["cleanup_owner"]),
    )


@dataclass(frozen=True, slots=True)
class ValheimAdapter:
    """Pure line parser for the Valheim dedicated server.

    Patterns are matched against the whole message after the timestamp prefix
    is removed, so a line that merely mentions a keyword inside other text is
    still noise. The join-code pattern is the one exception: it is searched,
    because the line around it is long and its exact wording is unverified.
    """

    def parse_line(self, line: str) -> Observation | None:
        message = _TIMESTAMP_PREFIX.sub("", line, count=1).strip()
        if not message:
            return None

        if match := _CHARACTER.fullmatch(message):
            return _parse_character(match)
        if _DISCONNECT_MARKER.fullmatch(message):
            return DisconnectMarkerObserved()
        if match := _ABANDONED_ZDO.fullmatch(message):
            return _parse_abandoned(match)
        if match := _SOCKET_CLOSED.fullmatch(message):
            return SocketClosedObserved(connection_id=int(match["connection_id"]))
        if _SERVER_READY.fullmatch(message):
            return ServerReadyObserved()
        if match := _WORLD_SAVED.fullmatch(message):
            return WorldSavedObserved(duration_ms=float(match["duration_ms"]))
        if match := _JOIN_CODE.search(message):
            return JoinCodeObserved(code=match["code"])
        return None
