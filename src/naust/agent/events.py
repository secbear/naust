"""CloudEvents 1.0 envelopes for what the agent observed.

Every event carries ``naustrun``, an id for this agent process, and
``naustsequence``, which counts from 1 within a run. A consumer that sees a
new run resets; within a run it can tell it missed something and re-read
status instead of trusting a gap.
"""

import uuid
from dataclasses import dataclass, field
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
    run: str = field(default_factory=lambda: uuid.uuid4().hex)

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
            "naustrun": self.run,
            "naustsequence": sequence,
            "data": event.data,
        }
