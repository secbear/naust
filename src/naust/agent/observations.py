"""Semantic observations and the game-adapter contract.

This module is the boundary between untrusted log text and presence logic. It
knows nothing about any particular game's log grammar: concrete adapters such
as :mod:`naust.agent.valheim` import it, never the other way round.

Observations are literal facts a line reported. They are not transitions; the
presence tracker decides what an observation means given everything seen so
far. Each kind is its own immutable dataclass so that impossible combinations
(a disconnect marker with a player name, a character event without one) cannot
be constructed.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ZdoId:
    """A Valheim-style networked-object identity: signed owner and object number.

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
    """A disconnect began. Carries no identity; correlation happens later."""


@dataclass(frozen=True, slots=True)
class AbandonedZdoObserved:
    """Cleanup of an object left behind by a departed owner.

    Many of these follow one disconnect. The owner is the only field the
    tracker uses; the object identity is kept for diagnostics.
    """

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
    """A world save completed. Duration is kept because telemetry wants it."""

    duration_ms: float


@dataclass(frozen=True, slots=True)
class JoinCodeObserved:
    """A crossplay join code was published by the backend."""

    code: str


type PresenceObservation = (
    CharacterObserved | DisconnectMarkerObserved | AbandonedZdoObserved | SocketClosedObserved
)
"""Observations the presence tracker may act on."""

type Observation = PresenceObservation | ServerReadyObserved | WorldSavedObserved | JoinCodeObserved
"""Every observation an adapter can produce."""


class GameAdapter(Protocol):
    """What the agent needs from a game: one line in, at most one observation out.

    Implementations must be pure. They perform no I/O, keep no presence state,
    and never raise on malformed or truncated input; noise simply yields
    ``None``.
    """

    def parse_line(self, line: str) -> Observation | None: ...
