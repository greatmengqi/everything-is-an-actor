"""Tests for streaming interpreter — interpret_stream yields TaskEvents."""

import pytest

from everything_is_an_actor.agents import AgentActor, AgentSystem
from everything_is_an_actor.flow import agent, interpret_stream

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
        system = AgentSystem()
        try:
            events = []
            async for event in interpret_stream(agent(StreamEcho), "hello", system):
                events.append(event)

            types = [e.type for e in events]
            assert "task_started" in types
            assert "task_progress" in types
            assert "task_completed" in types
        finally:
            await system.shutdown()

    async def test_flat_map_yields_events_from_both(self):
        system = AgentSystem()
        try:
            flow = agent(StreamEcho).flat_map(agent(StreamEcho))
            events = []
            async for event in interpret_stream(flow, "test", system):
                events.append(event)

            # Should have events from both agents
            completed = [e for e in events if e.type == "task_completed"]
            assert len(completed) >= 2
        finally:
            await system.shutdown()

    async def test_pure_yields_no_events(self):
        from everything_is_an_actor.flow import pure

        system = AgentSystem()
        try:
            events = []
            async for event in interpret_stream(pure(str.upper), "hello", system):
                events.append(event)
            assert events == []
        finally:
            await system.shutdown()
