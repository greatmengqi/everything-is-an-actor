# actor-for-agents

Asyncio-native Actor framework for Python agent systems — supervision trees, middleware pipeline, and pluggable mailbox.

Inspired by Erlang/Akka. Built for AI agent orchestration.

## Install

```bash
pip install actor-for-agents

# With Redis mailbox support
pip install actor-for-agents[redis]
```

## Quick Start

```python
import asyncio
from actor_for_agents import Actor, ActorSystem

class GreetActor(Actor):
    async def on_receive(self, message):
        return f"Hello, {message}!"

async def main():
    system = ActorSystem("demo")
    ref = await system.spawn(GreetActor, "greeter")

    result = await ref.ask("world")   # request-reply
    print(result)                      # Hello, world!

    await ref.tell("fire-and-forget")  # non-blocking

    await system.shutdown()

asyncio.run(main())
```

## Core API

| Class | Description |
|-------|-------------|
| `Actor` | Base class. Override `on_receive`, `on_started`, `on_stopped`, `on_restart` |
| `ActorRef` | Lightweight handle. `tell(msg)` / `ask(msg, timeout)` |
| `ActorSystem` | Container. `spawn(cls, name, mailbox, middlewares)` / `shutdown()` |
| `Mailbox` | Interface. `MemoryMailbox` (default) or `RedisMailbox` |
| `Middleware` | Interceptor chain for all lifecycle events |
| `OneForOneStrategy` | Restart only the failing child |
| `AllForOneStrategy` | Restart all siblings when one fails |

## Supervision

```python
from actor_for_agents import Actor, ActorSystem, OneForOneStrategy, Directive

class ParentActor(Actor):
    def supervisor_strategy(self):
        return OneForOneStrategy(max_restarts=3, within_seconds=60)

    async def on_started(self):
        self.child = await self.context.spawn(WorkerActor, "worker")
```

Directives: `resume` | `restart` | `stop` | `escalate`

## Middleware

```python
from actor_for_agents import Middleware

class LogMiddleware(Middleware):
    async def on_receive(self, ctx, message, next_fn):
        print(f"[{ctx.recipient.path}] ← {message}")
        result = await next_fn(ctx, message)
        print(f"[{ctx.recipient.path}] → {result}")
        return result

system = ActorSystem("app")
ref = await system.spawn(MyActor, "worker", middlewares=[LogMiddleware()])
```

## Redis Mailbox

```python
import redis.asyncio as redis
from actor_for_agents import ActorSystem
from actor_for_agents.mailbox_redis import RedisMailbox

pool = redis.ConnectionPool.from_url("redis://localhost:6379")

system = ActorSystem("app")
ref = await system.spawn(
    MyActor, "worker",
    mailbox=RedisMailbox(pool, "actor:inbox:worker", maxlen=1000),
)
```

Messages must be JSON-serializable. Survives process restarts.

## Retry

```python
from actor_for_agents import ask_with_retry

result = await ask_with_retry(
    ref, "fetch",
    max_attempts=3,
    base_delay=0.1,
    backoff=2.0,
)
```

## Benchmarks

Apple M-series, Python 3.12, asyncio:

### MemoryMailbox

| Metric | Value |
|--------|-------|
| `tell` throughput | 861K msg/s |
| `ask` throughput | 20K msg/s |
| `ask` latency p50 | 40 µs |
| `ask` latency p99 | 262 µs |
| 1000 actors × 100 msgs | 790K msg/s, 0 loss |
| Middleware overhead | +6.3% (1 middleware) |
| Spawn 5000 actors | 32 ms |

### RedisMailbox (localhost)

| Metric | Value |
|--------|-------|
| `tell` throughput | 2K msg/s |
| `ask` latency p50 | 526 µs |
| `put_batch` enqueue | 219K msg/s (batch=100) |
| 200 actors × 100 msgs | 11K msg/s, 0 loss |

## License

MIT
