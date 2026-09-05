"""Compose observer, resolver, and tracker over a stream of lines.

The same loop runs against a subprocess in the supervisor and against a file
in the replay CLI. It imports no game.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from naust.agent.presence import PresenceTracker, PresenceTransition
from naust.games.facts import Fact, Observer, Resolver


@dataclass(frozen=True, slots=True)
class ReplayEvent:
    """One line that the observer recognised, what it resolved to, and what changed."""

    line_number: int
    observation: object
    facts: tuple[Fact, ...]
    transitions: tuple[PresenceTransition, ...]


def replay(
    lines: Iterable[str],
    observer: Observer,
    resolver: Resolver,
    tracker: PresenceTracker,
) -> Iterator[ReplayEvent]:
    for line_number, line in enumerate(lines, start=1):
        observation = observer.parse_line(line)
        if observation is None:
            continue
        facts = resolver.resolve(observation)
        transitions = tuple(t for t in map(tracker.observe, facts) if t is not None)
        yield ReplayEvent(line_number, observation, facts, transitions)
