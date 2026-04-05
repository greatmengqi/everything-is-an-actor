"""Tests for AgentSystem.run_flow and run_flow_stream."""

import pytest

from everything_is_an_actor.core.system import ActorSystem
from everything_is_an_actor.agents import AgentActor, AgentSystem
from everything_is_an_actor.flow import agent, pure

pytestmark = pytest.mark.anyio


class Echo(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return input


class Upper(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return input.upper()


class TestRunFlow:
    async def test_simple_agent(self):
        system = AgentSystem(ActorSystem())
        try:
            result = await system.run_flow(agent(Echo), "hello")
            assert result == "hello"
        finally:
            await system.shutdown()

    async def test_composed_flow(self):
        system = AgentSystem(ActorSystem())
        try:
            flow = agent(Echo).flat_map(agent(Upper))
            result = await system.run_flow(flow, "hello")
            assert result == "HELLO"
        finally:
            await system.shutdown()

    async def test_pure_flow(self):
        system = AgentSystem(ActorSystem())
        try:
            result = await system.run_flow(pure(str.upper), "hello")
            assert result == "HELLO"
        finally:
            await system.shutdown()


class TestRunFlowStream:
    async def test_stream_emits_events(self):
        system = AgentSystem(ActorSystem())
        try:
            events = []
            async for event in system.run_flow_stream(agent(Echo), "hello"):
                events.append(event)
            types = [e.type for e in events]
            assert "task_started" in types
            assert "task_completed" in types
        finally:
            await system.shutdown()
