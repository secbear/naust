"""Valheim's grammar of arriving and leaving, turned into game-neutral facts.

The server never says "X left". Every way a peer goes (a clean quit's
``RPC_Disconnect``, a dropped connection's ``ZRpc timeout detected``, a kick)
ends in the same removal, and the removal sweeps away every non-persistent
object whose owner is no longer connected, logging each with that owner.
The sweep runs only when a peer is removed and only touches owners that are
gone, so a cleanup line naming a present player's owner is the game's own
statement that the player left, whatever preceded it. The resolver keeps the
name-to-owner map that makes that attribution possible.

Ten-minutely, the server also logs how many handshaken peers it has. Zero
names everybody: the server is empty. Any other number names nobody, so it
only matters when no names are known (the agent started mid-session).

Policy: fail awake. When evidence is incomplete nobody is removed.
"""

from dataclasses import dataclass, field

from naust.games.facts import (
    BackendReady,
    BackendVersion,
    Fact,
    JoinInfo,
    PlayerCount,
    PlayerJoined,
    PlayerLeft,
    SaveCompleted,
    SaveFailed,
)
from naust.games.valheim.observer import (
    AbandonedZdoObserved,
    CharacterObserved,
    JoinCodeObserved,
    PeerCountObserved,
    SaveFailedObserved,
    ServerReadyObserved,
    VersionObserved,
    WorldSavedObserved,
)


@dataclass(slots=True)
class ValheimResolver:
    _owners_by_name: dict[str, int] = field(default_factory=dict)

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
            case AbandonedZdoObserved(owner=owner):
                return self._departed(owner)
            case PeerCountObserved(count=0):
                gone = sorted(self._owners_by_name)
                self._owners_by_name.clear()
                return (*(PlayerLeft(name) for name in gone), PlayerCount(0))
            case PeerCountObserved(count=count):
                return (PlayerCount(count),)
            case ServerReadyObserved():
                return (BackendReady(),)
            case WorldSavedObserved(duration_ms=duration_ms):
                return (SaveCompleted(duration_ms),)
            case SaveFailedObserved(detail=detail):
                return (SaveFailed(detail),)
            case JoinCodeObserved(code=code):
                return (JoinInfo(code=code),)
            case VersionObserved(version=version):
                return (BackendVersion(version),)
            case _:
                return ()

    def _departed(self, owner: int) -> tuple[Fact, ...]:
        for name, known_owner in self._owners_by_name.items():
            if known_owner == owner:
                del self._owners_by_name[name]
                return (PlayerLeft(name),)
        # Unowned (0), a stranger (mid-stream start or failed login), or a player
        # already counted out by an earlier line of the same sweep: never guess.
        return ()
