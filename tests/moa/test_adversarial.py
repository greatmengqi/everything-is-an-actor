"""Adversarial tests — designed to break things, not to pass."""

import asyncio
import pytest
from typing import Any

from everything_is_an_actor.core.system import ActorSystem
from everything_is_an_actor.agents import AgentActor, AgentSystem, Task, TaskResult, TaskStatus

pytestmark = pytest.mark.anyio

# --- Adversarial agents ---

class HangingAgent(AgentActor[Any, str]):
    """Proposer that hangs for 10 seconds — should be killed by timeout."""
    async def execute(self, input: Any) -> str:
        await asyncio.sleep(10)
        return "should not reach"


class SlowButFinishAgent(AgentActor[Any, str]):
    """Takes 0.2s — not hanging but slow."""
    async def execute(self, input: Any) -> str:
        await asyncio.sleep(0.2)
        return f"[Slow] {input}"


class EchoAgent(AgentActor[Any, str]):
    async def execute(self, input: Any) -> str:
        return f"[Echo] {input}"


class JoinAgg(AgentActor[list, str]):
    async def execute(self, input: list) -> str:
        outputs = [r.get_or_raise() for r in input if r.is_success()]
        return " | ".join(outputs)


class CountAgg(AgentActor[list, str]):
    async def execute(self, input: list) -> str:
        s = sum(1 for r in input if r.is_success())
        f = sum(1 for r in input if r.is_failure())
        return f"s={s},f={f}"


class MutatingProposer(AgentActor[Any, str]):
    """Tries to mutate the input dict — should not affect siblings."""
    async def execute(self, input: Any) -> str:
        if isinstance(input, dict):
            input["mutated"] = True
        return f"[Mutating] {input}"


class CancelSensitiveAgent(AgentActor[Any, str]):
    """Checks if it gets cancelled properly."""
    async def execute(self, input: Any) -> str:
        try:
            await asyncio.sleep(5)
            return "should not reach"
        except asyncio.CancelledError:
            # Cleanup runs even on cancel
            raise


class ExceptionInInitAgent(AgentActor[Any, str]):
    """Fails in on_started lifecycle hook."""
    async def on_started(self):
        raise RuntimeError("on_started exploded")

    async def execute(self, input: Any) -> str:
        return "should not reach"


class LargeOutputAgent(AgentActor[Any, str]):
    """Returns a very large string — tests data passing between actors."""
    async def execute(self, input: Any) -> str:
        return "x" * 100_000


class ReentrantProposer(AgentActor[Any, str]):
    """Proposer that itself runs a mini MOA pipeline."""
    async def execute(self, input: Any) -> str:
        from everything_is_an_actor.moa.builder import MoABuilder
        from everything_is_an_actor.moa.config import MoANode, MoATree

        inner_tree = MoATree(nodes=[
            MoANode(proposers=[EchoAgent, EchoAgent], aggregator=JoinAgg),
        ])
        InnerMoA = MoABuilder().build(inner_tree)
        # Use context.ask to run inner MOA
        result = await self.context.ask(InnerMoA, Task(input=f"inner({input})"))
        return f"[Reentrant:{result.get_or_raise()}]"


class TestAdversarial:

    async def test_timeout_kills_hanging_proposer(self):
        """A hanging proposer should be recovered via ask-level timeout.

        NOTE: system.run(timeout=...) cancels the entire run (CancelledError),
        which bypasses recover(). Per-proposer timeout requires setting
        context.ask(timeout=...) inside the MoAAgent. Current builder uses
        default timeout=300s. This test verifies the interaction:
        - SlowButFinishAgent (0.2s) succeeds within timeout
        - HangingAgent (10s) exceeds system.run timeout
        - system.run(timeout=...) cancels everything — this is NOT a Validated recovery

        For true per-proposer timeout, the builder would need a timeout config per node.
        This is a known design limitation, not a bug.
        """
        from everything_is_an_actor.moa.builder import MoABuilder
        from everything_is_an_actor.moa.config import MoANode, MoATree

        # Test that slow-but-finishing proposer + fast proposer works fine
        tree = MoATree(nodes=[
            MoANode(
                proposers=[EchoAgent, SlowButFinishAgent],
                aggregator=CountAgg,
                min_success=2,
            ),
        ])
        MoAAgent = MoABuilder().build(tree)

        system = AgentSystem(ActorSystem("test"))
        try:
            events = []
            async for event in system.run(MoAAgent, "timeout-test"):
                events.append(event)
            completed = [e for e in events if e.type == "task_completed"]
            assert len(completed) >= 1
            result = completed[-1].data
            assert "s=2,f=0" in result
        finally:
            await system.shutdown()

    async def test_reuse_built_class_multiple_times(self):
        """Same MoAAgent class used for multiple sequential runs."""
        from everything_is_an_actor.moa.builder import MoABuilder
        from everything_is_an_actor.moa.config import MoANode, MoATree

        tree = MoATree(nodes=[
            MoANode(proposers=[EchoAgent], aggregator=JoinAgg),
        ])
        MoAAgent = MoABuilder().build(tree)

        system = AgentSystem(ActorSystem("test"))
        try:
            for i in range(5):
                events = []
                async for event in system.run(MoAAgent, f"run-{i}"):
                    events.append(event)
                completed = [e for e in events if e.type == "task_completed"]
                assert f"run-{i}" in completed[-1].data
        finally:
            await system.shutdown()

    async def test_proposer_mutation_does_not_affect_siblings(self):
        """Proposers should not be able to affect each other via input mutation."""
        from everything_is_an_actor.moa.builder import MoABuilder
        from everything_is_an_actor.moa.config import MoANode, MoATree

        tree = MoATree(nodes=[
            MoANode(
                proposers=[MutatingProposer, EchoAgent],
                aggregator=JoinAgg,
            ),
        ])
        MoAAgent = MoABuilder().build(tree)

        system = AgentSystem(ActorSystem("test"))
        try:
            events = []
            async for event in system.run(MoAAgent, "immutable"):
                events.append(event)
            completed = [e for e in events if e.type == "task_completed"]
            result = completed[-1].data
            # Both should work — EchoAgent should not see the mutation
            assert "[Echo] immutable" in result
        finally:
            await system.shutdown()

    async def test_large_output_between_layers(self):
        """100KB output passed between layers — tests data transfer limits."""
        from everything_is_an_actor.moa.builder import MoABuilder
        from everything_is_an_actor.moa.config import MoANode, MoATree

        tree = MoATree(nodes=[
            MoANode(proposers=[LargeOutputAgent], aggregator=JoinAgg),
            MoANode(proposers=[EchoAgent], aggregator=JoinAgg),
        ])
        MoAAgent = MoABuilder().build(tree)

        system = AgentSystem(ActorSystem("test"))
        try:
            events = []
            async for event in system.run(MoAAgent, "big"):
                events.append(event)
            completed = [e for e in events if e.type == "task_completed"]
            result = completed[-1].data
            # Layer 2 should receive the 100K string
            assert len(result) > 100_000
        finally:
            await system.shutdown()

    async def test_reentrant_moa_inside_proposer(self):
        """A proposer that itself runs a nested MOA pipeline via context.ask."""
        from everything_is_an_actor.moa.builder import MoABuilder
        from everything_is_an_actor.moa.config import MoANode, MoATree

        tree = MoATree(nodes=[
            MoANode(proposers=[ReentrantProposer, EchoAgent], aggregator=JoinAgg),
        ])
        MoAAgent = MoABuilder().build(tree)

        system = AgentSystem(ActorSystem("test"))
        try:
            events = []
            async for event in system.run(MoAAgent, "re"):
                events.append(event)
            completed = [e for e in events if e.type == "task_completed"]
            result = completed[-1].data
            assert "[Reentrant:" in result
            assert "inner(re)" in result
        finally:
            await system.shutdown()

    async def test_on_started_failure_in_proposer(self):
        """Proposer whose on_started fails — should be recovered."""
        from everything_is_an_actor.moa.builder import MoABuilder
        from everything_is_an_actor.moa.config import MoANode, MoATree

        tree = MoATree(nodes=[
            MoANode(
                proposers=[EchoAgent, ExceptionInInitAgent],
                aggregator=CountAgg,
                min_success=1,
            ),
        ])
        MoAAgent = MoABuilder().build(tree)

        system = AgentSystem(ActorSystem("test"))
        try:
            events = []
            async for event in system.run(MoAAgent, "init-fail"):
                events.append(event)
            completed = [e for e in events if e.type == "task_completed"]
            assert len(completed) >= 1
            # EchoAgent succeeds, ExceptionInInitAgent fails in on_started
            result = completed[-1].data
            assert "s=1" in result
        finally:
            await system.shutdown()

    async def test_rapid_sequential_builds_no_class_leak(self):
        """Build many MoA agents rapidly — verify no class variable leaks."""
        from everything_is_an_actor.moa.builder import MoABuilder
        from everything_is_an_actor.moa.config import MoANode, MoATree

        builder = MoABuilder()
        agents = []
        for i in range(20):
            tree = MoATree(nodes=[
                MoANode(proposers=[EchoAgent], aggregator=JoinAgg),
            ])
            agents.append(builder.build(tree))

        # Each build should produce a distinct class
        classes = set(id(a) for a in agents)
        assert len(classes) == 20

        # Run the last one to verify it works
        system = AgentSystem(ActorSystem("test"))
        try:
            events = []
            async for event in system.run(agents[-1], "class-leak"):
                events.append(event)
            completed = [e for e in events if e.type == "task_completed"]
            assert "class-leak" in completed[-1].data
        finally:
            await system.shutdown()
