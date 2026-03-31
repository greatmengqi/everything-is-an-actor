"""RedisMailbox benchmark: throughput, latency, concurrency, backpressure."""

import asyncio
import time

import redis.asyncio as redis

from actor_for_agents import Actor, ActorSystem
from actor_for_agents.mailbox_redis import RedisMailbox


class EchoActor(Actor):
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


async def _redis_client():
    client = redis.Redis(host="127.0.0.1", port=6379, decode_responses=False)
    await client.ping()
    return client


async def bench_redis_ask_throughput(n=20_000):
    client = await _redis_client()

    queue = "deerflow:bench:redis:ask"
    await client.delete(queue)

    mailbox = RedisMailbox(client.connection_pool, queue, brpop_timeout=0.05)
    system = ActorSystem("bench-redis")
    ref = await system.spawn(EchoActor, "echo", mailbox=mailbox)

    start = time.perf_counter()
    for _ in range(n):
        await ref.ask("ping", timeout=5.0)
    elapsed = time.perf_counter() - start

    await system.shutdown()

    rate = n / elapsed
    print(f"  redis ask throughput:  {fmt(n)} msgs in {elapsed:.2f}s = {fmt(int(rate))}/s")


async def bench_redis_tell_throughput(n=50_000):
    client = await _redis_client()

    queue = "deerflow:bench:redis:tell"
    await client.delete(queue)

    mailbox = RedisMailbox(client.connection_pool, queue, brpop_timeout=0.05)
    system = ActorSystem("bench-redis")
    ref = await system.spawn(CounterActor, "counter", mailbox=mailbox)

    start = time.perf_counter()
    for _ in range(n):
        await ref.tell("inc")
    count = await ref.ask("get", timeout=30.0)
    elapsed = time.perf_counter() - start

    await system.shutdown()

    rate = n / elapsed
    loss = n - count
    print(f"  redis tell throughput: {fmt(n)} msgs in {elapsed:.2f}s = {fmt(int(rate))}/s (loss: {loss})")


async def bench_redis_ask_latency(n=5_000):
    client = await _redis_client()

    queue = "deerflow:bench:redis:latency"
    await client.delete(queue)

    mailbox = RedisMailbox(client.connection_pool, queue, brpop_timeout=0.05)
    system = ActorSystem("bench-redis")
    ref = await system.spawn(EchoActor, "echo", mailbox=mailbox)

    for _ in range(100):
        await ref.ask("warmup", timeout=5.0)

    latencies = []
    for _ in range(n):
        t0 = time.perf_counter()
        await ref.ask("ping", timeout=5.0)
        latencies.append((time.perf_counter() - t0) * 1_000_000)

    await system.shutdown()

    latencies.sort()
    p50 = latencies[len(latencies) // 2]
    p99 = latencies[int(len(latencies) * 0.99)]
    p999 = latencies[int(len(latencies) * 0.999)]
    print(f"  redis ask latency:     p50={p50:.0f}µs  p99={p99:.0f}µs  p99.9={p999:.0f}µs")


async def bench_redis_concurrent_actors(num_actors=200, msgs_per_actor=100):
    client = await _redis_client()
    system = ActorSystem("bench-redis")
    refs = []

    for i in range(num_actors):
        q = f"deerflow:bench:redis:conc:{i}"
        await client.delete(q)
        mailbox = RedisMailbox(client.connection_pool, q, brpop_timeout=0.05)
        refs.append(await system.spawn(CounterActor, f"a{i}", mailbox=mailbox))

    start = time.perf_counter()

    async def send_batch(ref, n):
        for i in range(n):
            await ref.tell("inc")
            if i % 50 == 49:
                await asyncio.sleep(0)
        return await ref.ask("get", timeout=30.0)

    results = await asyncio.gather(*[send_batch(r, msgs_per_actor) for r in refs])
    elapsed = time.perf_counter() - start

    total = num_actors * msgs_per_actor
    delivered = sum(results)
    rate = total / elapsed
    loss = total - delivered
    print(
        f"  redis concurrency:     {num_actors} actors × {msgs_per_actor} msgs = {fmt(total)} in {elapsed:.2f}s = {fmt(int(rate))}/s (loss: {loss})"
    )

    await system.shutdown()


async def bench_redis_maxlen_backpressure(total_messages=20_000, maxlen=100, ask_timeout=0.01, ask_concurrency=200):
    client = await _redis_client()

    queue_tell = "deerflow:bench:redis:bp:tell"
    await client.delete(queue_tell)
    mailbox_tell = RedisMailbox(client.connection_pool, queue_tell, maxlen=maxlen, brpop_timeout=0.05)

    system_tell = ActorSystem("bench-redis-bp-tell")
    ref_tell = await system_tell.spawn(CounterActor, "counter", mailbox=mailbox_tell)

    # Saturate with tell: dropped messages become dead letters
    for _ in range(total_messages):
        await ref_tell.tell("inc")

    await asyncio.sleep(0.2)
    processed = await ref_tell.ask("get", timeout=10.0)
    dropped = len(system_tell.dead_letters)
    drop_rate = dropped / total_messages if total_messages else 0.0

    print(
        f"  redis maxlen tell:     maxlen={maxlen}, sent={fmt(total_messages)}, processed={fmt(processed)}, dropped={fmt(dropped)} ({drop_rate:.1%})"
    )

    await system_tell.shutdown()

    # Ask timeout rate under pressure
    queue_ask = "deerflow:bench:redis:bp:ask"
    await client.delete(queue_ask)
    mailbox_ask = RedisMailbox(client.connection_pool, queue_ask, maxlen=maxlen, brpop_timeout=0.05)

    system_ask = ActorSystem("bench-redis-bp-ask")
    ref_ask = await system_ask.spawn(EchoActor, "echo", mailbox=mailbox_ask)

    async def one_ask(i):
        try:
            await ref_ask.ask(i, timeout=ask_timeout)
            return True, None
        except asyncio.TimeoutError:
            return False, "timeout"
        except Exception:  # MailboxFullError or other rejection
            return False, "rejected"

    sem = asyncio.Semaphore(ask_concurrency)

    async def one_ask_limited(i):
        async with sem:
            return await one_ask(i)

    results = await asyncio.gather(*[one_ask_limited(i) for i in range(total_messages)])
    ok = sum(1 for r, _ in results if r)
    timeout_count = sum(1 for _, reason in results if reason == "timeout")
    rejected_count = sum(1 for _, reason in results if reason == "rejected")
    fail_rate = (total_messages - ok) / total_messages if total_messages else 0.0

    print(
        f"  redis maxlen ask:      maxlen={maxlen}, total={fmt(total_messages)}, ok={fmt(ok)}, "
        f"timeout={fmt(timeout_count)}, rejected={fmt(rejected_count)} (fail: {fail_rate:.1%}), "
        f"ask_timeout={ask_timeout}s, concurrency={ask_concurrency}"
    )

    await system_ask.shutdown()


async def bench_redis_put_batch(n=50_000, batch_size=100):
    """put_batch: N messages in N/batch_size round-trips instead of N."""
    client = await _redis_client()

    queue = "deerflow:bench:redis:batch"
    await client.delete(queue)

    mailbox = RedisMailbox(client.connection_pool, queue, brpop_timeout=0.05)
    system = ActorSystem("bench-redis-batch")
    ref = await system.spawn(CounterActor, "counter", mailbox=mailbox)

    from actor_for_agents.ref import _Envelope

    batches = [[_Envelope(payload="inc") for _ in range(batch_size)] for _ in range(n // batch_size)]

    t0 = time.perf_counter()
    for batch in batches:
        await mailbox.put_batch(batch)
    enqueue_elapsed = time.perf_counter() - t0

    count = await ref.ask("get", timeout=60.0)
    total_elapsed = time.perf_counter() - t0

    loss = n - count
    enqueue_rate = n / enqueue_elapsed
    print(
        f"  redis put_batch push:  {fmt(n)} msgs in {enqueue_elapsed:.3f}s = {fmt(int(enqueue_rate))}/s "
        f"(batch={batch_size}, round-trips={n // batch_size})"
    )
    print(
        f"  redis put_batch total: end-to-end {total_elapsed:.2f}s = {fmt(int(n / total_elapsed))}/s "
        f"(consume bottleneck, loss={loss})"
    )

    await system.shutdown()


async def main():
    print("=" * 72)
    print("  RedisMailbox Benchmarks")
    print("=" * 72)
    print()

    await bench_redis_tell_throughput()
    await bench_redis_ask_throughput()
    await bench_redis_ask_latency()
    await bench_redis_concurrent_actors()
    await bench_redis_put_batch()
    await bench_redis_maxlen_backpressure()

    print()
    print("=" * 72)
    print("  Done")
    print("=" * 72)


if __name__ == "__main__":
    asyncio.run(main())
