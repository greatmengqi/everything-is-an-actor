"""Agent layer benchmarks — Task round-trip, orchestration primitives, streaming."""

import asyncio
import time

from everything_is_an_actor.core.system import ActorSystem
from everything_is_an_actor.agents import AgentActor, AgentSystem, Task


def fmt(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


# ---------------------------------------------------------------------------
# Agent fixtures
# ---------------------------------------------------------------------------


class EchoAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return input


class UpperAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return input.upper()


class ChunkAgent(AgentActor[str, list]):
    """Yields n chunks per call — used for streaming benchmarks."""

    async def execute(self, input: str):
        n = int(input)
        for i in range(n):
            yield str(i)


# ---------------------------------------------------------------------------
# Benchmarks
# ---------------------------------------------------------------------------


async def bench_agent_ask_throughput(n=5_000):
    """Task round-trip throughput through AgentActor."""
    system = AgentSystem(ActorSystem("bench"))
    ref = await system.spawn(EchoAgent, "echo")

    # warmup
    for _ in range(50):
        await ref.ask(Task(input="w"))

    start = time.perf_counter()
    for _ in range(n):
        await ref.ask(Task(input="ping"))
    elapsed = time.perf_counter() - start

    await system.shutdown()
    rate = n / elapsed
    print(f"  AgentActor ask throughput:  {fmt(n)} tasks in {elapsed:.2f}s = {fmt(int(rate))}/s")


async def bench_agent_ask_latency(n=2_000):
    """Task round-trip latency percentiles."""
    system = AgentSystem(ActorSystem("bench"))
    ref = await system.spawn(EchoAgent, "echo")

    for _ in range(50):
        await ref.ask(Task(input="w"))

    latencies = []
    for _ in range(n):
        t0 = time.perf_counter()
        await ref.ask(Task(input="ping"))
        latencies.append((time.perf_counter() - t0) * 1_000_000)

    await system.shutdown()
    latencies.sort()
    p50 = latencies[len(latencies) // 2]
    p99 = latencies[int(len(latencies) * 0.99)]
    print(f"  AgentActor ask latency:     p50={p50:.0f}µs  p99={p99:.0f}µs")


async def bench_sequence(num_tasks=50, trials=20):
    """sequence() fan-out: N concurrent ephemeral agents per call."""
    system = AgentSystem(ActorSystem("bench"))

    class OrchestratorAgent(AgentActor[str, list]):
        async def execute(self, input: str) -> list:
            n = int(input)
            results = await self.context.sequence(
                [(EchoAgent, Task(input=f"item-{i}")) for i in range(n)]
            )
            return [r.output for r in results]

    ref = await system.spawn(OrchestratorAgent, "orch")

    # warmup
    await ref.ask(Task(input=str(num_tasks)))

    start = time.perf_counter()
    for _ in range(trials):
        result = await ref.ask(Task(input=str(num_tasks)))
        assert len(result.output) == num_tasks
    elapsed = time.perf_counter() - start

    await system.shutdown()
    total_tasks = trials * num_tasks
    rate = total_tasks / elapsed
    print(
        f"  sequence({num_tasks} agents):         {trials} calls in {elapsed:.2f}s "
        f"= {fmt(int(rate))} child tasks/s"
    )


async def bench_traverse(n=100, trials=10):
    """traverse(): map N inputs through one agent."""
    system = AgentSystem(ActorSystem("bench"))

    class OrchestratorAgent(AgentActor[str, list]):
        async def execute(self, input: str) -> list:
            k = int(input)
            results = await self.context.traverse([f"item-{i}" for i in range(k)], UpperAgent)
            return [r.output for r in results]

    ref = await system.spawn(OrchestratorAgent, "orch")

    await ref.ask(Task(input=str(n)))  # warmup

    start = time.perf_counter()
    for _ in range(trials):
        result = await ref.ask(Task(input=str(n)))
        assert len(result.output) == n
    elapsed = time.perf_counter() - start

    await system.shutdown()
    rate = (trials * n) / elapsed
    print(
        f"  traverse({n} inputs):         {trials} calls in {elapsed:.2f}s "
        f"= {fmt(int(rate))} items/s"
    )


async def bench_streaming(chunks=100, trials=20):
    """ask_stream(): throughput of task_chunk events through AgentActor."""
    from everything_is_an_actor.agents.task import StreamEvent, StreamResult

    system = AgentSystem(ActorSystem("bench"))
    ref = await system.spawn(ChunkAgent, "chunks")

    # warmup
    async for _ in ref.ask_stream(Task(input="10")):
        pass

    start = time.perf_counter()
    total_chunks = 0
    for _ in range(trials):
        async for item in ref.ask_stream(Task(input=str(chunks))):
            match item:
                case StreamEvent(event=e) if e.type == "task_chunk":
                    total_chunks += 1
                case StreamResult():
                    pass
    elapsed = time.perf_counter() - start

    await system.shutdown()
    rate = total_chunks / elapsed
    print(
        f"  ask_stream({chunks} chunks):      {trials} calls in {elapsed:.2f}s "
        f"= {fmt(int(rate))} chunks/s"
    )


async def bench_system_run(n=20):
    """AgentSystem.run(): end-to-end event streaming overhead."""
    system = AgentSystem(ActorSystem("bench"))

    latencies = []
    for _ in range(n):
        t0 = time.perf_counter()
        async for _ in system.run(EchoAgent, "hello"):
            pass
        latencies.append((time.perf_counter() - t0) * 1_000)

    latencies.sort()
    p50 = latencies[len(latencies) // 2]
    p99 = latencies[int(len(latencies) * 0.99)]
    print(f"  AgentSystem.run() latency:  p50={p50:.1f}ms  p99={p99:.1f}ms  (spawn+run+stream)")


async def main():
    print("=" * 60)
    print("  Agent Layer Benchmarks")
    print("=" * 60)
    print()

    print("[Task round-trip]")
    await bench_agent_ask_throughput()
    await bench_agent_ask_latency()
    print()

    print("[Orchestration primitives]")
    await bench_sequence()
    await bench_traverse()
    print()

    print("[Streaming]")
    await bench_streaming()
    print()

    print("[AgentSystem.run]")
    await bench_system_run()
    print()

    print("=" * 60)
    print("  Done")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
