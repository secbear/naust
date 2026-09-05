"""Presence tracking over game-neutral facts.

The tracker knows sets and bounds. It does not know how any game says
"joined" or "left"; a resolver already decided that. Callers receive
immutable snapshots and a transition only when the player set — or, for
count-only games, the count — genuinely changed.
"""

from dataclasses import dataclass, field

from naust.games.facts import Fact, PlayerCount, PlayerJoined, PlayerLeft


@dataclass(frozen=True, slots=True)
class PresenceSnapshot:
    """Read-only view of who is present. ``count`` may exceed ``len(players)``
    for games that only reveal a number."""

    players: frozenset[str]
    count: int


@dataclass(frozen=True, slots=True)
class PresenceTransition:
    before: PresenceSnapshot
    after: PresenceSnapshot

    @property
    def joined(self) -> frozenset[str]:
        return self.after.players - self.before.players

    @property
    def left(self) -> frozenset[str]:
        return self.before.players - self.after.players

    @property
    def count(self) -> int:
        return self.after.count


@dataclass(slots=True)
class PresenceTracker:
    """``max_players`` is the bound the game itself enforces. A join beyond it
    means the model is already wrong; it is counted in ``rejected_joins`` and
    ignored so every invariant stays true."""

    max_players: int = 10
    rejected_joins: int = field(default=0, init=False)
    _players: set[str] = field(default_factory=set, init=False)
    _count_only: int | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if self.max_players < 1:
            raise ValueError("max_players must be at least 1")

    @property
    def snapshot(self) -> PresenceSnapshot:
        return PresenceSnapshot(frozenset(self._players), self.count)

    @property
    def count(self) -> int:
        if self._count_only is not None and not self._players:
            return self._count_only
        return len(self._players)

    def observe(self, fact: Fact) -> PresenceTransition | None:
        before = self.snapshot
        match fact:
            case PlayerJoined(player=player):
                if player not in self._players:
                    if len(self._players) >= self.max_players:
                        self.rejected_joins += 1
                    else:
                        self._players.add(player)
                        self._count_only = None
            case PlayerLeft(player=player):
                self._players.discard(player)
            case PlayerCount(count=count):
                if not self._players:
                    self._count_only = max(0, min(count, self.max_players))
            case _:
                pass
        after = self.snapshot
        if not 0 <= after.count <= self.max_players:  # pragma: no cover
            raise AssertionError("presence tracker violated its own bounds")
        return None if after == before else PresenceTransition(before, after)
