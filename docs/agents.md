# Agent Layer

The `actor_for_agents.agents` module provides higher-level abstractions specifically for AI agent systems, built on top of the core actor primitives.

---

## The progressive API

The agent layer has 5 levels. Start at the lowest level you need — upgrading later requires no rewrites.

=== "Level 1 — plain class"

    Zero actor knowledge required. Just implement `execute()`.

    ```python
    class SearchAgent:
        async def execute(self, input: str) -> str:
            return await web_search(input)
    ```

    Works with `AgentSystem` (coming in M3).

=== "Level 2 — lifecycle hooks"

    Add initialization and cleanup.

    ```python
    class SearchAgent:
        async def on_started(self):
            self.client = aiohttp.ClientSession()

        async def execute(self, input: str) -> str:
            return await self.client.get(input)

        async def on_stopped(self):
            await self.client.close()
    ```

=== "Level 3 — actor config"

    Configure mailbox size and restart limits without inheriting from `AgentActor`.

    ```python
    from actor_for_agents.agents import ActorConfig

    class SearchAgent:
        __actor__ = ActorConfig(mailbox_size=64, max_restarts=5)

        async def execute(self, input: str) -> str:
            return await web_search(input)
    ```

=== "Level 4 — AgentActor"

    Full power: strong typing, supervision strategy, `emit_progress()`, access to actor context.

    ```python
    from actor_for_agents.agents import AgentActor

    class SearchAgent(AgentActor[str, str]):
        def supervisor_strategy(self):
            return OneForOneStrategy(max_restarts=5)

        async def on_started(self):
            self.client = aiohttp.ClientSession()

        async def execute(self, input: str) -> str:
            await self.emit_progress("searching...")
            result = await self.client.get(input)
            await self.emit_progress("done")
            return result

        async def on_stopped(self):
            await self.client.close()
    ```

=== "Level 5 — raw Actor"

    For infrastructure components (routers, caches, rate limiters) that don't follow the Task protocol.

    ```python
    from actor_for_agents import Actor

    class RateLimiterActor(Actor):
        async def on_receive(self, message):
            await self.throttle()
            return await self.forward(message)
    ```

---

## Task lifecycle

Every message to an `AgentActor` is a `Task`. The framework manages the lifecycle automatically.

```python
from actor_for_agents.agents import Task, TaskResult, TaskStatus

task: Task[str] = Task(input="what is the actor model?")
# task.id is auto-generated (uuid hex)

result: TaskResult[str] = await ref.ask(task)
print(result.output)        # the value execute() returned
print(result.status)        # TaskStatus.COMPLETED
print(result.task_id)       # same as task.id
```

### Status transitions

```
PENDING → RUNNING → COMPLETED
                 ↘ FAILED
```

| Status | When |
|--------|------|
| `COMPLETED` | `execute()` returned normally |
| `FAILED` | `execute()` raised an exception |

---

## Events

`AgentActor` emits `TaskEvent` objects automatically at each lifecycle stage:

| Event type | When emitted |
|------------|-------------|
| `task_started` | `execute()` begins |
| `task_progress` | You call `emit_progress(data)` |
| `task_completed` | `execute()` returns successfully |
| `task_failed` | `execute()` raises an exception (`data` contains the error message) |

```python
@dataclass
class TaskEvent:
    type: str         # one of the four types above
    task_id: str      # matches the Task.id
    agent_path: str   # e.g. "/app/summarizer"
    data: Any         # the progress data or final output
```

Events flow to a `RunStream` when using `AgentSystem.run()` or `ActorRef.ask_stream()`. With plain `ActorSystem`, events are silently dropped.

### Span linking fields

`TaskEvent` also carries two fields for distributed trace reconstruction:

| Field | Description |
|-------|-------------|
| `parent_task_id` | `task_id` of the calling agent's task. `None` for the root agent. |
| `parent_agent_path` | Actor path of the parent agent. `None` for the root agent. |

These let you reconstruct the full call tree from a flat event stream (OpenTelemetry-style spans).

---

## Streaming output from execute()

`execute()` supports two output modes:

=== "Single result — `return await`"

    The agent computes one value and returns it. Use when the consumer only needs the final result.

    ```python
    class GeoAgent(AgentActor[str, dict]):
        async def execute(self, input: str) -> dict:
            return await self.client.get(f"/geocode?q={input}")
    ```

    Result: `TaskResult.output` is a `dict`.

=== "Stream — `yield`"

    The agent is an async generator. Each `yield` emits a `task_chunk` event immediately.
    Use when the consumer should process values as they arrive (LLM tokens, file chunks, etc.).

    ```python
    class LLMAgent(AgentActor[str, list]):
        async def execute(self, prompt: str):
            async for token in openai.stream(prompt):
                yield token          # → task_chunk event, data=token
    ```

    Result: `TaskResult.output` is `list[token]` (all yielded values collected).

**When to use which:**

| | `return await` | `yield` |
|--|--|--|
| Consumer needs | Final result only | Values as they arrive |
| `TaskResult.output` | The return value | `list` of all yielded values |
| Event emitted | `task_completed` | `task_chunk` × N, then `task_completed` |
| Example | DB query, API call | LLM tokens, file download, pipeline |

---

## emit_progress

Use `emit_progress()` for **status/progress updates** — not for streaming output content.

```python
class SearchAgent(AgentActor[str, list]):
    async def execute(self, input: str) -> list:
        await self.emit_progress("searching...")   # status update
        results = await self.search(input)
        await self.emit_progress(f"found {len(results)} results")
        return results
```

!!! note
    `emit_progress()` emits `task_progress` events — semantically "how is the task going",
    not "here is output content". For streaming output use `yield` instead.

    It is a no-op if called outside of `execute()` or without an event sink attached.

---

## dispatch_stream

Use `dispatch_stream()` inside `execute()` to stream results from a child agent — and optionally propagate them up:

```python
class OrchestratorAgent(AgentActor[str, list]):
    async def execute(self, input: str):
        # Transparently stream child chunks up to the caller
        async for item in self.context.dispatch_stream(LLMAgent, Task(input=input)):
            match item:
                case StreamEvent(event=e) if e.type == "task_chunk":
                    yield e.data          # re-yield → becomes task_chunk for caller
                case StreamResult(result=r):
                    pass                  # final result available here
```

`dispatch_stream` is the streaming counterpart of `dispatch`:

| | `dispatch` | `dispatch_stream` |
|--|--|--|
| Child output | Single `TaskResult` | `StreamItem` sequence |
| Ephemeral actor | Stopped after `await` | Stopped after generator exhausted |
| Use when child | Returns one result | Streams chunks |

---

## Failure handling

`AgentActor` follows the actor model's let-it-crash philosophy:

- `execute()` raises → framework emits `task_failed`, re-raises for supervision
- The parent's `supervisor_strategy()` decides: restart / stop / escalate
- The `ask()` caller receives the exception

```python
class FlakyAgent(AgentActor):
    async def execute(self, input):
        if random.random() < 0.3:
            raise TransientError("try again")
        return process(input)

# Use plugins.retry for automatic retries
from actor_for_agents.plugins.retry import ask_with_retry

result = await ask_with_retry(
    ref, Task(input="data"),
    max_attempts=3,
    base_backoff_s=0.1,
)
```

---

## AgentSystem

`AgentSystem` extends `ActorSystem` with event streaming. It is a drop-in replacement — all existing APIs work unchanged.

```python
from actor_for_agents.agents import AgentSystem

system = AgentSystem("app")
```

### `run()` — spawn and stream

Spawns a fresh root agent for each call, streams all `TaskEvent`s from the entire actor tree.

```python
async for event in system.run(ResearchOrchestrator, user_query):
    if event.type == "task_progress":
        print(event.data)
```

Child agents spawned via `dispatch()` automatically route their events to the same stream.

### `ask_stream()` — stream from existing ref

Reuses an already-spawned agent. Returns a stream of `StreamItem` objects — events first, then the final result.

```python
ref = await system.spawn(SummaryAgent, "summarizer")

async for item in ref.ask_stream(Task(input="long document...")):
    match item:
        case StreamEvent(event=e):
            print(e.type, e.data)    # intermediate events
        case StreamResult(result=r):
            print(r.output)          # final output (last item)
```

`ask_stream` is symmetric with `ref.ask()`. Use it when the agent is long-lived and handles multiple requests.

### Comparison

| | `run()` | `ask_stream()` |
|--|---------|----------------|
| Agent lifecycle | Fresh spawn per call | Reuse existing ref |
| Where to call | On the system | On the ref |
| Input | raw value | `Task` |
| Output | `TaskEvent` stream | `StreamItem` stream (`StreamEvent \| StreamResult`) |

---

## Stream types

`ask_stream()` yields a sealed `StreamItem` ADT:

```python
from actor_for_agents.agents.task import StreamEvent, StreamResult

async for item in ref.ask_stream(Task(input="...")):
    match item:
        case StreamEvent(event=e):   # TaskEvent wrapper
            ...
        case StreamResult(result=r): # TaskResult wrapper — always last
            ...
```

| Type | Field | Description |
|------|-------|-------------|
| `StreamEvent` | `event: TaskEvent` | One lifecycle event |
| `StreamResult` | `result: TaskResult` | Final outcome, always the last item |

---

## Guard: don't override on_receive

If you accidentally override `on_receive()` in an `AgentActor` subclass, the framework emits a `UserWarning` at class definition time:

```python
class MyAgent(AgentActor):
    async def on_receive(self, message):  # ← UserWarning
        ...
```

```
UserWarning: MyAgent: do not override on_receive() in AgentActor subclasses.
Implement execute() instead — the framework manages on_receive().
```
