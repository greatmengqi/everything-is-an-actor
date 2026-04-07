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

The actor model solves all of these. `everything-is-an-actor` brings it to Python asyncio with five layers:

| Layer | What it provides |
|-------|-----------------|
| **Core** (`everything_is_an_actor.core`) | Generic actor primitives: mailbox, supervision, middleware |
| **Agents** (`everything_is_an_actor.agents`) | AI-specific abstractions: `Task`, `AgentActor`, streaming events |
| **Flow** (`everything_is_an_actor.flow`) | Composable orchestration ADT: categorical combinators, serialization, visualization |
| **MOA** (`everything_is_an_actor.moa`) | Mixture-of-Agents pattern: parallel proposers → quorum → aggregation |
| **Integrations** (`everything_is_an_actor.integrations`) | External framework adapters (LangChain) |

The project also introduces an original `Flow` model for agent orchestration: a typed `Flow[I, O]` semantic core that can be represented equivalently in Python, `YAML`, and `JSON`, while graph remains a derived view for visualization and execution inspection. See the [Flow API guide](flow.md) for usage and the [Flow DSL vs Graph analysis](superpowers/specs/2026-04-07-flow-vs-graph-analysis.md) for the semantic rationale.

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
from everything_is_an_actor import ActorSystem
from everything_is_an_actor.agents import AgentSystem, AgentActor, Task

class ResearchAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        await self.emit_progress("searching...")
        r = await self.context.ask(SummaryAgent, Task(input=input))
        return r.output

async def main():
    system = AgentSystem(ActorSystem("app"))

    # Stream every event from the entire agent tree
    async for event in system.run(ResearchAgent, "actor model"):
        print(event.type, event.agent_path, event.data)

asyncio.run(main())
```

---

## Key features

**Actor core**

- `tell` (fire-and-forget) + `ask` (request-reply) messaging
- `MemoryMailbox` / `FastMailbox` — configurable message queue backend
- `RedisMailbox` — persistent, survives process restarts
- `OneForOneStrategy` / `AllForOneStrategy` supervision
- Middleware pipeline for all lifecycle events
- **Virtual actors** — `VirtualActorRegistry` activates on first message and deactivates on idle; supports `ask`/`tell`/`ask_stream`, manual `deactivate`, and pluggable `RegistryStore`
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

**Flow API** — composable agent orchestration

- `Flow[I, O]` ADT — syntax tree for workflows, data not execution
- Categorical combinators: `map`, `flat_map`, `zip`, `race`, `branch`, `recover`, `fallback_to`, `divert_to`, `loop`
- `at_least(k, *flows)` — quorum parallelism ("N-way, at least K succeed")
- `to_dict` / `from_dict` — Flow serialization for persistence and transfer
- `to_mermaid` — automatic Mermaid diagram generation
- `FlowSystem` / `AgentSystem.run_flow()` — interpreter execution

**MOA** — Mixture-of-Agents pattern

- `moa_layer(proposers, aggregator, min_success)` — single MOA layer
- `moa_tree([layers])` — multi-layer pipeline with directive passing
- `MoASystem` — high-level entry point, zero boilerplate
- `LayerOutput` — inter-layer directive communication

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
