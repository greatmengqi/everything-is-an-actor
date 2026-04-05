"""Tests for MoASystem high-level entry point."""

import pytest

from everything_is_an_actor.agents import AgentActor
from everything_is_an_actor.flow.quorum import QuorumResult
from everything_is_an_actor.moa.system import MoASystem
from everything_is_an_actor.moa.patterns import moa_layer, moa_tree

pytestmark = pytest.mark.anyio


class EchoProposer(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return f"[Echo] {input}"


class JoinAggregator(AgentActor[QuorumResult[str], str]):
    async def execute(self, input: QuorumResult[str]) -> str:
        return " | ".join(input.succeeded)


class TestMoASystem:
    async def test_run(self):
        system = MoASystem()
        try:
            result = await system.run(
                moa_tree([
                    moa_layer(
                        proposers=[EchoProposer],
                        aggregator=JoinAggregator,
                    ),
                ]),
                "hello",
            )
            assert "[Echo]" in result
        finally:
            await system.shutdown()

    async def test_run_stream(self):
        system = MoASystem()
        try:
            events = []
            async for event in system.run_stream(
                moa_tree([
                    moa_layer(
                        proposers=[EchoProposer],
                        aggregator=JoinAggregator,
                    ),
                ]),
                "hello",
            ):
                events.append(event)
            types = [e.type for e in events]
            assert "task_started" in types
            assert "task_completed" in types
        finally:
            await system.shutdown()
