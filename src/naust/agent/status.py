"""The agent's status document: level-triggered truth, ``naust/v1alpha1``.

Events are hints; this is what a consumer reads to be correct again after
missing one. It is the agent's view of one backend, so its states are
STARTING/READY/DRAINING/STOPPED/FAILED; a world's SLEEPING and WAKING belong
to whoever orchestrates the agent.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from naust.agent.presence import PresenceTransition
from naust.agent.supervisor import BackendState, SaveFiles
from naust.games.facts import JoinInfo

API_VERSION = "naust/v1alpha1"
KIND = "BackendStatus"

ConditionStatus = Literal["True", "False", "Unknown"]


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(slots=True)
class Condition:
    """Kubernetes-shaped: a type, a tri-state status, a reason, and when it last changed."""

    type: str
    status: ConditionStatus = "Unknown"
    reason: str | None = None
    since: str | None = None

    def update(self, status: ConditionStatus, reason: str | None = None) -> bool:
        changed = status != self.status
        if changed:
            self.since = now_iso()
        self.status = status
        self.reason = reason
        return changed

    def as_dict(self) -> dict[str, str | None]:
        return {
            "type": self.type,
            "status": self.status,
            "reason": self.reason,
            "since": self.since,
        }


def _initial_conditions() -> dict[str, Condition]:
    return {
        "Ready": Condition("Ready", "False", "Starting"),
        "Draining": Condition("Draining", "False"),
        "SaveVerified": Condition("SaveVerified", "Unknown"),
        "VersionKnown": Condition("VersionKnown", "Unknown"),
    }


@dataclass(slots=True)
class AgentStatus:
    world: str
    game: str
    capabilities: dict[str, str | None]
    max_players: int
    save_files: SaveFiles
    sequence: int = 0
    state: BackendState = BackendState.STARTING
    conditions: dict[str, Condition] = field(default_factory=_initial_conditions)
    pid: int | None = None
    started_at: str | None = None
    version: str | None = None
    players: dict[str, str] = field(default_factory=dict)
    count: int = 0
    idle_since: str | None = None
    join: JoinInfo | None = None
    last_save_at: str | None = None
    last_save_ms: float | None = None
    game_extension: dict[str, Any] = field(default_factory=dict)

    def bump(self) -> int:
        self.sequence += 1
        return self.sequence

    def set_condition(self, type_: str, status: ConditionStatus, reason: str | None = None) -> bool:
        return self.conditions[type_].update(status, reason)

    def apply_transition(self, transition: PresenceTransition) -> None:
        when = now_iso()
        for name in transition.joined:
            self.players[name] = when
        for name in transition.left:
            self.players.pop(name, None)
        self.count = transition.count
        self.idle_since = when if self.count == 0 else None

    def note_save(self, duration_ms: float | None) -> None:
        self.last_save_at = now_iso()
        self.last_save_ms = duration_ms

    def file_sizes(self) -> list[dict[str, Any]]:
        return [
            {"path": str(path), "bytes": path.stat().st_size if path.exists() else None}
            for path in self.save_files.paths
        ]

    def document(self) -> dict[str, Any]:
        join: dict[str, Any] | None = None
        if self.join is not None:
            join = {"kind": self.join.kind}
            if self.join.code is not None:
                join["code"] = self.join.code
            else:
                join["address"] = self.join.address
                join["port"] = self.join.port
        return {
            "apiVersion": API_VERSION,
            "kind": KIND,
            "world": self.world,
            "game": self.game,
            "sequence": self.sequence,
            "observedAt": now_iso(),
            "state": self.state.value,
            "conditions": [c.as_dict() for c in self.conditions.values()],
            "backend": {"pid": self.pid, "startedAt": self.started_at, "version": self.version},
            "presence": {
                "count": self.count,
                "players": [{"id": name, "since": since} for name, since in self.players.items()],
                "quality": self.capabilities.get("presence"),
                "idleSince": self.idle_since,
                "maxPlayers": self.max_players,
            },
            "join": join,
            "save": {
                "lastCompletedAt": self.last_save_at,
                "lastDurationMs": self.last_save_ms,
                "files": self.file_sizes(),
            },
            "capabilities": dict(self.capabilities),
            "game_extension": {self.game: dict(self.game_extension)},
        }
