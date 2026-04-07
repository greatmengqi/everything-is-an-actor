"""End-to-end: full pipeline with multiple primitives."""

import pytest
from everything_is_an_actor.core.system import ActorSystem
from everything_is_an_actor.agents import AgentActor, AgentSystem
from everything_is_an_actor.flow import Continue, Done, Interpreter, agent, loop, pure, to_dict, to_mermaid

pytestmark = pytest.mark.anyio


class Researcher(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return f"research:{input}"


class Analyst(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return f"analysis:{input}"


class Writer(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return f"written:{input}"


_critic_calls = 0


class Critic(AgentActor[str, Continue[str] | Done[str]]):
    async def execute(self, input: str) -> Continue[str] | Done[str]:
        global _critic_calls
        _critic_calls += 1
        if _critic_calls >= 2:
            return Done(value=f"approved:{input}")
        return Continue(value=f"revise:{input}")


class Fallback(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return f"fallback:{input}"


class TestE2E:
    async def test_full_pipeline(self):
        global _critic_calls
        _critic_calls = 0

        pipeline = (
            agent(Researcher)
            .zip(agent(Analyst))
            .map(lambda pair: f"{pair[0]} | {pair[1]}")
            .flat_map(
                loop(agent(Writer).flat_map(agent(Critic)), max_iter=5)
            )
            .recover_with(agent(Fallback))
        )

        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            result = await interp.run(pipeline, ("query", "query"))
            assert "approved:" in result
            assert "written:" in result
        finally:
            await system.shutdown()

    def test_visualize(self):
        pipeline = agent(Researcher).zip(agent(Analyst)).map(lambda p: p).flat_map(agent(Writer))
        mermaid = to_mermaid(pipeline)
        assert "graph LR" in mermaid
        for name in ("Researcher", "Analyst", "Writer"):
            assert name in mermaid

    def test_serialize_agent_flow(self):
        flow = agent(Researcher).flat_map(agent(Writer))
        d = to_dict(flow)
        assert d["type"] == "FlatMap"
