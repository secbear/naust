"""Compose an adapter and a tracker over a stream of lines.

This is the loop the live Agent will run against a subprocess and the replay
CLI runs against a file. It imports no game-specific module.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from naust.agent.observations import GameAdapter, Observation
from naust.agent.presence import PresenceTracker, PresenceTransition


@dataclass(frozen=True, slots=True)
class ReplayEvent:
    """One line that produced an observation, and any transition it caused."""

    line_number: int
    observation: Observation
    transition: PresenceTransition | None


def replay(
    lines: Iterable[str],
    adapter: GameAdapter,
    tracker: PresenceTracker,
) -> Iterator[ReplayEvent]:
    """Feed lines through the adapter into the tracker, yielding what mattered."""

    for line_number, line in enumerate(lines, start=1):
        observation = adapter.parse_line(line)
        if observation is None:
            continue
        yield ReplayEvent(line_number, observation, tracker.observe(observation))
