"""Project 0 Agent runtime boundary; backend supervision arrives in Project 2."""

from collections import deque
from dataclasses import dataclass, field

from naust.agent.config import AgentConfig


@dataclass(slots=True)
class AgentRuntime:
    """Ephemeral observations that must never enter external configuration."""

    players: set[str] = field(default_factory=set)
    ready: bool = False
    log_buffer: deque[str] = field(default_factory=lambda: deque(maxlen=1_000))


class Agent:
    """Behavioral component composed from static config and fresh runtime state."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.runtime = AgentRuntime()

    def run(self) -> None:
        """Exit cleanly until Project 2 adds backend supervision."""


def run_agent(config: AgentConfig) -> None:
    Agent(config).run()
