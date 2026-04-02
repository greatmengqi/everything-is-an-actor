# Getting Started

## Installation

```bash
pip install everything-is-an-actor
```

For Redis mailbox support:

```bash
pip install everything-is-an-actor[redis]
```

Requirements: Python 3.12+

---

## Core concepts

### Actor

An actor is an isolated unit of computation with its own mailbox. Actors communicate only through messages — no shared state, no locks.

```python
from everything_is_an_actor import Actor, ActorSystem

class CounterActor(Actor):
    def __init__(self):
        self.count = 0

    async def on_receive(self, message):
        if message == "inc":
            self.count += 1
        elif message == "get":
            return self.count
```

### ActorSystem

The system is the container that spawns and manages actors.

```python
async def main():
    system = ActorSystem("app")

    ref = await system.spawn(CounterActor, "counter")

    await ref.tell("inc")          # fire-and-forget
    await ref.tell("inc")
    count = await ref.ask("get")   # request-reply
    print(count)  # 2

    await system.shutdown()
```

### tell vs ask

| | `tell(msg)` | `ask(msg, timeout)` |
|--|--|--|
| Blocking | No | Yes (awaits reply) |
| Return value | None | The actor's return value |
| Use case | Events, notifications | Queries, computations |

---

## Supervision

When a child actor fails, the parent's supervisor strategy decides what happens.

```python
from everything_is_an_actor import Actor, ActorSystem, OneForOneStrategy

class WorkerActor(Actor):
    async def on_receive(self, message):
        if message == "fail":
            raise RuntimeError("simulated crash")
        return f"ok:{message}"

class SupervisorActor(Actor):
    def supervisor_strategy(self):
        # Restart only the failing child, up to 3 times per minute
        return OneForOneStrategy(max_restarts=3, within_seconds=60)

    async def on_started(self):
        self.worker = await self.context.spawn(WorkerActor, "worker")

    async def on_receive(self, message):
        return await self.worker.ask(message, timeout=2.0)
```

Directives:

| Directive | Behavior |
|-----------|----------|
| `restart` | Stop the crashed actor, create a fresh instance |
| `resume` | Ignore the error, continue processing |
| `stop` | Permanently stop the actor |
| `escalate` | Propagate the failure to the grandparent |

---

## Middleware

Middleware intercepts every message and lifecycle event.

```python
from everything_is_an_actor import Middleware

class LogMiddleware(Middleware):
    async def on_receive(self, ctx, message, next_fn):
        print(f"[{ctx.recipient.path}] ← {message}")
        result = await next_fn(ctx, message)
        print(f"[{ctx.recipient.path}] → {result}")
        return result

system = ActorSystem("app")
ref = await system.spawn(MyActor, "worker", middlewares=[LogMiddleware()])
```

---

## Redis mailbox

Persist messages across process restarts:

```python
import redis.asyncio as redis
from everything_is_an_actor import ActorSystem
from everything_is_an_actor.plugins.redis import RedisMailbox

pool = redis.ConnectionPool.from_url("redis://localhost:6379")

system = ActorSystem("app")
ref = await system.spawn(
    MyActor, "worker",
    mailbox=RedisMailbox(pool, "actor:inbox:worker", maxlen=1000),
)
```

!!! note
    Redis mailbox requires JSON-serializable messages.

---

## Next steps

- [Agent Layer](agents.md) — higher-level abstractions for AI agents
- [API Reference](api/agent-actor.md) — full API documentation
