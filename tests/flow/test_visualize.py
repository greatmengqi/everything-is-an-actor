"""Tests for Flow Mermaid visualization."""

from everything_is_an_actor.agents import AgentActor
from everything_is_an_actor.flow import agent, race
from everything_is_an_actor.flow.visualize import to_mermaid


class A(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return input


class B(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return input


class C(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return input


class TestVisualize:
    def test_agent_node(self):
        result = to_mermaid(agent(A))
        assert "graph LR" in result
        assert "A" in result

    def test_flat_map_chain(self):
        result = to_mermaid(agent(A).flat_map(agent(B)).flat_map(agent(C)))
        assert "-->" in result
        for name in ("A", "B", "C"):
            assert name in result

    def test_zip_parallel(self):
        result = to_mermaid(agent(A).zip(agent(B)))
        assert "parallel" in result
        assert "join" in result

    def test_race(self):
        result = to_mermaid(race(agent(A), agent(B)))
        assert "race" in result
        assert "A" in result
        assert "B" in result
