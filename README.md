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

```python
class OrchestratorAgent(AgentActor[str, dict]):
    async def execute(self, query: str) -> dict:
        # Fan-out to multiple agents concurrently
        results = await self.context.dispatch_parallel([
            (SearchAgent, Task(input=query)),
            (FactCheckAgent, Task(input=query)),
        ])
        return {"search": results[0], "facts": results[1]}
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

| Metric | Value |
|--------|-------|
| `tell` throughput | 861K msg/s |
| `ask` throughput | 20K msg/s |
| `ask` latency p50 | 40 µs |
| `ask` latency p99 | 262 µs |
| 1000 actors × 100 msgs | 790K msg/s, 0 loss |
| Middleware overhead | +6.3% (1 middleware) |
| Spawn 5000 actors | 32 ms |

## License

MIT
