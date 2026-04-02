"""Tests for ActorContext.dispatch / dispatch_parallel (M2)."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from everything_is_an_actor import ActorSystem
from everything_is_an_actor.agents import AgentActor, Task, TaskResult

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
    class SimpleAgent(AgentActor[str, str]):
        async def execute(self, input: str) -> str:
            result: TaskResult[str] = await self.context.dispatch(EchoAgent, Task(input=input))
            return result.output

    system = ActorSystem("t")
    ref = await system.spawn(SimpleAgent, "a")
    result = await ref.ask(Task(input="hello"))
    assert result.output == "hello"
    await system.shutdown()


async def test_dispatch_exception_propagates():
    class BrokenCaller(AgentActor[str, str]):
        async def execute(self, input: str) -> str:
            result: TaskResult[str] = await self.context.dispatch(BrokenAgent, Task(input=input))
            return result.output

    system = ActorSystem("t")
    ref = await system.spawn(BrokenCaller, "a")
    with pytest.raises(Exception, match="boom: bad"):
        await ref.ask(Task(input="bad"))
    await system.shutdown()


async def test_dispatch_multiple_sequential_calls():
    """dispatch() can be called multiple times with the same class (unique names)."""

    class MultiCaller(AgentActor[list, list]):
        async def execute(self, input: list) -> list:
            results = []
            for item in input:
                r: TaskResult[str] = await self.context.dispatch(EchoAgent, Task(input=item))
                results.append(r.output)
            return results

    system = ActorSystem("t")
    ref = await system.spawn(MultiCaller, "a")
    result = await ref.ask(Task(input=["a", "b", "c"]))
    assert result.output == ["a", "b", "c"]
    await system.shutdown()


async def test_dispatch_child_stopped_after_completion():
    """Ephemeral child is stopped and removed after dispatch completes."""

    class EphemeralCaller(AgentActor[str, str]):
        async def execute(self, input: str) -> str:
            r: TaskResult[str] = await self.context.dispatch(EchoAgent, Task(input=input))
            # no sleep needed — dispatch() awaits join() so child is fully stopped on return
            assert len(self.context.children) == 0, "child should be stopped and removed"
            return r.output

    system = ActorSystem("t")
    ref = await system.spawn(EphemeralCaller, "a")
    result = await ref.ask(Task(input="x"))
    assert result.output == "x"
    await system.shutdown()


async def test_dispatch_child_stopped_on_exception():
    """Ephemeral child is stopped even when execute() raises."""

    class CleanupCaller(AgentActor[str, str]):
        async def execute(self, input: str) -> str:
            try:
                await self.context.dispatch(BrokenAgent, Task(input=input))
            except Exception:
                pass
            # no sleep needed — dispatch() awaits join() even on exception path
            assert len(self.context.children) == 0
            return "recovered"

    system = ActorSystem("t")
    ref = await system.spawn(CleanupCaller, "a")
    result = await ref.ask(Task(input="x"))
    assert result.output == "recovered"
    await system.shutdown()


async def test_dispatch_timeout_raises():
    class SlowCaller(AgentActor[str, str]):
        async def execute(self, input: str) -> str:
            result: TaskResult[str] = await self.context.dispatch(SlowAgent, Task(input=input), timeout=0.1)
            return result.output

    system = ActorSystem("t")
    ref = await system.spawn(SlowCaller, "a")
    with pytest.raises(asyncio.TimeoutError):
        await ref.ask(Task(input="x"), timeout=5.0)
    await system.shutdown()


# ---------------------------------------------------------------------------
# dispatch_parallel — concurrent children
# ---------------------------------------------------------------------------


async def test_dispatch_parallel_returns_ordered_results():
    class ParallelCaller(AgentActor[list, list]):
        async def execute(self, input: list) -> list:
            results: list[TaskResult[str]] = await self.context.dispatch_parallel(
                [(EchoAgent, Task(input=v)) for v in input]
            )
            return [r.output for r in results]

    system = ActorSystem("t")
    ref = await system.spawn(ParallelCaller, "a")
    result = await ref.ask(Task(input=["x", "y", "z"]))
    assert result.output == ["x", "y", "z"]
    await system.shutdown()


async def test_dispatch_parallel_different_agent_types():
    class MixedCaller(AgentActor[str, list]):
        async def execute(self, input: str) -> list:
            results = await self.context.dispatch_parallel(
                [
                    (EchoAgent, Task(input=input)),
                    (UpperAgent, Task(input=input)),
                ]
            )
            return [r.output for r in results]

    system = ActorSystem("t")
    ref = await system.spawn(MixedCaller, "a")
    result = await ref.ask(Task(input="hello"))
    assert result.output == ["hello", "HELLO"]
    await system.shutdown()


async def test_dispatch_parallel_empty_list():
    class EmptyCaller(AgentActor[Any, list]):
        async def execute(self, input: Any) -> list:
            return await self.context.dispatch_parallel([])

    system = ActorSystem("t")
    ref = await system.spawn(EmptyCaller, "a")
    result = await ref.ask(Task(input=None))
    assert result.output == []
    await system.shutdown()


async def test_dispatch_parallel_exception_propagates():
    class FailingCaller(AgentActor[str, list]):
        async def execute(self, input: str) -> list:
            return await self.context.dispatch_parallel(
                [
                    (EchoAgent, Task(input="ok")),
                    (BrokenAgent, Task(input="bad")),
                ]
            )

    system = ActorSystem("t")
    ref = await system.spawn(FailingCaller, "a")
    with pytest.raises(Exception, match="boom: bad"):
        await ref.ask(Task(input="x"))
    await system.shutdown()


async def test_dispatch_parallel_is_concurrent():
    """Parallel dispatch completes in ~1x delay, not N×delay."""

    class ConcurrentCaller(AgentActor[str, list]):
        async def execute(self, input: str) -> list:
            results = await self.context.dispatch_parallel(
                [
                    (DelayedAgent, Task(input="a")),
                    (DelayedAgent, Task(input="b")),
                    (DelayedAgent, Task(input="c")),
                ]
            )
            return [r.output for r in results]

    system = ActorSystem("t")
    ref = await system.spawn(ConcurrentCaller, "a")
    start = time.monotonic()
    result = await ref.ask(Task(input="x"), timeout=5.0)
    elapsed = time.monotonic() - start
    assert result.output == ["a", "b", "c"]
    assert elapsed < 0.25, f"expected concurrent execution (~0.1s), got {elapsed:.2f}s"
    await system.shutdown()


# ---------------------------------------------------------------------------
# dispatch to existing ActorRef
# ---------------------------------------------------------------------------


async def test_dispatch_to_existing_ref():
    """dispatch() with an ActorRef reuses the running actor instead of spawning."""
    system = ActorSystem("t")
    existing = await system.spawn(EchoAgent, "persistent")

    class ReusingAgent(AgentActor[str, str]):
        async def execute(self, input: str) -> str:
            r: TaskResult[str] = await self.context.dispatch(existing, Task(input=input))
            return r.output

    ref = await system.spawn(ReusingAgent, "reuser")
    result = await ref.ask(Task(input="hello"))
    assert result.output == "hello"
    assert existing.is_alive  # not stopped — caller owns the lifecycle
    await system.shutdown()


async def test_dispatch_parallel_mix_class_and_ref():
    """dispatch_parallel() accepts a mix of class targets and ActorRef targets."""
    system = ActorSystem("t")
    persistent = await system.spawn(UpperAgent, "upper")

    class MixedAgent(AgentActor[str, list]):
        async def execute(self, input: str) -> list:
            results = await self.context.dispatch_parallel(
                [
                    (EchoAgent, Task(input=input)),
                    (persistent, Task(input=input)),
                ]
            )
            return [r.output for r in results]

    ref = await system.spawn(MixedAgent, "mixer")
    result = await ref.ask(Task(input="hi"))
    assert result.output == ["hi", "HI"]
    await system.shutdown()


# ---------------------------------------------------------------------------
# Regression: no child leaks after dispatch_parallel partial failure
# ---------------------------------------------------------------------------


async def test_dispatch_parallel_no_child_leak_on_failure():
    """After a partial failure in dispatch_parallel, all ephemeral children are stopped."""

    class LeakCheckAgent(AgentActor[str, dict]):
        async def execute(self, input: str) -> dict:
            try:
                await self.context.dispatch_parallel(
                    [
                        (DelayedAgent, Task(input="slow")),  # still running when BrokenAgent raises
                        (BrokenAgent, Task(input="bad")),
                    ]
                )
            except Exception:
                pass
            # no sleep — dispatch_parallel cancels+joins all children before raising
            return {"children": len(self.context.children)}

    system = ActorSystem("t")
    ref = await system.spawn(LeakCheckAgent, "lc")
    result = await ref.ask(Task(input="x"), timeout=5.0)
    assert result.output["children"] == 0, "orphaned children after partial failure"
    await system.shutdown()


async def test_dispatch_deterministic_name():
    """dispatch() with explicit name uses that name for the ephemeral child."""

    class NamedCaller(AgentActor[str, str]):
        async def execute(self, input: str) -> str:
            r: TaskResult[str] = await self.context.dispatch(EchoAgent, Task(input=input), name="my-echo")
            return r.output

    system = ActorSystem("t")
    ref = await system.spawn(NamedCaller, "caller")
    result = await ref.ask(Task(input="hello"))
    assert result.output == "hello"
    await system.shutdown()


# ---------------------------------------------------------------------------
# Nested dispatch
# ---------------------------------------------------------------------------


async def test_nested_dispatch():
    """An AgentActor can dispatch to another AgentActor that also dispatches."""

    class InnerAgent(AgentActor[str, list]):
        async def execute(self, input: str) -> list:
            results = await self.context.dispatch_parallel(
                [
                    (EchoAgent, Task(input=input + "-1")),
                    (EchoAgent, Task(input=input + "-2")),
                ]
            )
            return [r.output for r in results]

    class OuterAgent(AgentActor[str, list]):
        async def execute(self, input: str) -> list:
            r: TaskResult[list] = await self.context.dispatch(InnerAgent, Task(input=input))
            return r.output

    system = ActorSystem("t")
    ref = await system.spawn(OuterAgent, "outer")
    result = await ref.ask(Task(input="x"), timeout=5.0)
    assert result.output == ["x-1", "x-2"]
    await system.shutdown()
