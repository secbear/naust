"""Presence tracking: what observations mean given everything seen so far.

The tracker consumes semantic observations, never raw text, and mutates one
private model of the world. Callers receive immutable snapshots and
transitions. Its safety policy is *fail awake*: when evidence is incomplete
it keeps a player present rather than guessing that somebody left, because
an empty server kept awake costs money while a false-empty server can start
a shutdown under somebody's feet.
"""

from dataclasses import dataclass, field

from naust.agent.observations import (
    AbandonedZdoObserved,
    CharacterObserved,
    DisconnectMarkerObserved,
    Observation,
    SocketClosedObserved,
)


@dataclass(frozen=True, slots=True)
class PresenceSnapshot:
    """Read-only view of who is present."""

    players: frozenset[str]

    @property
    def count(self) -> int:
        return len(self.players)


@dataclass(frozen=True, slots=True)
class PresenceTransition:
    """A genuine change in the player set. Emitted only when before != after."""

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
class _PendingDisconnect:
    """Correlation state for one identity-free disconnect sequence.

    Opened by the disconnect marker, closed by the socket boundary. Once a
    cleanup owner has been matched to a present player, the sequence is
    resolved and further cleanup lines for that owner are no-ops.
    """

    resolved_owner: int | None = None


@dataclass(slots=True)
class PresenceTracker:
    """Stateful interpreter of presence observations for one world.

    ``max_players`` is the bound the game itself enforces. A join that would
    exceed it means the tracker's model is already wrong; the policy is to
    ignore that join, count it in ``rejected_joins`` for diagnostics, and
    keep every invariant true rather than construct illegal state.
    """

    max_players: int = 10
    rejected_joins: int = field(default=0, init=False)
    _owners_by_name: dict[str, int] = field(default_factory=dict, init=False)
    _pending_disconnect: _PendingDisconnect | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if self.max_players < 1:
            raise ValueError("max_players must be at least 1")

    @property
    def snapshot(self) -> PresenceSnapshot:
        return PresenceSnapshot(frozenset(self._owners_by_name))

    @property
    def count(self) -> int:
        return len(self._owners_by_name)

    def observe(self, observation: Observation) -> PresenceTransition | None:
        """Consume one observation; return a transition only if presence changed."""

        before = self.snapshot
        match observation:
            case CharacterObserved(name=name, zdoid=zdoid):
                self._observe_character(name, zdoid.owner, zdoid.is_null)
            case DisconnectMarkerObserved():
                self._pending_disconnect = _PendingDisconnect()
            case AbandonedZdoObserved(owner=owner):
                self._observe_cleanup(owner)
            case SocketClosedObserved():
                self._pending_disconnect = None
            case _:
                pass
        after = self.snapshot
        self._check_invariants()
        if after == before:
            return None
        return PresenceTransition(before=before, after=after)

    def _observe_character(self, name: str, owner: int, is_null: bool) -> None:
        if is_null:
            # A death: the character has no live object right now. Not a leave.
            return
        if name in self._owners_by_name:
            # Respawn, or a reconnect that changed the owner. Refresh, no join.
            self._owners_by_name[name] = owner
            return
        if len(self._owners_by_name) >= self.max_players:
            self.rejected_joins += 1
            return
        self._owners_by_name[name] = owner

    def _observe_cleanup(self, owner: int) -> None:
        pending = self._pending_disconnect
        if pending is None or pending.resolved_owner is not None:
            # No open disconnect, or this one already evicted somebody.
            return
        for name, known_owner in self._owners_by_name.items():
            if known_owner == owner:
                del self._owners_by_name[name]
                pending.resolved_owner = owner
                return
        # Unknown owner: mid-stream start or a failed login. Never guess.

    def _check_invariants(self) -> None:
        if not 0 <= len(self._owners_by_name) <= self.max_players:  # pragma: no cover
            raise AssertionError("presence tracker violated its own bounds")
