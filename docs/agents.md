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

Events flow to a `RunStream` when using `AgentSystem` (coming in M3). With plain `ActorSystem`, events are silently dropped unless you attach an event sink manually.

---

## emit_progress

Use `emit_progress()` inside `execute()` to stream intermediate results:

```python
class StreamingAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        chunks = []
        async for token in llm.stream(input):
            await self.emit_progress(token)   # emits task_progress
            chunks.append(token)
        return "".join(chunks)
```

!!! note
    `emit_progress()` is a no-op if:

    - Called outside of `execute()` (no active task)
    - No event sink is attached (plain `ActorSystem` without `AgentSystem`)

---

## Failure handling

`AgentActor` follows the actor model's let-it-crash philosophy:

- `execute()` raises → framework emits `task_failed`, re-raises for supervision
- The parent's `supervisor_strategy()` decides: restart / stop / escalate
- The `ask()` caller receives the exception

```python
class FlakyAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
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

## Guard: don't override on_receive

If you accidentally override `on_receive()` in an `AgentActor` subclass, the framework emits a `UserWarning` at class definition time:

```python
class MyAgent(AgentActor[str, str]):
    async def on_receive(self, message):  # ← UserWarning
        ...
```

```
UserWarning: MyAgent: do not override on_receive() in AgentActor subclasses.
Implement execute() instead — the framework manages on_receive().
```
