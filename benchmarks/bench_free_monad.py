"""Free Monad benchmarks — throughput, latency, comparison with direct calls."""

import asyncio
import time

from everything_is_an_actor import Actor, ActorSystem
from everything_is_an_actor.actor_f import spawn, tell, ask, tell_direct
from everything_is_an_actor.interpreter import run_free


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


def fmt(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


async def bench_free_tell_throughput(n=100_000):
    """Measure tell throughput via Free Monad (system.run_free)."""
    system = ActorSystem("bench")
    ref = await system.spawn(CounterActor, "counter", mailbox_size=n + 10)

    start = time.perf_counter()
    for _ in range(n):
        await system.run_free(tell(ref, "inc"))
    # Wait for processing
    count = await ref.ask("get", timeout=30.0)
    if count != n:
        print(f"  warning: expected {n} processed, got {count}")
    elapsed = time.perf_counter() - start

    await system.shutdown()
    rate = n / elapsed
    print(f"  run_free tell:      {fmt(n)} msgs in {elapsed:.2f}s = {fmt(int(rate))}/s")


async def bench_direct_tell_throughput(n=100_000):
    """Measure tell throughput via tell_direct (bypasses Free monad)."""
    system = ActorSystem("bench")
    ref = await system.spawn(CounterActor, "counter", mailbox_size=n + 10)

    start = time.perf_counter()
    for _ in range(n):
        await tell_direct(ref, "inc")
    # Wait for processing
    count = await ref.ask("get", timeout=30.0)
    if count != n:
        print(f"  warning: expected {n} processed, got {count}")
    elapsed = time.perf_counter() - start

    await system.shutdown()
    rate = n / elapsed
    print(f"  tell_direct:         {fmt(n)} msgs in {elapsed:.2f}s = {fmt(int(rate))}/s")


async def bench_free_ask_throughput(n=50_000):
    """Measure ask throughput via Free Monad (system.run_free)."""
    system = ActorSystem("bench")
    ref = await system.spawn(NoopActor, "echo")

    start = time.perf_counter()
    for _ in range(n):
        await system.run_free(ask(ref, "ping"))
    elapsed = time.perf_counter() - start

    await system.shutdown()
    rate = n / elapsed
    print(f"  run_free ask:       {fmt(n)} msgs in {elapsed:.2f}s = {fmt(int(rate))}/s")


async def bench_direct_ask_throughput(n=50_000):
    """Measure ask throughput via direct ref.ask (baseline)."""
    system = ActorSystem("bench")
    ref = await system.spawn(NoopActor, "echo")

    start = time.perf_counter()
    for _ in range(n):
        await ref.ask("ping")
    elapsed = time.perf_counter() - start

    await system.shutdown()
    rate = n / elapsed
    print(f"  direct ask:          {fmt(n)} msgs in {elapsed:.2f}s = {fmt(int(rate))}/s")


async def bench_free_ask_latency(n=10_000):
    """Measure run_free round-trip latency percentiles."""
    system = ActorSystem("bench")
    ref = await system.spawn(NoopActor, "echo")

    # Warmup
    for _ in range(100):
        await system.run_free(ask(ref, "warmup"))

    latencies = []
    for _ in range(n):
        t0 = time.perf_counter()
        await system.run_free(ask(ref, "ping"))
        latencies.append((time.perf_counter() - t0) * 1_000_000)

    await system.shutdown()
    latencies.sort()
    p50 = latencies[len(latencies) // 2]
    p99 = latencies[int(len(latencies) * 0.99)]
    p999 = latencies[int(len(latencies) * 0.999)]
    print(f"  run_free latency:    p50={p50:.0f}µs  p99={p99:.0f}µs  p99.9={p999:.0f}µs")


async def bench_direct_ask_latency(n=10_000):
    """Measure direct ask round-trip latency percentiles (baseline)."""
    system = ActorSystem("bench")
    ref = await system.spawn(NoopActor, "echo")

    # Warmup
    for _ in range(100):
        await ref.ask("warmup")

    latencies = []
    for _ in range(n):
        t0 = time.perf_counter()
        await ref.ask("ping")
        latencies.append((time.perf_counter() - t0) * 1_000_000)

    await system.shutdown()
    latencies.sort()
    p50 = latencies[len(latencies) // 2]
    p99 = latencies[int(len(latencies) * 0.99)]
    p999 = latencies[int(len(latencies) * 0.999)]
    print(f"  direct latency:      p50={p50:.0f}µs  p99={p99:.0f}µs  p99.9={p999:.0f}µs")


async def bench_free_composition_throughput(n=20_000):
    """Measure throughput of Free Monad workflow with multiple operations."""
    system = ActorSystem("bench")
    ref = await system.spawn(CounterActor, "counter")

    # Build Free workflow using flatMap chain
    def workflow():
        return tell(ref, "inc").flatMap(lambda _: ask(ref, "get"))

    start = time.perf_counter()
    for _ in range(n):
        await system.run_free(workflow())
    elapsed = time.perf_counter() - start

    await system.shutdown()
    rate = n / elapsed
    print(f"  run_free compose:   {fmt(n)} ops in {elapsed:.2f}s = {fmt(int(rate))}/s")


async def bench_mock_interpreter_throughput(n=100_000):
    """Measure MockInterpreter throughput (no real actors)."""
    from everything_is_an_actor.interpreter import MockSystem, run_free_mock
    from everything_is_an_actor.actor_f import ask

    system = MockSystem()
    ref = system.get_ref("echo")
    ref.set_reply("ping", "pong")

    workflow = ask(ref, "ping")

    start = time.perf_counter()
    for _ in range(n):
        run_free_mock(system, workflow)
    elapsed = time.perf_counter() - start

    rate = n / elapsed
    print(f"  mock interpreter:   {fmt(n)} msgs in {elapsed:.2f}s = {fmt(int(rate))}/s")


async def main():
    print("=" * 60)
    print("  Free Monad Benchmarks")
    print("=" * 60)
    print()

    print("[Throughput Comparison]")
    await bench_direct_ask_throughput()
    await bench_free_ask_throughput()
    print()

    print("[Tell Throughput]")
    await bench_direct_tell_throughput()
    await bench_free_tell_throughput()
    print()

    print("[Latency Comparison]")
    await bench_direct_ask_latency()
    await bench_free_ask_latency()
    print()

    print("[Free Monad Specific]")
    await bench_free_composition_throughput()
    await bench_mock_interpreter_throughput()
    print()

    print("=" * 60)
    print("  Done")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
