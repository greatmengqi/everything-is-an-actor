# actor-for-agents

Asyncio-native Actor framework for Python agent systems — supervision trees, event streaming, and pluggable mailbox.

Inspired by Erlang/Akka. Built for AI agent orchestration.

**[Documentation](https://greatmengqi.github.io/actor-for-agents/)** · [Getting Started](https://greatmengqi.github.io/actor-for-agents/getting-started/) · [Agent Layer](https://greatmengqi.github.io/actor-for-agents/agents/) · [API Reference](https://greatmengqi.github.io/actor-for-agents/api/agent-actor/)

## Install

```bash
pip install actor-for-agents

# With Redis mailbox support
pip install actor-for-agents[redis]
```

## Agent Quick Start

```python
import asyncio
from actor_for_agents.agents import AgentSystem, AgentActor, Task

class SummaryAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        await self.emit_progress("summarizing...")
        return f"Summary: {input[:80]}..."

async def main():
    system = AgentSystem("app")

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
system = AgentSystem("app")

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

## Core Actor API

| Class | Description |
|-------|-------------|
| `Actor` | Base class. Override `on_receive`, `on_started`, `on_stopped`, `on_restart` |
| `ActorRef` | Lightweight handle. `tell(msg)` / `ask(msg)` / `ask_stream(task)` |
| `ActorSystem` | Container. `spawn(cls, name)` / `shutdown()` |
| `AgentSystem` | Drop-in replacement with event streaming. `run(cls, input)` / `abort(run_id)` |
| `Mailbox` | Interface. `MemoryMailbox` (default) or `RedisMailbox` |
| `Middleware` | Interceptor chain for all lifecycle events |
| `OneForOneStrategy` | Restart only the failing child |
| `AllForOneStrategy` | Restart all siblings when one fails |

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
        print(f"[{ctx.recipient.path}] ← {message}")
        result = await next_fn(ctx, message)
        print(f"[{ctx.recipient.path}] → {result}")
        return result

ref = await system.spawn(MyActor, "worker", middlewares=[LogMiddleware()])
```

## Redis Mailbox

```python
from actor_for_agents.plugins.redis import RedisMailbox

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
| `ask` latency p50 | 32 µs |
| `ask` latency p99 | 46 µs |
| 100-hop actor chain | 2.0 ms, 20 µs/hop |
| 1000 actors × 100 msgs | 879K msg/s, 0 loss |
| Middleware overhead | +0% (~1 middleware) |
| Spawn 5000 actors | 27 ms |

**Agent layer**

| Metric | Value |
|--------|-------|
| `AgentActor` ask throughput | 27K tasks/s |
| `AgentActor` ask latency p50 | 36 µs |
| `AgentActor` ask latency p99 | 50 µs |
| `sequence(50)` fan-out | 32K child tasks/s |
| `traverse(100)` map | 28K items/s |
| `ask_stream` chunk throughput | 227K chunks/s |
| `AgentSystem.run()` latency p50 | 0.2 ms (spawn+run+stream) |

## License

MIT
