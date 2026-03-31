# AgentActor API Reference

## Task

```python
from actor_for_agents.agents import Task
```

A unit of work sent to an `AgentActor`.

Generic type parameter `InputT` constrains the input type.

**Fields**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `input` | `InputT` | required | The input data for the agent |
| `id` | `str` | auto (uuid hex) | Unique task identifier |

**Example**

```python
task: Task[str] = Task(input="summarize this document")
task: Task[dict] = Task(input={"query": "actor model", "limit": 5}, id="my-task-001")
```

---

## TaskResult

```python
from actor_for_agents.agents import TaskResult
```

The outcome returned by `AgentActor.on_receive()` after `execute()` completes.

**Fields**

| Field | Type | Description |
|-------|------|-------------|
| `task_id` | `str` | Matches the originating `Task.id` |
| `output` | `OutputT \| None` | The value returned by `execute()` |
| `error` | `str \| None` | Error message if status is `FAILED` |
| `status` | `TaskStatus` | `COMPLETED` or `FAILED` |

---

## TaskStatus

```python
from actor_for_agents.agents import TaskStatus
```

```python
class TaskStatus(Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    COMPLETED = "completed"
    FAILED    = "failed"
    CANCELLED = "cancelled"
```

---

## TaskEvent

```python
from actor_for_agents.agents import TaskEvent
```

An event emitted during task execution.

**Fields**

| Field | Type | Description |
|-------|------|-------------|
| `type` | `str` | `task_started`, `task_progress`, `task_chunk`, `task_completed`, `task_failed` |
| `task_id` | `str` | The associated task ID |
| `agent_path` | `str` | Actor path (e.g. `/app/summarizer`) |
| `data` | `Any` | Progress data or final output |
| `parent_task_id` | `str \| None` | `task_id` of the calling agent's task. `None` for the root agent. |
| `parent_agent_path` | `str \| None` | Actor path of the parent agent. `None` for the root agent. |

`parent_task_id` and `parent_agent_path` enable OpenTelemetry-style trace reconstruction from a flat event stream.

---

## StreamItem

```python
from actor_for_agents.agents.task import StreamItem, StreamEvent, StreamResult
```

Sealed ADT yielded by `ActorRef.ask_stream()`. Use `match/case` for exhaustive handling.

### StreamEvent

Wraps a `TaskEvent` emitted during execution.

| Field | Type | Description |
|-------|------|-------------|
| `event` | `TaskEvent` | The wrapped lifecycle event |

### StreamResult

Wraps the final `TaskResult`. Always the **last** item in the stream.

| Field | Type | Description |
|-------|------|-------------|
| `result` | `TaskResult[OutputT]` | The wrapped task outcome |

**Example**

```python
async for item in ref.ask_stream(Task(input="...")):
    match item:
        case StreamEvent(event=e):
            print(e.type, e.data)
        case StreamResult(result=r):
            print(r.output)
```

---

## ActorConfig

```python
from actor_for_agents.agents import ActorConfig
```

Optional actor-level configuration for Level 1-3 plain agent classes.

**Fields**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `mailbox_size` | `int` | `256` | Max queued messages |
| `max_restarts` | `int` | `3` | Max restarts within `within_seconds` |
| `within_seconds` | `float` | `60.0` | Restart rate window |

**Usage**

```python
class MyAgent:
    __actor__ = ActorConfig(mailbox_size=128, max_restarts=5)

    async def execute(self, input): ...
```

> Note: `ActorConfig` is used by `AgentSystem` (M3). Ignored when using plain `ActorSystem`.

---

## AgentActor

```python
from actor_for_agents.agents import AgentActor
```

Base class for AI agents (Level 4). Inherits from `Actor`. Generic over input type `InputT` and output type `OutputT`.

```python
class MyAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str: ...
```

Type parameters flow end-to-end: `Task[InputT]` → `execute(input: InputT) -> OutputT` → `TaskResult[OutputT]`.

### Methods to override

#### `execute(input)`

```python
async def execute(self, input: InputT) -> OutputT
```

Implement your agent logic here. Supports two output modes:

**Single result** — `return` a value. Becomes `TaskResult.output`.

```python
class SummaryAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return await llm.summarize(input)
```

**Streaming** — `yield` values (async generator). Each `yield` emits a `task_chunk` event immediately. `TaskResult.output` is the collected list of all yielded values.

```python
class LLMAgent(AgentActor[str, list]):
    async def execute(self, prompt: str):
        async for token in openai.stream(prompt):
            yield token   # → task_chunk event, data=token
```

Raise any exception to signal failure. The framework emits `task_failed` and supervision handles the restart.

#### `emit_progress(data)`

```python
async def emit_progress(self, data: Any) -> None
```

Emit a `task_progress` event for **status/progress updates** during `execute()`. No-op if called outside an active task or without an event sink attached.

Use `yield` (streaming mode) for output content; use `emit_progress()` for "how is the task going" messages.

```python
async def execute(self, input):
    await self.emit_progress("searching...")
    results = await self.search(input)
    await self.emit_progress(f"found {len(results)} results")
    return results
```

#### `on_started()`

```python
async def on_started(self) -> None
```

Called once after the actor is spawned, before any messages are processed.

#### `on_stopped()`

```python
async def on_stopped(self) -> None
```

Called on graceful shutdown. Release resources here.

#### `on_restart(error)`

```python
async def on_restart(self, error: Exception) -> None
```

Called on the new instance after a supervision-triggered restart.

#### `supervisor_strategy()`

```python
def supervisor_strategy(self) -> SupervisorStrategy
```

Override to customize child supervision. Default: `OneForOneStrategy(max_restarts=3, within_seconds=60)`.

### Do not override

#### `on_receive(message)`

Managed by the framework. Handles `Task` wrapping, event emission, and error propagation.

Accidentally overriding this method emits a `UserWarning` at class definition time.

---

## ActorContext

Injected as `self.context` before `on_started`. Available inside all lifecycle hooks and `execute()`.

### `dispatch(target, message, *, timeout, name)`

Spawn an ephemeral child actor (or send to an existing ref), await the result, stop the child.

```python
result = await self.context.dispatch(SearchAgent, Task(input=query))
```

### `dispatch_parallel(tasks, *, timeout)`

Fan-out to multiple agents concurrently; results preserve task order. If any task raises, remaining tasks are cancelled and all ephemeral children are cleaned up before the exception propagates.

```python
results = await self.context.dispatch_parallel([
    (AgentA, Task(input="x")),
    (AgentB, Task(input="y")),
])
```

### `dispatch_stream(target, message, *, timeout, name)`

Streaming counterpart of `dispatch`. Yields `StreamItem` objects — events first, then the final `StreamResult`. Ephemeral children are stopped after the stream is exhausted or the caller breaks early.

Use inside a streaming `execute()` to transparently forward child chunks:

```python
class OrchestratorAgent(AgentActor[str, list]):
    async def execute(self, input: str):
        async for item in self.context.dispatch_stream(LLMAgent, Task(input=input)):
            match item:
                case StreamEvent(event=e) if e.type == "task_chunk":
                    yield e.data          # re-yield → becomes task_chunk for caller
                case StreamResult(result=r):
                    pass                  # final result available here
```

| | `dispatch` | `dispatch_stream` |
|--|--|--|
| Child output | Single `TaskResult` | `StreamItem` sequence |
| Ephemeral actor | Stopped after `await` | Stopped after generator exhausted |
| Use when child | Returns one result | Streams chunks |

---

## Usage with ActorSystem

`AgentActor` works with the standard `ActorSystem`. Messages must be wrapped in `Task`.

```python
from actor_for_agents import ActorSystem
from actor_for_agents.agents import AgentActor, Task

system = ActorSystem("app")
ref = await system.spawn(SummaryAgent, "summarizer")

result: TaskResult[str] = await ref.ask(Task(input="..."))
print(result.output)    # str
print(result.status)    # TaskStatus.COMPLETED

await system.shutdown()
```

---

## ActorRef.ask_stream

```python
async def ask_stream(self, message: Task, *, timeout: float = 30.0) -> AsyncIterator[StreamItem]
```

Stream `TaskEvent`s from an already-spawned `AgentActor` ref, then yield the final `TaskResult`.

- The agent is **not** re-spawned. Reuse the same ref across multiple calls.
- Child agents spawned via `dispatch()` inside `execute()` inherit the event sink automatically.
- Raises the agent's exception after the stream is exhausted (if `execute()` raised).

```python
from actor_for_agents.agents.system import AgentSystem
from actor_for_agents.agents.task import StreamEvent, StreamResult

system = AgentSystem()
ref = await system.spawn(SummaryAgent, "summarizer")

# First call
async for item in ref.ask_stream(Task(input="doc 1")):
    match item:
        case StreamEvent(event=e):
            print(e.type, e.data)
        case StreamResult(result=r):
            print(r.output)

# Reuse same ref
async for item in ref.ask_stream(Task(input="doc 2")):
    ...
```

---

## AgentSystem

```python
from actor_for_agents.agents import AgentSystem
```

Drop-in replacement for `ActorSystem` with event-streaming support.

### `run(agent_cls, input, *, run_id, timeout)`

Spawns a fresh root agent and streams all `TaskEvent`s from the entire actor tree.

```python
async for event in system.run(ResearchOrchestrator, user_query, timeout=120.0):
    if event.type == "task_progress":
        yield event.data
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `agent_cls` | `type[AgentActor]` | required | Root agent class to instantiate |
| `input` | `Any` | required | Passed to root agent as `Task.input` |
| `run_id` | `str \| None` | auto | Stable ID for log correlation |
| `timeout` | `float` | `600.0` | Max seconds for root agent to complete |

### `abort(run_id)`

Cancel a running agent tree by `run_id`. No-op if already finished.

```python
await system.abort(run_id)
```
