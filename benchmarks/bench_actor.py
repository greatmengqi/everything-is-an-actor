"""Actor framework benchmarks — throughput, latency, concurrency."""

import asyncio
import time

from actor_for_agents import Actor, ActorSystem, Middleware


class NoopActor(Actor):
    async def on_receive(self, message):
        return message


class CounterActor(Actor):
    async def on_started(self):
        self.count = 0

    async def on_receive(self, message):
        if message == "inc":
            self.count += 1
            return self.count
        if message == "get":
            return self.count
        return self.count


class ChainActor(Actor):
    """Forwards message to next actor in chain."""
    next_ref = None

    async def on_receive(self, message):
        if self.next_ref is not None:
            return await self.next_ref.ask(message)
        return message


class ComputeActor(Actor):
    """Simulates CPU work via thread pool."""
    async def on_receive(self, message):
        def fib(n):
            a, b = 0, 1
            for _ in range(n):
                a, b = b, a + b
            return a
        return await self.context.run_in_executor(fib, message)


class CountMiddleware(Middleware):
    def __init__(self):
        self.count = 0

    async def on_receive(self, ctx, message, next_fn):
        self.count += 1
        return await next_fn(ctx, message)


def fmt(n):
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.0f}K"
    return str(n)


async def bench_tell_throughput(n=100_000):
    """Measure tell (fire-and-forget) throughput."""
    system = ActorSystem("bench")
    ref = await system.spawn(CounterActor, "counter", mailbox_size=n + 10)

    start = time.perf_counter()
    for _ in range(n):
        await ref.tell("inc")
    # Wait for all messages to be processed
    count = await ref.ask("get", timeout=30.0)
    if count != n:
        print(f"  warning: expected {n} processed, got {count}")
    elapsed = time.perf_counter() - start

    await system.shutdown()
    rate = n / elapsed
    print(f"  tell throughput:     {fmt(n)} msgs in {elapsed:.2f}s = {fmt(int(rate))}/s")


async def bench_ask_throughput(n=50_000):
    """Measure ask (request-response) throughput."""
    system = ActorSystem("bench")
    ref = await system.spawn(NoopActor, "echo")

    start = time.perf_counter()
    for _ in range(n):
        await ref.ask("ping")
    elapsed = time.perf_counter() - start

    await system.shutdown()
    rate = n / elapsed
    print(f"  ask throughput:      {fmt(n)} msgs in {elapsed:.2f}s = {fmt(int(rate))}/s")


async def bench_ask_latency(n=10_000):
    """Measure ask round-trip latency percentiles."""
    system = ActorSystem("bench")
    ref = await system.spawn(NoopActor, "echo")

    # Warmup
    for _ in range(100):
        await ref.ask("warmup")

    latencies = []
    for _ in range(n):
        t0 = time.perf_counter()
        await ref.ask("ping")
        latencies.append((time.perf_counter() - t0) * 1_000_000)  # microseconds

    await system.shutdown()
    latencies.sort()
    p50 = latencies[len(latencies) // 2]
    p99 = latencies[int(len(latencies) * 0.99)]
    p999 = latencies[int(len(latencies) * 0.999)]
    print(f"  ask latency:        p50={p50:.0f}µs  p99={p99:.0f}µs  p99.9={p999:.0f}µs")


async def bench_concurrent_actors(num_actors=1000, msgs_per_actor=100):
    """Measure throughput with many concurrent actors."""
    system = ActorSystem("bench")
    refs = []
    for i in range(num_actors):
        refs.append(await system.spawn(CounterActor, f"a{i}", mailbox_size=msgs_per_actor + 10))

    start = time.perf_counter()

    async def send_batch(ref, n):
        for i in range(n):
            await ref.tell("inc")
            # Yield control every 50 msgs so actor loops can drain
            if i % 50 == 49:
                await asyncio.sleep(0)
        return await ref.ask("get", timeout=30.0)

    results = await asyncio.gather(*[send_batch(r, msgs_per_actor) for r in refs])
    elapsed = time.perf_counter() - start

    total = num_actors * msgs_per_actor
    delivered = sum(results)
    rate = total / elapsed
    loss = total - delivered
    print(f"  {num_actors} actors × {msgs_per_actor} msgs: {fmt(total)} in {elapsed:.2f}s = {fmt(int(rate))}/s (loss: {loss})")

    await system.shutdown()


async def bench_actor_chain(depth=100):
    """Measure ask latency through a chain of actors (hop overhead)."""
    system = ActorSystem("bench")
    refs = []
    for i in range(depth):
        refs.append(await system.spawn(ChainActor, f"c{i}"))
    # Link chain: c0 → c1 → ... → c99
    for i in range(depth - 1):
        refs[i]._cell.actor.next_ref = refs[i + 1]

    start = time.perf_counter()
    result = await refs[0].ask("ping", timeout=30.0)
    elapsed = time.perf_counter() - start

    assert result == "ping"
    per_hop = elapsed / depth * 1_000_000  # µs
    print(f"  chain {depth} hops:     {elapsed*1000:.1f}ms total, {per_hop:.0f}µs/hop")

    await system.shutdown()


async def bench_middleware_overhead(n=50_000):
    """Measure overhead of middleware pipeline."""
    mw = CountMiddleware()

    system_plain = ActorSystem("plain")
    ref_plain = await system_plain.spawn(NoopActor, "echo")

    system_mw = ActorSystem("mw")
    ref_mw = await system_mw.spawn(NoopActor, "echo", middlewares=[mw])

    # Plain
    t0 = time.perf_counter()
    for _ in range(n):
        await ref_plain.ask("p")
    plain_elapsed = time.perf_counter() - t0

    # With middleware
    t0 = time.perf_counter()
    for _ in range(n):
        await ref_mw.ask("p")
    mw_elapsed = time.perf_counter() - t0

    overhead = ((mw_elapsed - plain_elapsed) / plain_elapsed) * 100
    print(f"  middleware overhead: {overhead:+.1f}% ({fmt(n)} ask calls, 1 middleware)")

    await system_plain.shutdown()
    await system_mw.shutdown()


async def bench_executor_parallel(num_tasks=16):
    """Measure thread pool parallelism with CPU work."""
    system = ActorSystem("bench", executor_workers=8)
    refs = [await system.spawn(ComputeActor, f"cpu{i}") for i in range(num_tasks)]

    start = time.perf_counter()
    results = await asyncio.gather(*[r.ask(10_000, timeout=30.0) for r in refs])
    elapsed = time.perf_counter() - start

    print(f"  executor parallel:  {num_tasks} fib(10K) in {elapsed*1000:.0f}ms ({num_tasks/elapsed:.0f} tasks/s)")

    await system.shutdown()


async def bench_spawn_teardown(n=5000):
    """Measure actor spawn + shutdown speed."""
    system = ActorSystem("bench")

    start = time.perf_counter()
    refs = []
    for i in range(n):
        refs.append(await system.spawn(NoopActor, f"a{i}"))
    spawn_elapsed = time.perf_counter() - start

    start = time.perf_counter()
    await system.shutdown()
    shutdown_elapsed = time.perf_counter() - start

    print(f"  spawn {n}:          {spawn_elapsed*1000:.0f}ms ({n/spawn_elapsed:.0f}/s)")
    print(f"  shutdown {n}:       {shutdown_elapsed*1000:.0f}ms")


async def main():
    print("=" * 60)
    print("  Actor Framework Benchmarks")
    print("=" * 60)
    print()

    print("[Throughput]")
    await bench_tell_throughput()
    await bench_ask_throughput()
    print()

    print("[Latency]")
    await bench_ask_latency()
    await bench_actor_chain()
    print()

    print("[Concurrency]")
    await bench_concurrent_actors()
    await bench_executor_parallel()
    print()

    print("[Overhead]")
    await bench_middleware_overhead()
    print()

    print("[Lifecycle]")
    await bench_spawn_teardown()
    print()

    print("=" * 60)
    print("  Done")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
