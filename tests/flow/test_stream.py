"""Tests for streaming interpreter — interpret_stream yields TaskEvents."""

import pytest

from everything_is_an_actor.core.system import ActorSystem
from everything_is_an_actor.agents import AgentActor, AgentSystem
from everything_is_an_actor.flow import Interpreter, agent

pytestmark = pytest.mark.anyio


class StreamEcho(AgentActor[str, str]):
    """Agent that emits progress before returning."""

    async def execute(self, input: str) -> str:
        await self.emit_progress(f"processing: {input}")
        return f"done: {input}"


class SimpleEcho(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return input


class TestInterpretStream:
    async def test_agent_yields_events(self):
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            events = []
            async for event in interp.run_stream(agent(StreamEcho), "hello"):
                events.append(event)

            types = [e.type for e in events]
            assert "task_started" in types
            assert "task_progress" in types
            assert "task_completed" in types
        finally:
            await system.shutdown()

    async def test_flat_map_streams_second_agent(self):
        """FlatMap interprets first non-streaming, streams second."""
        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            flow = agent(StreamEcho).flat_map(agent(StreamEcho))
            events = []
            async for event in interp.run_stream(flow, "test"):
                events.append(event)

            # Events from the second agent in the chain
            types = [e.type for e in events]
            assert "task_started" in types
            assert "task_completed" in types
        finally:
            await system.shutdown()

    async def test_pure_yields_no_events(self):
        from everything_is_an_actor.flow import pure

        system = AgentSystem(ActorSystem())
        interp = Interpreter(system)
        try:
            events = []
            async for event in interp.run_stream(pure(str.upper), "hello"):
                events.append(event)
            assert events == []
        finally:
            await system.shutdown()
