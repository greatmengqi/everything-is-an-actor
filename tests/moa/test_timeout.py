"""Timeout tests — verify per-proposer timeout via context.ask + recover at framework level."""

import asyncio
import pytest
from typing import Any

from everything_is_an_actor.agents import AgentActor, AgentSystem, Task, TaskResult, TaskStatus

pytestmark = pytest.mark.anyio

class HangingAgent(AgentActor[Any, str]):
    async def execute(self, input: Any) -> str:
        await asyncio.sleep(30)
        return "should not reach"


class EchoAgent(AgentActor[Any, str]):
    async def execute(self, input: Any) -> str:
        return f"[Echo] {input}"


class CountAgg(AgentActor[list, str]):
    async def execute(self, input: list) -> str:
        s = sum(1 for r in input if r.is_success())
        f = sum(1 for r in input if r.is_failure())
        return f"s={s},f={f}"


class TestProposerTimeout:

    async def test_hanging_proposer_times_out_and_recovers(self):
        """HangingAgent times out at proposer level, recover catches it, pipeline continues."""
        from everything_is_an_actor.moa.builder import MoABuilder
        from everything_is_an_actor.moa.config import MoANode, MoATree

        tree = MoATree(nodes=[
            MoANode(
                proposers=[EchoAgent, HangingAgent],
                aggregator=CountAgg,
                min_success=1,
                proposer_timeout=1.0,  # HangingAgent will timeout after 1s
            ),
        ])
        MoAAgent = MoABuilder().build(tree)

        system = AgentSystem("test")
        try:
            events = []
            async with asyncio.timeout(5):  # safety net — should finish in ~1s
                async for event in system.run(MoAAgent, "timeout"):
                    events.append(event)
            completed = [e for e in events if e.type == "task_completed"]
            assert len(completed) >= 1
            result = completed[-1].data
            # EchoAgent succeeded, HangingAgent timed out and was recovered
            assert "s=1,f=1" in result
        finally:
            await system.shutdown()

    async def test_all_proposers_timeout_min_success_not_met(self):
        """All proposers hang, all timeout, min_success not met → RuntimeError."""
        from everything_is_an_actor.moa.builder import MoABuilder
        from everything_is_an_actor.moa.config import MoANode, MoATree

        tree = MoATree(nodes=[
            MoANode(
                proposers=[HangingAgent, HangingAgent],
                aggregator=CountAgg,
                min_success=1,
                proposer_timeout=0.5,
            ),
        ])
        MoAAgent = MoABuilder().build(tree)

        system = AgentSystem("test")
        try:
            async with asyncio.timeout(5):
                with pytest.raises(RuntimeError, match="MOA:"):
                    async for event in system.run(MoAAgent, "all-timeout"):
                        pass
        finally:
            await system.shutdown()

    async def test_default_timeout_is_reasonable(self):
        """Default proposer_timeout=30s, normal agents finish well within it."""
        from everything_is_an_actor.moa.config import MoANode

        node = MoANode(proposers=[EchoAgent], aggregator=CountAgg)
        assert node.proposer_timeout == 30.0
