"""CloudEvents 1.0 envelopes for what the agent observed.

Every event carries ``naustsequence`` so a consumer can tell it missed one
and re-read status instead of trusting a gap.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from naust.agent.status import now_iso

TYPE_PREFIX = "io.naust."
SPEC_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class Event:
    """A short type such as ``backend.ready`` and its JSON-able data."""

    type: str
    data: dict[str, Any]


@dataclass(frozen=True, slots=True)
class EventFactory:
    source: str

    @classmethod
    def for_world(cls, host: str, world_id: str) -> "EventFactory":
        return cls(source=f"naust://{host}/worlds/{world_id}")

    def cloudevent(self, event: Event, sequence: int) -> dict[str, Any]:
        return {
            "specversion": SPEC_VERSION,
            "id": str(uuid.uuid4()),
            "source": self.source,
            "type": TYPE_PREFIX + event.type,
            "time": now_iso(),
            "datacontenttype": "application/json",
            "naustsequence": sequence,
            "data": event.data,
        }
