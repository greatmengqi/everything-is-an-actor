"""Tests for OrchestratorActor (M2)."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from actor_for_agents import ActorSystem
from actor_for_agents.agents import AgentActor, Task, TaskResult
from actor_for_agents.agents.orchestrator import OrchestratorActor

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class EchoAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return input


class UpperAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return input.upper()


class BrokenAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        raise ValueError(f"boom: {input}")


class SlowAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        await asyncio.sleep(10)
        return input


class DelayedAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        await asyncio.sleep(0.1)
        return input


# ---------------------------------------------------------------------------
# dispatch — single child
# ---------------------------------------------------------------------------


async def test_dispatch_returns_output():
    class SimpleOrchestrator(OrchestratorActor[str, str]):
        async def execute(self, input: str) -> str:
            return await self.dispatch(EchoAgent, input)

    system = ActorSystem("t")
    ref = await system.spawn(SimpleOrchestrator, "orch")
    result = await ref.ask(Task(input="hello"))
    assert result.output == "hello"
    await system.shutdown()


async def test_dispatch_exception_propagates():
    class BrokenOrchestrator(OrchestratorActor[str, str]):
        async def execute(self, input: str) -> str:
            return await self.dispatch(BrokenAgent, input)

    system = ActorSystem("t")
    ref = await system.spawn(BrokenOrchestrator, "orch")
    with pytest.raises(Exception, match="boom: bad"):
        await ref.ask(Task(input="bad"))
    await system.shutdown()


async def test_dispatch_multiple_sequential_calls():
    """Same orchestrator can dispatch same class multiple times (unique names)."""

    class MultiOrchestrator(OrchestratorActor[list, list]):
        async def execute(self, input: list) -> list:
            results = []
            for item in input:
                results.append(await self.dispatch(EchoAgent, item))
            return results

    system = ActorSystem("t")
    ref = await system.spawn(MultiOrchestrator, "orch")
    result = await ref.ask(Task(input=["a", "b", "c"]))
    assert result.output == ["a", "b", "c"]
    await system.shutdown()


async def test_dispatch_child_stopped_after_completion():
    """Dispatched child is stopped after task completes (no child leaks)."""

    class EphemeralOrchestrator(OrchestratorActor[str, str]):
        async def execute(self, input: str) -> str:
            result = await self.dispatch(EchoAgent, input)
            await asyncio.sleep(0.05)  # let stop propagate
            assert len(self.context.children) == 0, "child should be stopped and removed"
            return result

    system = ActorSystem("t")
    ref = await system.spawn(EphemeralOrchestrator, "orch")
    result = await ref.ask(Task(input="x"))
    assert result.output == "x"
    await system.shutdown()


async def test_dispatch_child_stopped_on_exception():
    """Dispatched child is stopped even when execute() raises."""

    class CleanupOrchestrator(OrchestratorActor[str, str]):
        async def execute(self, input: str) -> str:
            try:
                await self.dispatch(BrokenAgent, input)
            except Exception:
                pass
            await asyncio.sleep(0.05)
            assert len(self.context.children) == 0
            return "recovered"

    system = ActorSystem("t")
    ref = await system.spawn(CleanupOrchestrator, "orch")
    result = await ref.ask(Task(input="x"))
    assert result.output == "recovered"
    await system.shutdown()


async def test_dispatch_timeout_raises():
    class SlowOrchestrator(OrchestratorActor[str, str]):
        async def execute(self, input: str) -> str:
            return await self.dispatch(SlowAgent, input, timeout=0.1)

    system = ActorSystem("t")
    ref = await system.spawn(SlowOrchestrator, "orch")
    with pytest.raises(asyncio.TimeoutError):
        await ref.ask(Task(input="x"), timeout=5.0)
    await system.shutdown()


# ---------------------------------------------------------------------------
# dispatch_parallel — concurrent children
# ---------------------------------------------------------------------------


async def test_dispatch_parallel_returns_ordered_results():
    class ParallelOrchestrator(OrchestratorActor[list, list]):
        async def execute(self, input: list) -> list:
            return await self.dispatch_parallel([(EchoAgent, v) for v in input])

    system = ActorSystem("t")
    ref = await system.spawn(ParallelOrchestrator, "orch")
    result = await ref.ask(Task(input=["x", "y", "z"]))
    assert result.output == ["x", "y", "z"]
    await system.shutdown()


async def test_dispatch_parallel_different_agent_types():
    class MixedOrchestrator(OrchestratorActor[str, list]):
        async def execute(self, input: str) -> list:
            return await self.dispatch_parallel([
                (EchoAgent, input),
                (UpperAgent, input),
            ])

    system = ActorSystem("t")
    ref = await system.spawn(MixedOrchestrator, "orch")
    result = await ref.ask(Task(input="hello"))
    assert result.output == ["hello", "HELLO"]
    await system.shutdown()


async def test_dispatch_parallel_empty_list():
    class EmptyOrchestrator(OrchestratorActor[Any, list]):
        async def execute(self, input: Any) -> list:
            return await self.dispatch_parallel([])

    system = ActorSystem("t")
    ref = await system.spawn(EmptyOrchestrator, "orch")
    result = await ref.ask(Task(input=None))
    assert result.output == []
    await system.shutdown()


async def test_dispatch_parallel_exception_propagates():
    class FailingOrchestrator(OrchestratorActor[str, list]):
        async def execute(self, input: str) -> list:
            return await self.dispatch_parallel([
                (EchoAgent, "ok"),
                (BrokenAgent, "bad"),
            ])

    system = ActorSystem("t")
    ref = await system.spawn(FailingOrchestrator, "orch")
    with pytest.raises(Exception, match="boom: bad"):
        await ref.ask(Task(input="x"))
    await system.shutdown()


async def test_dispatch_parallel_is_concurrent():
    """Parallel dispatch completes in ~1x delay, not N×delay."""

    class ConcurrentOrchestrator(OrchestratorActor[str, list]):
        async def execute(self, input: str) -> list:
            return await self.dispatch_parallel([
                (DelayedAgent, "a"),
                (DelayedAgent, "b"),
                (DelayedAgent, "c"),
            ])

    system = ActorSystem("t")
    ref = await system.spawn(ConcurrentOrchestrator, "orch")
    start = time.monotonic()
    result = await ref.ask(Task(input="x"), timeout=5.0)
    elapsed = time.monotonic() - start
    assert result.output == ["a", "b", "c"]
    assert elapsed < 0.25, f"expected concurrent execution (~0.1s), got {elapsed:.2f}s"
    await system.shutdown()


# ---------------------------------------------------------------------------
# Nested orchestration
# ---------------------------------------------------------------------------


async def test_orchestrator_can_dispatch_other_orchestrator():
    """OrchestratorActor can be a child of another OrchestratorActor."""

    class InnerOrchestrator(OrchestratorActor[str, list]):
        async def execute(self, input: str) -> list:
            return await self.dispatch_parallel([
                (EchoAgent, input + "-1"),
                (EchoAgent, input + "-2"),
            ])

    class OuterOrchestrator(OrchestratorActor[str, list]):
        async def execute(self, input: str) -> list:
            return await self.dispatch(InnerOrchestrator, input)

    system = ActorSystem("t")
    ref = await system.spawn(OuterOrchestrator, "outer")
    result = await ref.ask(Task(input="x"), timeout=5.0)
    assert result.output == ["x-1", "x-2"]
    await system.shutdown()
