"""Stress tests for ActorContext.dispatch / dispatch_parallel."""

from __future__ import annotations

import time

import pytest

from actor_for_agents import ActorSystem
from actor_for_agents.agents import AgentActor, Task, TaskResult

pytestmark = pytest.mark.anyio


class EchoAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return input


class SumAgent(AgentActor[list, int]):
    async def execute(self, input: list) -> int:
        return sum(input)


# ---------------------------------------------------------------------------
# 1. High concurrency — large dispatch_parallel fan-out
# ---------------------------------------------------------------------------


async def test_dispatch_parallel_100_concurrent():
    """Fan out to 100 ephemeral agents concurrently."""

    class FanOutAgent(AgentActor[int, list]):
        async def execute(self, input: int) -> list:
            results: list[TaskResult[str]] = await self.context.dispatch_parallel(
                [(EchoAgent, Task(input=str(i))) for i in range(input)]
            )
            return [r.output for r in results]

    system = ActorSystem("stress", executor_workers=4)
    ref = await system.spawn(FanOutAgent, "fanout")
    start = time.monotonic()
    result = await ref.ask(Task(input=100), timeout=30.0)
    elapsed = time.monotonic() - start

    assert len(result.output) == 100
    assert result.output == [str(i) for i in range(100)]
    print(f"\n[fan-out 100] {elapsed:.3f}s")
    await system.shutdown()


async def test_dispatch_parallel_500_concurrent():
    """Fan out to 500 ephemeral agents — tests mailbox + task creation limits."""

    class MassiveFanOut(AgentActor[int, int]):
        async def execute(self, input: int) -> int:
            results: list[TaskResult[str]] = await self.context.dispatch_parallel(
                [(EchoAgent, Task(input=str(i))) for i in range(input)]
            )
            return len(results)

    system = ActorSystem("stress", executor_workers=4)
    ref = await system.spawn(MassiveFanOut, "fanout")
    start = time.monotonic()
    result = await ref.ask(Task(input=500), timeout=60.0)
    elapsed = time.monotonic() - start

    assert result.output == 500
    print(f"\n[fan-out 500] {elapsed:.3f}s")
    await system.shutdown()


# ---------------------------------------------------------------------------
# 2. High-frequency sequential — spawn/stop overhead per dispatch
# ---------------------------------------------------------------------------


async def test_dispatch_sequential_1000():
    """1000 sequential dispatches — measures spawn+ask+stop overhead per call."""

    class SequentialCaller(AgentActor[int, int]):
        async def execute(self, input: int) -> int:
            count = 0
            for i in range(input):
                r: TaskResult[str] = await self.context.dispatch(EchoAgent, Task(input=str(i)))
                assert r.output == str(i)
                count += 1
            return count

    system = ActorSystem("stress", executor_workers=4)
    ref = await system.spawn(SequentialCaller, "seq")
    start = time.monotonic()
    result = await ref.ask(Task(input=1000), timeout=60.0)
    elapsed = time.monotonic() - start

    assert result.output == 1000
    per_call_ms = elapsed / 1000 * 1000
    print(f"\n[sequential 1000] {elapsed:.3f}s total, {per_call_ms:.2f}ms/dispatch")
    await system.shutdown()


# ---------------------------------------------------------------------------
# 3. Reuse existing ref — no spawn overhead
# ---------------------------------------------------------------------------


async def test_dispatch_reuse_ref_1000():
    """1000 dispatches to a persistent actor — no spawn/stop overhead."""
    system = ActorSystem("stress", executor_workers=4)
    persistent = await system.spawn(EchoAgent, "persistent")

    class ReuseCaller(AgentActor[int, int]):
        async def execute(self, input: int) -> int:
            count = 0
            for i in range(input):
                r: TaskResult[str] = await self.context.dispatch(
                    persistent, Task(input=str(i))
                )
                assert r.output == str(i)
                count += 1
            return count

    ref = await system.spawn(ReuseCaller, "reuse")
    start = time.monotonic()
    result = await ref.ask(Task(input=1000), timeout=60.0)
    elapsed = time.monotonic() - start

    assert result.output == 1000
    per_call_ms = elapsed / 1000 * 1000
    print(f"\n[reuse ref 1000] {elapsed:.3f}s total, {per_call_ms:.2f}ms/dispatch")
    await system.shutdown()


# ---------------------------------------------------------------------------
# 4. Nested dispatch — deep chain
# ---------------------------------------------------------------------------


async def test_dispatch_nested_depth_10():
    """Chain of 10 nested dispatches — tests supervision tree depth."""

    def make_chain(depth: int) -> type[AgentActor]:
        if depth == 0:
            return EchoAgent

        inner_cls = make_chain(depth - 1)

        class ChainAgent(AgentActor[str, str]):
            async def execute(self, input: str) -> str:
                r: TaskResult[str] = await self.context.dispatch(
                    inner_cls, Task(input=input)
                )
                return r.output

        ChainAgent.__name__ = f"Chain{depth}"
        return ChainAgent

    root_cls = make_chain(10)
    system = ActorSystem("stress", executor_workers=4)
    ref = await system.spawn(root_cls, "chain-root")
    start = time.monotonic()
    result = await ref.ask(Task(input="deep"), timeout=30.0)
    elapsed = time.monotonic() - start

    assert result.output == "deep"
    print(f"\n[chain depth=10] {elapsed:.3f}s")
    await system.shutdown()


# ---------------------------------------------------------------------------
# 5. Mixed: parallel + sequential interleaved
# ---------------------------------------------------------------------------


async def test_dispatch_mixed_workload():
    """Alternating parallel and sequential dispatches — realistic orchestrator pattern."""

    class MixedOrchestrator(AgentActor[int, dict]):
        async def execute(self, input: int) -> dict:
            # Phase 1: parallel fan-out
            batch1: list[TaskResult[str]] = await self.context.dispatch_parallel(
                [(EchoAgent, Task(input=f"p{i}")) for i in range(input)]
            )
            # Phase 2: sequential aggregation dispatch
            total = 0
            for i in range(input // 10):
                r: TaskResult[str] = await self.context.dispatch(
                    EchoAgent, Task(input=f"s{i}")
                )
                total += len(r.output)
            return {"parallel": len(batch1), "sequential": total}

    system = ActorSystem("stress", executor_workers=4)
    ref = await system.spawn(MixedOrchestrator, "mixed")
    start = time.monotonic()
    result = await ref.ask(Task(input=50), timeout=30.0)
    elapsed = time.monotonic() - start

    assert result.output["parallel"] == 50
    print(f"\n[mixed workload n=50] {elapsed:.3f}s → {result.output}")
    await system.shutdown()
