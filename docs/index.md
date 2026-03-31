# actor-for-agents

**Asyncio-native Actor framework for Python agent systems.**

Inspired by Erlang/Akka. Built for AI agent orchestration.

---

## Why actor-for-agents?

Multi-agent AI systems have a concurrency problem. When a lead agent delegates to multiple workers, you need:

- **Structured concurrency** — workers run in parallel without manual thread management
- **Fault isolation** — a failed worker shouldn't crash the orchestrator
- **Task lifecycle** — every unit of work has a status: pending → running → completed / failed
- **Event streaming** — consumers subscribe to what agents produce, in real time

The actor model solves all of these. `actor-for-agents` brings it to Python asyncio with two layers:

| Layer | What it provides |
|-------|-----------------|
| **Core** (`actor_for_agents`) | Generic actor primitives: mailbox, supervision, middleware |
| **Agents** (`actor_for_agents.agents`) | AI-specific abstractions: `Task`, `AgentActor`, streaming events |

---

## Install

```bash
pip install actor-for-agents

# With Redis mailbox support
pip install actor-for-agents[redis]
```

---

## 30-second example

```python
import asyncio
from actor_for_agents.agents import AgentSystem, AgentActor, Task

class ResearchAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        await self.emit_progress("searching...")
        result = await self.context.dispatch(SummaryAgent, Task(input=input))
        return result

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
- `MemoryMailbox` — 861K msg/s throughput
- `RedisMailbox` — persistent, survives process restarts
- `OneForOneStrategy` / `AllForOneStrategy` supervision
- Middleware pipeline for all lifecycle events

**Agent layer**

- `Task` / `TaskResult` / `TaskEvent` — first-class task lifecycle
- `AgentActor` — implement `execute()`, not `on_receive()`
- `emit_progress()` — status/progress updates during execution
- Streaming `execute()` — `yield` tokens/chunks as an async generator; emits `task_chunk` events
- Progressive API: plain classes → full actor control (5 levels)

**Orchestration**

- `dispatch(AgentCls, message)` — spawn ephemeral child, send once, await result
- `dispatch_parallel([(A, msg), (B, msg)])` — fan-out with fail-fast sibling cancellation
- `dispatch_stream(AgentCls, message)` — streaming counterpart; forward child chunks upstream

**Event streaming**

- `AgentSystem.run(AgentCls, input)` — spawn root agent, stream all `TaskEvent`s from the entire actor tree
- `ref.ask_stream(Task(...))` — stream events from an existing ref; `StreamItem` ADT (`StreamEvent | StreamResult`) for `match/case`
- `TaskEvent.parent_task_id` + `parent_agent_path` — OpenTelemetry-style span linking; reconstruct full call tree from flat event stream

---

## Benchmarks

Apple M-series, Python 3.12:

| Metric | Value |
|--------|-------|
| `tell` throughput | 861K msg/s |
| `ask` throughput | 20K msg/s |
| `ask` latency p50 | 40 µs |
| Spawn 5000 actors | 32 ms |
| Middleware overhead | +6.3% |
