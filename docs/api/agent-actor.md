# AgentActor API Reference

## Task

```python
from actor_for_agents.agents import Task
```

A unit of work sent to an `AgentActor`.

**Fields**

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `input` | `Any` | required | The input data for the agent |
| `id` | `str` | auto (uuid hex) | Unique task identifier |

**Example**

```python
task = Task(input="summarize this document")
task = Task(input={"query": "actor model", "limit": 5}, id="my-task-001")
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
| `output` | `Any` | The value returned by `execute()` |
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
| `type` | `str` | `task_started`, `task_progress`, `task_completed`, `task_failed` |
| `task_id` | `str` | The associated task ID |
| `agent_path` | `str` | Actor path (e.g. `/app/summarizer`) |
| `data` | `Any` | Progress data or final output |

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

Base class for AI agents (Level 4). Inherits from `Actor`.

### Methods to override

#### `execute(input)`

```python
async def execute(self, input: Any) -> Any
```

Implement your agent logic here. The return value becomes `TaskResult.output`.

Raise any exception to signal failure. The framework emits `task_failed` and supervision handles the restart.

```python
class SummaryAgent(AgentActor):
    async def execute(self, input: str) -> str:
        return await llm.summarize(input)
```

#### `emit_progress(data)`

```python
async def emit_progress(self, data: Any) -> None
```

Emit a `task_progress` event during `execute()`. No-op if called outside an active task or without an event sink attached.

```python
async def execute(self, input):
    async for chunk in llm.stream(input):
        await self.emit_progress(chunk)
    return "done"
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

## Usage with ActorSystem

`AgentActor` works with the standard `ActorSystem`. Messages must be wrapped in `Task`.

```python
from actor_for_agents import ActorSystem
from actor_for_agents.agents import AgentActor, Task

system = ActorSystem("app")
ref = await system.spawn(SummaryAgent, "summarizer")

result = await ref.ask(Task(input="..."))
print(result.output)
print(result.status)    # TaskStatus.COMPLETED

await system.shutdown()
```
