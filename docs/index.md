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
from actor_for_agents import ActorSystem
from actor_for_agents.agents import AgentActor, Task, TaskResult

class SummaryAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        await self.emit_progress("processing...")
        return f"Summary: {input[:50]}..."

async def main():
    system = ActorSystem("app")
    ref = await system.spawn(SummaryAgent, "summarizer")

    result: TaskResult[str] = await ref.ask(Task(input="Long document content here..."))
    print(result.output)  # Summary: Long document content here...

    await system.shutdown()

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
- `emit_progress()` — stream intermediate results
- Progressive API: plain classes → full actor control (5 levels)

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
