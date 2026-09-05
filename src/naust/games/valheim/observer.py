"""Valheim dedicated-server log observer: one line to one observation.

Every pattern is an observation of a specific server build, not an API. The
recorded evidence is ``tests/fixtures/valheim/presence-session.log`` (server
engine 6000.0.61f1, game l-0.221.12, network version 36). Re-verify each
pattern against a fresh capture whenever the game updates.
"""

import re
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class ZdoId:
    """A networked-object identity: signed owner and object number.

    ``0:0`` has one explicit meaning — the named character currently has no
    live object — which the game uses to report a death.
    """

    owner: int
    object_id: int

    @property
    def is_null(self) -> bool:
        return self.owner == 0 and self.object_id == 0


@dataclass(frozen=True, slots=True)
class CharacterObserved:
    """A named character was bound to a ZDOID, or to ``0:0`` on death."""

    name: str
    zdoid: ZdoId


@dataclass(frozen=True, slots=True)
class DisconnectMarkerObserved:
    """A disconnect began. Carries no identity; the resolver correlates it."""


@dataclass(frozen=True, slots=True)
class AbandonedZdoObserved:
    """Cleanup of an object left behind by a departed owner. Many per disconnect."""

    zdoid: ZdoId
    owner: int


@dataclass(frozen=True, slots=True)
class SocketClosedObserved:
    """A connection's socket closed. Ends the current disconnect sequence."""

    connection_id: int


@dataclass(frozen=True, slots=True)
class ServerReadyObserved:
    """The backend announced it is accepting players."""


@dataclass(frozen=True, slots=True)
class WorldSavedObserved:
    duration_ms: float


@dataclass(frozen=True, slots=True)
class JoinCodeObserved:
    code: str


@dataclass(frozen=True, slots=True)
class VersionObserved:
    version: str
    network_version: int


type ValheimObservation = (
    CharacterObserved
    | DisconnectMarkerObserved
    | AbandonedZdoObserved
    | SocketClosedObserved
    | ServerReadyObserved
    | WorldSavedObserved
    | JoinCodeObserved
    | VersionObserved
)

# ``01/01/2026 23:07:32: `` — most, not all, lines carry a local timestamp.
# It is stripped rather than parsed because no caller needs it yet.
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
_VERSION: Final = re.compile(
    r"Valheim version: ?(?P<version>\S+) \(network version (?P<network_version>\d+)\)"
)
# Not present in the recorded fixture, which ran without a working PlayFab
# plugin. The shape comes from crossplay hosting documentation:
#   Session "Name" with join code 123456 and IP 1.2.3.4:2456 is active with 0 player(s)
# Treat it as unverified until a crossplay capture confirms it.
_JOIN_CODE: Final = re.compile(r"with join code (?P<code>\d{6})(?!\d)")


@dataclass(frozen=True, slots=True)
class ValheimObserver:
    """Pure line parser. Whole-message matches after the timestamp prefix."""

    def parse_line(self, line: str) -> ValheimObservation | None:
        message = _TIMESTAMP_PREFIX.sub("", line, count=1).strip()
        if not message:
            return None

        if match := _CHARACTER.fullmatch(message):
            return CharacterObserved(
                name=match["name"],
                zdoid=ZdoId(owner=int(match["owner"]), object_id=int(match["object_id"])),
            )
        if _DISCONNECT_MARKER.fullmatch(message):
            return DisconnectMarkerObserved()
        if match := _ABANDONED_ZDO.fullmatch(message):
            return AbandonedZdoObserved(
                zdoid=ZdoId(owner=int(match["owner"]), object_id=int(match["object_id"])),
                owner=int(match["cleanup_owner"]),
            )
        if match := _SOCKET_CLOSED.fullmatch(message):
            return SocketClosedObserved(connection_id=int(match["connection_id"]))
        if _SERVER_READY.fullmatch(message):
            return ServerReadyObserved()
        if match := _WORLD_SAVED.fullmatch(message):
            return WorldSavedObserved(duration_ms=float(match["duration_ms"]))
        if match := _VERSION.fullmatch(message):
            return VersionObserved(match["version"], int(match["network_version"]))
        if match := _JOIN_CODE.search(message):
            return JoinCodeObserved(code=match["code"])
        return None
