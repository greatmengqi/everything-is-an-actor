# everything-is-an-actor

Asyncio-native Actor framework for Python agent systems — supervision trees, event streaming, composable orchestration, and pluggable mailbox.

Inspired by Erlang/Akka. Built for AI agent orchestration.

**[Documentation](https://greatmengqi.github.io/everything-is-an-actor/)** · [Getting Started](https://greatmengqi.github.io/everything-is-an-actor/getting-started/) · [Agent Layer](https://greatmengqi.github.io/everything-is-an-actor/agents/) · [Flow API](https://greatmengqi.github.io/everything-is-an-actor/flow/) · [MOA](https://greatmengqi.github.io/everything-is-an-actor/moa/) · [API Reference](https://greatmengqi.github.io/everything-is-an-actor/api/agent-actor/)

## Install

```bash
pip install everything-is-an-actor

# With Redis mailbox support
pip install everything-is-an-actor[redis]
```

## Agent Quick Start

```python
import asyncio
from everything_is_an_actor import ActorSystem
from everything_is_an_actor.agents import AgentSystem, AgentActor, Task

class SummaryAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        await self.emit_progress("summarizing...")
        return f"Summary: {input[:80]}..."

async def main():
    system = AgentSystem(ActorSystem("app"))

    # Stream every event from the entire agent tree
    async for event in system.run(SummaryAgent, "Long document..."):
        print(event.type, event.agent_path, event.data)

asyncio.run(main())
```

## Agent Layer

### `AgentActor` — implement `execute()`, not `on_receive()`

```python
class ResearchAgent(AgentActor[str, list]):
    async def on_started(self):
        self.client = aiohttp.ClientSession()

    async def execute(self, query: str) -> list:
        await self.emit_progress("searching...")
        results = await self.client.get(f"/search?q={query}")
        return results

    async def on_stopped(self):
        await self.client.close()
```

### Streaming output via `yield`

Each `yield` inside `execute()` emits a `task_chunk` event immediately — ideal for LLM tokens or file chunks.

```python
class LLMAgent(AgentActor[str, list]):
    async def execute(self, prompt: str):
        async for token in openai.stream(prompt):
            yield token   # → task_chunk event
```

### Orchestration

Six concurrency primitives for composing child agents — all ephemeral actors are cleaned up automatically.

```python
class OrchestratorAgent(AgentActor[str, Any]):
    async def execute(self, query: str):

        # ask — single child, one result
        r = await self.context.ask(SearchAgent, Task(input=query))
        result = r.output

        # sequence — fan-out, results in order, fail-fast
        a, b = await self.context.sequence([
            (SearchAgent, Task(input=query)),
            (FactCheckAgent, Task(input=query)),
        ])
        combined = {"search": a.output, "facts": b.output}

        # traverse — map a list through one agent
        summaries = await self.context.traverse(["doc1", "doc2", "doc3"], SummaryAgent)
        texts = [r.output for r in summaries]

        # race — first wins, cancel the rest
        fastest = await self.context.race([
            (FastAgent, Task(input=query)),
            (SlowAgent, Task(input=query)),
        ])
        winner = fastest.output

        # zip — two tasks, typed pair
        search, facts = await self.context.zip(
            (SearchAgent, Task(input=query)),
            (FactCheckAgent, Task(input=query)),
        )

        # stream — forward child chunks upstream
        async for item in self.context.stream(LLMAgent, Task(input=query)):
            match item:
                case StreamEvent(event=e) if e.type == "task_chunk":
                    yield e.data
                case StreamResult():
                    pass
```

### Event streaming

```python
system = AgentSystem(ActorSystem("app"))

# Stream all events from a fresh agent run
async for event in system.run(OrchestratorAgent, user_query):
    if event.type == "task_progress":
        print(event.data)

# Or stream from an existing ref
ref = await system.spawn(SummaryAgent, "summarizer")
async for item in ref.ask_stream(Task(input="document...")):
    match item:
        case StreamEvent(event=e):
            print(e.type, e.data)
        case StreamResult(result=r):
            print(r.output)
```

### Span linking

Every `TaskEvent` carries `parent_task_id` and `parent_agent_path` — reconstruct the full call tree from a flat event stream (OpenTelemetry-style spans).

---

## Flow API

Composable agent orchestration as data. `Flow[I, O]` is an ADT — build workflows with categorical combinators, execute against the actor runtime.

```python
from everything_is_an_actor.flow import agent, pure, race, at_least

# Declarative pipeline — data, not execution
pipeline = (
    agent(Researcher)
    .zip(agent(Analyst))
    .map(merge)
    .flat_map(agent(Writer))
    .recover_with(agent(Fallback))
)

# Execute
system = AgentSystem(ActorSystem())
result = await system.run_flow(pipeline, "quantum computing")
```

**Combinators:** `map`, `flat_map`, `zip`, `zip_all`, `race`, `branch`, `branch_on`, `recover`, `recover_with`, `fallback_to`, `divert_to`, `loop`, `loop_with_state`, `filter`, `and_then`

**Quorum parallelism:**

```python
from everything_is_an_actor.flow import at_least

# Run 3 agents, succeed if at least 2 return
quorum = at_least(2, agent(A), agent(B), agent(C))
# → QuorumResult(succeeded=(...), failed=(...))
```

**Serialization & visualization:**

```python
from everything_is_an_actor.flow import to_dict, from_dict, to_mermaid

data = to_dict(pipeline)          # JSON-compatible dict
restored = from_dict(data, registry)  # round-trip
print(to_mermaid(pipeline))       # Mermaid diagram
```

---

## Mixture of Agents (MOA)

Layer-by-layer pipeline built on Flow: parallel proposers → quorum validation → aggregation, with inter-layer directive passing.

```python
from everything_is_an_actor.agents import AgentActor
from everything_is_an_actor.moa import MoASystem, moa_layer, moa_tree
from everything_is_an_actor.flow.quorum import QuorumResult

class Researcher(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return f"Research: {input}"

class Critic(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return f"Critique: {input}"

class Synthesizer(AgentActor[QuorumResult[str], str]):
    async def execute(self, results: QuorumResult[str]) -> str:
        return "\n".join(results.succeeded)

system = MoASystem()
result = await system.run(
    moa_tree([
        moa_layer(
            proposers=[Researcher, Critic],
            aggregator=Synthesizer,
            min_success=1,
            timeout=10.0,
        ),
    ]),
    input="quantum computing",
)
await system.shutdown()
```

**Features:**
- **Validated fault-tolerance** — failed proposers are recovered into `QuorumResult.failed`, not fail-fast
- **Layer directives** — aggregator can return `LayerOutput(result, directive)` to steer next layer
- **Three API levels** — `MoASystem` (zero-config) → `AgentSystem.run_flow` (Flow composition) → raw `ActorSystem` (full control)
- **Full observability** — every proposer/aggregator emits `TaskEvent` via the existing event stream

---

## Virtual Actor

Virtual actors activate on demand and deactivate when idle — no manual lifecycle management.

```python
from everything_is_an_actor import Actor, ActorSystem, AfterIdle
from everything_is_an_actor import VirtualActorRegistry

class ChatAgent(Actor):
    def stop_policy(self):
        return AfterIdle(seconds=300)  # deactivate after 5 min idle

    async def on_started(self):                          # = on_activate
        self.history = await db.load(self.context.self_ref.name)

    async def on_receive(self, message):
        self.history.append(message)
        return respond(self.history)

    async def on_stopped(self):                          # = on_deactivate
        await db.save(self.context.self_ref.name, self.history)

async def main():
    system = ActorSystem("app")
    registry = VirtualActorRegistry(system)

    # Actor activates on first message, deactivates on idle
    reply = await registry.ask(ChatAgent, "session_123", "hello")
    reply = await registry.ask(ChatAgent, "session_123", "how are you")

    # Different sessions are independent actors
    await registry.tell(ChatAgent, "session_456", "hi")

    await system.shutdown()
```

### Pluggable registry store

Default is in-memory. For persistence across restarts, supply a custom backend:

```python
from everything_is_an_actor import RegistryStore

class RedisRegistryStore(RegistryStore):
    async def put(self, key):   await redis.sadd("actors", key)
    async def delete(self, key): await redis.srem("actors", key)
    async def list_all(self):   return list(await redis.smembers("actors"))

registry = VirtualActorRegistry(system, store=RedisRegistryStore())
```

---

## Core Actor API

| Class | Description |
|-------|-------------|
| `Actor` | Base class. Override `on_receive`, `on_started`, `on_stopped`, `on_restart`, `stop_policy` |
| `ActorRef` | Lightweight handle. `tell(msg)` / `ask(msg)` / `ask_stream(task)` / `free_ask(msg)` / `free_tell(msg)` / `free_stop()` |
| `ActorSystem` | Container. `spawn(cls, name)` / `get_actor(path)` / `ask(path, msg)` / `shutdown()` |
| `AgentSystem` | Agent facade over ActorSystem. `run(cls, input)` / `run_flow(flow, input)` / `abort(run_id)` |
| `VirtualActorRegistry` | On-demand activation / idle deactivation. `ask` / `tell` / `ask_stream` / `deactivate` / `deactivate_all` / `is_active` / `known_ids` |
| `RegistryStore` | Pluggable backend for virtual actor registry. Default in-memory, extensible to Redis/DB |
| `Mailbox` | Interface. `MemoryMailbox` (default), `FastMailbox`, `ThreadedMailbox`, or `RedisMailbox` |
| `Middleware` | Interceptor chain for all lifecycle events |
| `OneForOneStrategy` | Restart only the failing child |
| `AllForOneStrategy` | Restart all siblings when one fails |
| `StopPolicy` | ADT: `StopMode.NEVER` / `StopMode.ONE_TIME` / `AfterMessage(msg)` / `AfterIdle(seconds)` |

## Stop Policy

Actors support declarative lifecycle management via `stop_policy`:

```python
class OneTimeWorker(Actor):
    def stop_policy(self) -> StopPolicy:
        return StopMode.ONE_TIME  # Auto-stop after one message

    async def on_receive(self, task):
        return process(task)
        # Actor stops automatically after this

class IdleCache(Actor):
    def stop_policy(self) -> StopPolicy:
        return AfterIdle(seconds=60.0)  # Auto-stop after 60s idle

    async def on_receive(self, message):
        return self.cache.get(message)
```

**`tell()` type constraint**: Spawning a temporary actor via `tell()` requires a non-NEVER policy to prevent actor leaks.

```python
await self.tell(EchoActor, "hello")  # OK if EchoActor has ONE_TIME/AfterMessage/AfterIdle
# Raises TypeError if EchoActor has NEVER policy
```

## Path-Based Lookup

Actors can be addressed by path for distributed systems:

```python
# Get actor ref by path
ref = await system.get_actor("/app/workers/collector")
result = await ref.ask("status")

# Shorthand for get_actor + ask
result = await system.ask("/app/workers/collector", "status")
```

## Free Monad API

For composable actor workflows, use `ref.free_xxx()`:

```python
def workflow(ref: ActorRef):
    return (
        ref.free_ask("hello")
        .map(lambda r: r.upper())
        .flatMap(lambda r: ref.free_tell(r))
        .flatMap(lambda _: ref.free_stop())
    )

result = await system.run_free(workflow(worker_ref))
```

Available: `free_ask(msg)`, `free_tell(msg)`, `free_stop()`

## Supervision

```python
class ParentActor(Actor):
    def supervisor_strategy(self):
        return OneForOneStrategy(max_restarts=3, within_seconds=60)

    async def on_started(self):
        self.child = await self.context.spawn(WorkerActor, "worker")
```

Directives: `resume` | `restart` | `stop` | `escalate`

## Middleware

```python
class LogMiddleware(Middleware):
    async def on_receive(self, ctx, message, next_fn):
        print(f"[{ctx.actor_ref.path}] <- {message}")
        result = await next_fn(ctx, message)
        print(f"[{ctx.actor_ref.path}] -> {result}")
        return result

ref = await system.spawn(MyActor, "worker", middlewares=[LogMiddleware()])
```

## Redis Mailbox

```python
from everything_is_an_actor.plugins.redis import RedisMailbox

ref = await system.spawn(
    MyActor, "worker",
    mailbox=RedisMailbox(pool, "actor:inbox:worker", maxlen=1000),
)
```

## Benchmarks

Apple M-series, Python 3.12, asyncio:

**Actor core**

| Metric | Value |
|--------|-------|
| `tell` throughput | 945K msg/s |
| `ask` throughput | 29K msg/s |
| `ask` latency p50 | 32 us |
| `ask` latency p99 | 46 us |
| 100-hop actor chain | 2.0 ms, 20 us/hop |
| 1000 actors x 100 msgs | 879K msg/s, 0 loss |
| Middleware overhead | +0% (~1 middleware) |
| Spawn 5000 actors | 27 ms |

**Agent layer**

| Metric | Value |
|--------|-------|
| `AgentActor` ask throughput | 27K tasks/s |
| `AgentActor` ask latency p50 | 36 us |
| `AgentActor` ask latency p99 | 50 us |
| `sequence(50)` fan-out | 32K child tasks/s |
| `traverse(100)` map | 28K items/s |
| `ask_stream` chunk throughput | 227K chunks/s |
| `AgentSystem.run()` latency p50 | 0.2 ms (spawn+run+stream) |

## License

MIT
