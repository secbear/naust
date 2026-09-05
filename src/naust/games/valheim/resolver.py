"""Valheim's grammar of arriving and leaving, turned into game-neutral facts.

The server never says "X left". It emits an identity-free ``RPC_Disconnect``,
then cleanup lines naming the departed *owner*, then closes the socket. A
failed login emits the same marker with no cleanup. The resolver keeps the
name-to-owner map and the open disconnect window that make attribution
possible, so the generic tracker never has to.

Policy: fail awake. When evidence is incomplete nobody is removed.
"""

from dataclasses import dataclass, field

from naust.games.facts import (
    BackendReady,
    BackendVersion,
    Fact,
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
)


@dataclass(slots=True)
class _PendingDisconnect:
    resolved_owner: int | None = None


@dataclass(slots=True)
class ValheimResolver:
    _owners_by_name: dict[str, int] = field(default_factory=dict)
    _pending: _PendingDisconnect | None = None

    def resolve(self, observation: object) -> tuple[Fact, ...]:
        match observation:
            case CharacterObserved(name=name, zdoid=zdoid):
                if zdoid.is_null:
                    return ()  # a death, not a leave
                if name in self._owners_by_name:
                    self._owners_by_name[name] = zdoid.owner  # respawn or reconnect
                    return ()
                self._owners_by_name[name] = zdoid.owner
                return (PlayerJoined(name),)
            case DisconnectMarkerObserved():
                self._pending = _PendingDisconnect()
                return ()
            case AbandonedZdoObserved(owner=owner):
                return self._attribute(owner)
            case SocketClosedObserved():
                self._pending = None
                return ()
            case ServerReadyObserved():
                return (BackendReady(),)
            case WorldSavedObserved(duration_ms=duration_ms):
                return (SaveCompleted(duration_ms),)
            case JoinCodeObserved(code=code):
                return (JoinInfo(code=code),)
            case VersionObserved(version=version):
                return (BackendVersion(version),)
            case _:
                return ()

    def _attribute(self, owner: int) -> tuple[Fact, ...]:
        pending = self._pending
        if pending is None or pending.resolved_owner is not None:
            return ()
        for name, known_owner in self._owners_by_name.items():
            if known_owner == owner:
                del self._owners_by_name[name]
                pending.resolved_owner = owner
                return (PlayerLeft(name),)
        return ()  # unknown owner: mid-stream start or failed login; never guess
