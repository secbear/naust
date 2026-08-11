from naust.agent.config import AgentConfig
from naust.agent.service import Agent


def test_agent_creates_runtime_state_internally() -> None:
    agent = Agent(AgentConfig())

    assert agent.runtime.players == set()
    assert agent.runtime.ready is False
    assert list(agent.runtime.log_buffer) == []
