# everything-is-an-actor

**Asyncio-native Actor framework for Python agent systems.**

Inspired by Erlang/Akka. Built for AI agent orchestration.

---

## Why everything-is-an-actor?

Multi-agent AI systems have a concurrency problem. When a lead agent delegates to multiple workers, you need:

- **Structured concurrency** — workers run in parallel without manual thread management
- **Fault isolation** — a failed worker shouldn't crash the orchestrator
- **Task lifecycle** — every unit of work has a status: pending → running → completed / failed
- **Event streaming** — consumers subscribe to what agents produce, in real time

The actor model solves all of these. `everything-is-an-actor` brings it to Python asyncio with two layers:

| Layer | What it provides |
|-------|-----------------|
| **Core** (`everything_is_an_actor`) | Generic actor primitives: mailbox, supervision, middleware |
| **Agents** (`everything_is_an_actor.agents`) | AI-specific abstractions: `Task`, `AgentActor`, streaming events |

---

## Install

```bash
pip install everything-is-an-actor

# With Redis mailbox support
pip install everything-is-an-actor[redis]
```

---

## 30-second example

```python
import asyncio
from everything_is_an_actor.agents import AgentSystem, AgentActor, Task

class ResearchAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        await self.emit_progress("searching...")
        r = await self.context.ask(SummaryAgent, Task(input=input))
        return r.output

async def main():
    system = AgentSystem("app")

    # Stream every event from the entire agent tree
    async for event in system.run(ResearchAgent, "actor model"):
        print(event.type, event.agent_path, event.data)

asyncio.run(main())
```

---

## Key features

**Actor core**

- `tell` (fire-and-forget) + `ask` (request-reply) messaging
- `MemoryMailbox` — 945K msg/s throughput
- `RedisMailbox` — persistent, survives process restarts
- `OneForOneStrategy` / `AllForOneStrategy` supervision
- Middleware pipeline for all lifecycle events
- **Stop Policy ADT** — declarative lifecycle: `StopMode.NEVER` / `StopMode.ONE_TIME` / `AfterMessage(msg)` / `AfterIdle(seconds)`
- **Path-based lookup** — address actors by path: `system.get_actor("/app/workers/collector")`
- **Free Monad API** — composable workflows: `ref.free_ask()` / `ref.free_tell()` / `ref.free_stop()`

**Agent layer**

- `Task` / `TaskResult` / `TaskEvent` — first-class task lifecycle
- `AgentActor` — implement `execute()`, not `on_receive()`
- `emit_progress()` — status/progress updates during execution
- Streaming `execute()` — `yield` tokens/chunks as an async generator; emits `task_chunk` events
- Progressive API: plain classes → full actor control (5 levels)

**Orchestration**

- `ask(AgentCls, message)` — spawn ephemeral child, send once, await result
- `sequence([(A, msg), (B, msg)])` — fan-out with fail-fast sibling cancellation; results in order
- `traverse(inputs, AgentCls)` — map a list through one agent concurrently
- `race([(A, msg), (B, msg)])` — first-wins, cancel the rest
- `zip((A, msg), (B, msg))` — two tasks, typed pair
- `stream(AgentCls, message)` — streaming counterpart; forward child chunks upstream

**Event streaming**

- `AgentSystem.run(AgentCls, input)` — spawn root agent, stream all `TaskEvent`s from the entire actor tree
- `ref.ask_stream(Task(...))` — stream events from an existing ref; `StreamItem` ADT (`StreamEvent | StreamResult`) for `match/case`
- `TaskEvent.parent_task_id` + `parent_agent_path` — OpenTelemetry-style span linking; reconstruct full call tree from flat event stream

---

## Benchmarks

Apple M-series, Python 3.12, asyncio:

**Actor core**

| Metric | Value |
|--------|-------|
| `tell` throughput | 945K msg/s |
| `ask` throughput | 29K msg/s |
| `ask` latency p50 | 32 µs |
| `ask` latency p99 | 46 µs |
| 1000 actors × 100 msgs | 879K msg/s, 0 loss |
| Spawn 5000 actors | 27 ms |

**Agent layer**

| Metric | Value |
|--------|-------|
| `AgentActor` ask throughput | 27K tasks/s |
| `AgentActor` ask latency p50 | 36 µs |
| `sequence(50)` fan-out | 32K child tasks/s |
| `ask_stream` chunk throughput | 227K chunks/s |
| `AgentSystem.run()` latency p50 | 0.2 ms |
