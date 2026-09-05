"""Game-neutral facts, and the two contracts every game implements.

An **observer** turns one unit of raw input (a log line, a query reply) into
a game-typed observation, or nothing. A **resolver** turns those observations
into the facts below, holding whatever state the game's grammar needs to make
them true. Nothing above the resolver knows a game's words.
"""

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True, slots=True)
class PlayerJoined:
    player: str


@dataclass(frozen=True, slots=True)
class PlayerLeft:
    player: str


@dataclass(frozen=True, slots=True)
class PlayerCount:
    """A count from a source that reveals no identities (a query protocol)."""

    count: int


@dataclass(frozen=True, slots=True)
class BackendReady:
    """The backend announced it accepts players."""


@dataclass(frozen=True, slots=True)
class SaveCompleted:
    duration_ms: float | None = None


@dataclass(frozen=True, slots=True)
class JoinInfo:
    """How players reach this backend: a join code, or an address and port."""

    code: str | None = None
    address: str | None = None
    port: int | None = None

    @property
    def kind(self) -> Literal["code", "address"]:
        return "code" if self.code is not None else "address"


@dataclass(frozen=True, slots=True)
class BackendVersion:
    version: str


type PresenceFact = PlayerJoined | PlayerLeft | PlayerCount
type Fact = PresenceFact | BackendReady | SaveCompleted | JoinInfo | BackendVersion


class Observer(Protocol):
    """Pure: one line in, at most one game-typed observation out, never raises."""

    def parse_line(self, line: str) -> object | None: ...


class Resolver(Protocol):
    """Stateful: observations in, zero or more facts out."""

    def resolve(self, observation: object) -> tuple[Fact, ...]: ...
