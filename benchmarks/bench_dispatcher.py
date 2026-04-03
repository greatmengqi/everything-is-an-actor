"""Benchmark: Dispatcher performance comparison.

Compares throughput and latency across:
1. No dispatcher (default ActorSystem — single loop)
2. DefaultDispatcher (explicit, same loop)
3. PoolDispatcher(1) — single worker thread
4. PoolDispatcher(4) — 4 worker threads
5. PoolDispatcher(8) — 8 worker threads
"""

import asyncio
import time
import statistics

from everything_is_an_actor import Actor, ActorSystem
from everything_is_an_actor.dispatcher import DefaultDispatcher, PoolDispatcher
from everything_is_an_actor.agents.agent_actor import AgentActor
from everything_is_an_actor.agents.task import Task


class EchoActor(Actor[str, str]):
    async def on_receive(self, message):
        return message


class ComputeActor(Actor[int, int]):
    """Simulates light CPU work."""
    async def on_receive(self, message):
        total = 0
        for i in range(message):
            total += i
        return total


class IOActor(Actor[float, str]):
    """Simulates async I/O wait."""
    async def on_receive(self, message):
        await asyncio.sleep(message)
        return "done"


class EchoAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return input.upper()


async def bench_throughput(label: str, system: ActorSystem, actor_cls, msg, n: int):
    """Measure ask throughput: N sequential asks."""
    ref = await system.spawn(actor_cls, "bench")
    # Warmup
    for _ in range(min(100, n)):
        await system.ask(ref, msg)

    start = time.perf_counter()
    for _ in range(n):
        await system.ask(ref, msg)
    elapsed = time.perf_counter() - start

    ops = n / elapsed
    avg_us = (elapsed / n) * 1_000_000
    print(f"  {label:30s}  {ops:>10,.0f} ops/s  {avg_us:>8.1f} us/op  ({n} asks in {elapsed:.2f}s)")
    ref.stop()
    await ref.join()
    return ops, avg_us


async def bench_concurrent(label: str, system: ActorSystem, actor_cls, msg, num_actors: int, asks_per_actor: int):
    """Measure concurrent throughput: N actors, M asks each."""
    refs = [await system.spawn(actor_cls, f"a{i}") for i in range(num_actors)]

    async def drive(ref, m):
        for _ in range(m):
            await system.ask(ref, msg)

    start = time.perf_counter()
    await asyncio.gather(*[drive(r, asks_per_actor) for r in refs])
    elapsed = time.perf_counter() - start

    total = num_actors * asks_per_actor
    ops = total / elapsed
    print(f"  {label:30s}  {ops:>10,.0f} ops/s  ({num_actors} actors x {asks_per_actor} asks = {total} in {elapsed:.2f}s)")
    for r in refs:
        r.stop()
    await asyncio.gather(*[r.join() for r in refs])
    return ops


async def bench_latency(label: str, system: ActorSystem, actor_cls, msg, n: int):
    """Measure per-ask latency distribution."""
    ref = await system.spawn(actor_cls, "lat")
    # Warmup
    for _ in range(50):
        await system.ask(ref, msg)

    latencies = []
    for _ in range(n):
        t0 = time.perf_counter()
        await system.ask(ref, msg)
        latencies.append((time.perf_counter() - t0) * 1_000_000)

    p50 = statistics.median(latencies)
    p99 = sorted(latencies)[int(n * 0.99)]
    avg = statistics.mean(latencies)
    print(f"  {label:30s}  avg={avg:>7.1f}us  p50={p50:>7.1f}us  p99={p99:>7.1f}us")
    ref.stop()
    await ref.join()
    return avg, p50, p99


async def run_suite(label: str, system: ActorSystem, n_throughput=10000, n_latency=2000):
    """Run a full benchmark suite for one configuration."""
    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"{'='*70}")

    print(f"\n  [Throughput — sequential ask]")
    await bench_throughput("echo", system, EchoActor, "hello", n_throughput)
    await bench_throughput("compute(100)", system, ComputeActor, 100, n_throughput)

    print(f"\n  [Throughput — concurrent (10 actors)]")
    await bench_concurrent("echo x10", system, EchoActor, "hello", 10, n_throughput // 10)
    await bench_concurrent("compute x10", system, ComputeActor, 100, 10, n_throughput // 10)

    print(f"\n  [Latency distribution]")
    await bench_latency("echo", system, EchoActor, "hello", n_latency)
    await bench_latency("compute(100)", system, ComputeActor, 100, n_latency)

    print(f"\n  [AgentActor]")
    await bench_throughput("agent echo", system, EchoAgent, Task(input="hello"), n_throughput // 2)


async def main():
    import os
    cpu_count = os.cpu_count() or 4
    print(f"CPU cores: {cpu_count}")
    print(f"Python: {__import__('sys').version}")

    N = 10000

    # 1. Baseline: no dispatcher
    system = ActorSystem("baseline")
    await run_suite("Baseline (no dispatcher)", system, N)
    await system.shutdown()

    # 2. DefaultDispatcher
    system = ActorSystem("default-disp", dispatcher=DefaultDispatcher())
    await run_suite("DefaultDispatcher (same loop)", system, N)
    await system.shutdown()

    # 3. PoolDispatcher(1)
    d1 = PoolDispatcher(pool_size=1)
    await d1.start()
    system = ActorSystem("pool-1", dispatcher=d1)
    await run_suite("PoolDispatcher(1)", system, N)
    await system.shutdown()
    await d1.shutdown()

    # 4. PoolDispatcher(4)
    d4 = PoolDispatcher(pool_size=4)
    await d4.start()
    system = ActorSystem("pool-4", dispatcher=d4)
    await run_suite("PoolDispatcher(4)", system, N)
    await system.shutdown()
    await d4.shutdown()

    # 5. PoolDispatcher(8)
    d8 = PoolDispatcher(pool_size=8)
    await d8.start()
    system = ActorSystem("pool-8", dispatcher=d8)
    await run_suite("PoolDispatcher(8)", system, N)
    await system.shutdown()
    await d8.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
