"""Profiler for actor framework — identify bottlenecks."""

import asyncio
import cProfile
import pstats
from io import StringIO

from actor_for_agents import Actor, ActorSystem


class NoopActor(Actor):
    async def on_receive(self, message):
        return message


async def bench_tell_profiled(n=100_000):
    """Profile tell throughput."""
    profiler = cProfile.Profile()
    profiler.enable()

    system = ActorSystem("bench")
    ref = await system.spawn(NoopActor, "echo", mailbox_size=n + 10)

    for _ in range(n):
        await ref.tell("msg")

    await system.shutdown()

    profiler.disable()
    return profiler


async def bench_ask_profiled(n=50_000):
    """Profile ask throughput."""
    profiler = cProfile.Profile()
    profiler.enable()

    system = ActorSystem("bench")
    ref = await system.spawn(NoopActor, "echo")

    for _ in range(n):
        await ref.ask("ping")

    await system.shutdown()

    profiler.disable()
    return profiler


async def bench_actor_with_chain(n=10_000, depth=10):
    """Profile actor chain (simulates real workload)."""

    class ChainActor(Actor):
        next_ref = None

        async def on_receive(self, message):
            if self.next_ref is not None:
                return await self.next_ref.ask(message)
            return message

    profiler = cProfile.Profile()
    profiler.enable()

    system = ActorSystem("bench")
    refs = []
    for i in range(depth):
        refs.append(await system.spawn(ChainActor, f"c{i}"))

    for i in range(depth - 1):
        refs[i]._cell.actor.next_ref = refs[i + 1]

    for _ in range(n):
        await refs[0].ask("ping")

    await system.shutdown()

    profiler.disable()
    return profiler


def print_top_functions(profiler, sort_key="cumulative", limit=20):
    """Print top functions by specified sort key."""
    stats = pstats.Stats(profiler)
    stats.sort_stats(sort_key)

    print(f"\n=== Top {limit} functions by {sort_key} ===")
    stats.print_stats(limit)

    # Also print callers
    print(f"\n=== Who called those functions ===")
    stats.print_callers(limit)


def analyze_actor_specific(profiler):
    """Analyze actor-specific bottlenecks."""
    stats = pstats.Stats(profiler)

    print("\n=== Actor Framework Analysis ===")

    # Key functions to look for
    targets = [
        "enqueue",
        "put_nowait",
        "get",
        "_run",
        "on_receive",
        "tell",
        "ask",
        "send_reply",
        "register",
    ]

    found = {}
    for func, stat in stats.stats.items():
        fname = f"{func[0]}:{func[1]}:{func[2]}"
        for target in targets:
            if target in fname.lower():
                key = f"{func[2]} ({func[0]}:{func[1]})"
                found[key] = stat[3]  # cumulative time

    if found:
        sorted_found = sorted(found.items(), key=lambda x: x[1], reverse=True)
        print("\nActor-related functions (by cumulative time):")
        for name, time_val in sorted_found[:15]:
            print(f"  {time_val*1000:.2f}ms  {name}")
    else:
        print("No direct matches found, check top functions above.")


async def main():
    print("=" * 60)
    print("  Actor Framework Profiler")
    print("=" * 60)

    # Profile tell
    print("\n[1] Profiling tell (100K messages)...")
    profiler = await bench_tell_profiled(100_000)
    print_top_functions(profiler, "cumulative", 15)
    analyze_actor_specific(profiler)

    # Profile ask
    print("\n\n[2] Profiling ask (50K messages)...")
    profiler = await bench_ask_profiled(50_000)
    print_top_functions(profiler, "cumulative", 15)
    analyze_actor_specific(profiler)

    # Profile chain
    print("\n\n[3] Profiling actor chain (10K messages, 10 actors)...")
    profiler = await bench_actor_with_chain(10_000, 10)
    print_top_functions(profiler, "cumulative", 15)
    analyze_actor_specific(profiler)

    print("\n" + "=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
