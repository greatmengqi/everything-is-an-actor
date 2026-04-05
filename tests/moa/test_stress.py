"""Stress and edge-case tests for MOA — designed to expose underlying framework bugs."""

import asyncio
import pytest
from typing import Any

from everything_is_an_actor.agents import AgentActor, AgentSystem, Task, TaskResult, TaskStatus


# --- Test agents ---

class EchoAgent(AgentActor[Any, str]):
    async def execute(self, input: Any) -> str:
        return f"[Echo] {input}"


class SlowAgent(AgentActor[Any, str]):
    async def execute(self, input: Any) -> str:
        await asyncio.sleep(0.05)
        return f"[Slow] {input}"


class FastAgent(AgentActor[Any, str]):
    async def execute(self, input: Any) -> str:
        return f"[Fast] {input}"


class FailingAgent(AgentActor[Any, str]):
    async def execute(self, input: Any) -> str:
        raise ValueError("boom")


class FlakeyAgent(AgentActor[Any, str]):
    """Fails on first call, succeeds on second (per instance)."""
    _call_count = 0

    async def execute(self, input: Any) -> str:
        FlakeyAgent._call_count += 1
        if FlakeyAgent._call_count % 2 == 1:
            raise RuntimeError("flakey failure")
        return f"[Flakey] {input}"


class JoinAgg(AgentActor[list, str]):
    async def execute(self, input: list) -> str:
        outputs = [r.get_or_raise() for r in input if r.is_success()]
        return " | ".join(outputs)


class CountAgg(AgentActor[list, str]):
    """Reports count of successes and failures."""
    async def execute(self, input: list) -> str:
        s = sum(1 for r in input if r.is_success())
        f = sum(1 for r in input if r.is_failure())
        outputs = [r.get_or_raise() for r in input if r.is_success()]
        return f"s={s},f={f}:{','.join(outputs)}"


class ChildSpawningProposer(AgentActor[Any, str]):
    """Proposer that internally spawns a child actor via context.ask."""
    async def execute(self, input: Any) -> str:
        child_result = await self.context.ask(EchoAgent, Task(input=f"child({input})"))
        return f"[Parent:{child_result.get_or_raise()}]"


class StreamingProposer(AgentActor[Any, list]):
    """Proposer that yields chunks via async generator."""
    async def execute(self, input: Any):
        for i in range(3):
            yield f"chunk-{i}-{input}"


class StreamJoinAgg(AgentActor[list, str]):
    """Aggregator that handles TaskResults where output might be a list (from streaming)."""
    async def execute(self, input: list) -> str:
        parts = []
        for r in input:
            if r.is_success():
                o = r.get_or_raise()
                if isinstance(o, list):
                    parts.append("|".join(str(x) for x in o))
                else:
                    parts.append(str(o))
        return " + ".join(parts)


class FailingAgg(AgentActor[list, str]):
    """Aggregator that always fails."""
    async def execute(self, input: list) -> str:
        raise RuntimeError("aggregator exploded")


# --- Tests ---

@pytest.mark.asyncio
class TestStress:

    async def test_deep_nesting_3_levels(self):
        """MoATree nested 3 levels deep."""
        from everything_is_an_actor.moa.builder import MoABuilder
        from everything_is_an_actor.moa.config import MoANode, MoATree

        innermost = MoATree(nodes=[
            MoANode(proposers=[EchoAgent], aggregator=JoinAgg),
        ])
        middle = MoATree(nodes=[
            MoANode(proposers=[innermost, EchoAgent], aggregator=JoinAgg),
        ])
        outer = MoATree(nodes=[
            MoANode(proposers=[middle, EchoAgent], aggregator=JoinAgg),
        ])
        MoAAgent = MoABuilder().build(outer)

        system = AgentSystem("test")
        try:
            events = []
            async for event in system.run(MoAAgent, "deep"):
                events.append(event)
            completed = [e for e in events if e.type == "task_completed"]
            assert len(completed) >= 1
            result = completed[-1].data
            assert "[Echo]" in result
        finally:
            await system.shutdown()

    async def test_large_fan_out_10_proposers(self):
        """10 proposers in parallel — tests concurrent actor spawning."""
        from everything_is_an_actor.moa.builder import MoABuilder
        from everything_is_an_actor.moa.config import MoANode, MoATree

        tree = MoATree(nodes=[
            MoANode(proposers=[EchoAgent] * 10, aggregator=CountAgg),
        ])
        MoAAgent = MoABuilder().build(tree)

        system = AgentSystem("test")
        try:
            events = []
            async for event in system.run(MoAAgent, "fan"):
                events.append(event)
            completed = [e for e in events if e.type == "task_completed"]
            result = completed[-1].data
            assert result.startswith("s=10,f=0:")
        finally:
            await system.shutdown()

    async def test_slow_and_fast_proposers_mixed(self):
        """Slow + fast proposers — tests that all results are collected, not just fast ones."""
        from everything_is_an_actor.moa.builder import MoABuilder
        from everything_is_an_actor.moa.config import MoANode, MoATree

        tree = MoATree(nodes=[
            MoANode(proposers=[SlowAgent, FastAgent, FastAgent], aggregator=CountAgg),
        ])
        MoAAgent = MoABuilder().build(tree)

        system = AgentSystem("test")
        try:
            events = []
            async for event in system.run(MoAAgent, "mix"):
                events.append(event)
            completed = [e for e in events if e.type == "task_completed"]
            result = completed[-1].data
            # All 3 should succeed, including the slow one
            assert result.startswith("s=3,f=0:")
            assert "[Slow]" in result
            assert "[Fast]" in result
        finally:
            await system.shutdown()

    async def test_concurrent_moa_runs(self):
        """Multiple MOA agents running simultaneously on same system."""
        from everything_is_an_actor.moa.builder import MoABuilder
        from everything_is_an_actor.moa.config import MoANode, MoATree

        tree = MoATree(nodes=[
            MoANode(proposers=[EchoAgent, EchoAgent], aggregator=JoinAgg),
        ])
        MoAAgent = MoABuilder().build(tree)

        system = AgentSystem("test")
        try:
            async def run_one(query: str) -> str:
                events = []
                async for event in system.run(MoAAgent, query):
                    events.append(event)
                completed = [e for e in events if e.type == "task_completed"]
                return completed[-1].data

            results = await asyncio.gather(
                run_one("alpha"),
                run_one("beta"),
                run_one("gamma"),
            )
            assert "alpha" in results[0]
            assert "beta" in results[1]
            assert "gamma" in results[2]
        finally:
            await system.shutdown()

    async def test_proposer_that_spawns_children(self):
        """Proposer internally uses context.ask to spawn child actors."""
        from everything_is_an_actor.moa.builder import MoABuilder
        from everything_is_an_actor.moa.config import MoANode, MoATree

        tree = MoATree(nodes=[
            MoANode(proposers=[ChildSpawningProposer, EchoAgent], aggregator=JoinAgg),
        ])
        MoAAgent = MoABuilder().build(tree)

        system = AgentSystem("test")
        try:
            events = []
            async for event in system.run(MoAAgent, "spawn"):
                events.append(event)
            completed = [e for e in events if e.type == "task_completed"]
            result = completed[-1].data
            assert "[Parent:" in result
            assert "child(spawn)" in result
        finally:
            await system.shutdown()

    async def test_streaming_proposer(self):
        """Proposer that yields chunks — tests async generator integration."""
        from everything_is_an_actor.moa.builder import MoABuilder
        from everything_is_an_actor.moa.config import MoANode, MoATree

        tree = MoATree(nodes=[
            MoANode(proposers=[StreamingProposer, EchoAgent], aggregator=StreamJoinAgg),
        ])
        MoAAgent = MoABuilder().build(tree)

        system = AgentSystem("test")
        try:
            events = []
            async for event in system.run(MoAAgent, "stream"):
                events.append(event)
            completed = [e for e in events if e.type == "task_completed"]
            assert len(completed) >= 1
            result = completed[-1].data
            assert "chunk-0" in result
            assert "chunk-2" in result
        finally:
            await system.shutdown()

    async def test_aggregator_failure_propagates(self):
        """When aggregator fails, the error should propagate correctly."""
        from everything_is_an_actor.moa.builder import MoABuilder
        from everything_is_an_actor.moa.config import MoANode, MoATree

        tree = MoATree(nodes=[
            MoANode(proposers=[EchoAgent], aggregator=FailingAgg),
        ])
        MoAAgent = MoABuilder().build(tree)

        system = AgentSystem("test")
        try:
            with pytest.raises(RuntimeError, match="aggregator exploded"):
                async for event in system.run(MoAAgent, "fail"):
                    pass
        finally:
            await system.shutdown()

    async def test_none_input(self):
        """None as input — should pass through without crashing."""
        from everything_is_an_actor.moa.builder import MoABuilder
        from everything_is_an_actor.moa.config import MoANode, MoATree

        tree = MoATree(nodes=[
            MoANode(proposers=[EchoAgent], aggregator=JoinAgg),
        ])
        MoAAgent = MoABuilder().build(tree)

        system = AgentSystem("test")
        try:
            events = []
            async for event in system.run(MoAAgent, None):
                events.append(event)
            completed = [e for e in events if e.type == "task_completed"]
            assert len(completed) >= 1
            assert "None" in completed[-1].data
        finally:
            await system.shutdown()

    async def test_many_layers_pipeline(self):
        """5 layers chained — tests accumulation and no resource leaks."""
        from everything_is_an_actor.moa.builder import MoABuilder
        from everything_is_an_actor.moa.config import MoANode, MoATree

        tree = MoATree.repeated(
            MoANode(proposers=[EchoAgent], aggregator=JoinAgg),
            num_layers=5,
        )
        MoAAgent = MoABuilder().build(tree)

        system = AgentSystem("test")
        try:
            events = []
            async for event in system.run(MoAAgent, "x"):
                events.append(event)
            completed = [e for e in events if e.type == "task_completed"]
            result = completed[-1].data
            # 5 layers of [Echo] wrapping
            assert result.count("[Echo]") == 5
        finally:
            await system.shutdown()

    async def test_partial_failures_across_multiple_layers(self):
        """Layer 1 has a failure, Layer 2 also has a failure — both tolerated."""
        from everything_is_an_actor.moa.builder import MoABuilder
        from everything_is_an_actor.moa.config import MoANode, MoATree

        tree = MoATree(nodes=[
            MoANode(
                proposers=[EchoAgent, FailingAgent, EchoAgent],
                aggregator=CountAgg,
                min_success=1,
            ),
            MoANode(
                proposers=[EchoAgent, FailingAgent],
                aggregator=CountAgg,
                min_success=1,
            ),
        ])
        MoAAgent = MoABuilder().build(tree)

        system = AgentSystem("test")
        try:
            events = []
            async for event in system.run(MoAAgent, "cascade"):
                events.append(event)
            completed = [e for e in events if e.type == "task_completed"]
            result = completed[-1].data
            # Layer 2 should report 1 success and 1 failure
            assert "s=1,f=1:" in result
        finally:
            await system.shutdown()

    async def test_large_fan_out_with_failures(self):
        """8 proposers, 3 fail, min_success=5 — exactly at threshold."""
        from everything_is_an_actor.moa.builder import MoABuilder
        from everything_is_an_actor.moa.config import MoANode, MoATree

        tree = MoATree(nodes=[
            MoANode(
                proposers=[EchoAgent] * 5 + [FailingAgent] * 3,
                aggregator=CountAgg,
                min_success=5,
            ),
        ])
        MoAAgent = MoABuilder().build(tree)

        system = AgentSystem("test")
        try:
            events = []
            async for event in system.run(MoAAgent, "threshold"):
                events.append(event)
            completed = [e for e in events if e.type == "task_completed"]
            result = completed[-1].data
            assert result.startswith("s=5,f=3:")
        finally:
            await system.shutdown()
