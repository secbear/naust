"""Agent entry points. The runtime lives in :mod:`naust.agent.runtime`."""

from naust.agent.config import AgentConfig
from naust.agent.runtime import EXIT_FAILED, EXIT_OK, WorldRuntime, run_world

__all__ = ["EXIT_FAILED", "EXIT_OK", "WorldRuntime", "run_agent", "run_world"]


def run_agent(config: AgentConfig) -> None:
    """Project 0 stub retained for ``naust agent`` without ``--world``."""
