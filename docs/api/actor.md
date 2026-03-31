# Core Actor API Reference

## Actor

```python
from actor_for_agents import Actor
```

Base class for all actors (Level 5). Use this for infrastructure components that need direct message-passing control. For AI agents, prefer `AgentActor`.

### Methods to override

#### `on_receive(message)`

```python
async def on_receive(self, message: Any) -> Any
```

Handle an incoming message. Return value is sent back as reply for `ask()` calls.

#### `on_started()`

```python
async def on_started(self) -> None
```

Called after creation, before receiving messages.

#### `on_stopped()`

```python
async def on_stopped(self) -> None
```

Called on graceful shutdown. Release resources here.

#### `on_restart(error)`

```python
async def on_restart(self, error: Exception) -> None
```

Called on the new instance before resuming after a supervision-triggered restart.

#### `supervisor_strategy()`

```python
def supervisor_strategy(self) -> SupervisorStrategy
```

Override to customize how this actor supervises its children.

Default: `OneForOneStrategy(max_restarts=3, within_seconds=60)`.

### Properties (via context)

| Property | Type | Description |
|----------|------|-------------|
| `context.self_ref` | `ActorRef` | Reference to this actor |
| `context.parent` | `ActorRef \| None` | Parent actor reference |
| `context.children` | `dict[str, ActorRef]` | Child actor references |
| `context.system` | `ActorSystem` | The containing system |

### Spawning children

```python
async def on_started(self):
    self.worker = await self.context.spawn(WorkerActor, "worker")
```

---

## ActorRef

```python
from actor_for_agents import ActorRef
```

Lightweight handle to a running actor.

### Methods

#### `tell(message)`

```python
async def tell(self, message: Any) -> None
```

Send a message without waiting for a reply (fire-and-forget).

#### `ask(message, timeout)`

```python
async def ask(self, message: Any, timeout: float = 30.0) -> Any
```

Send a message and wait for the reply.

Raises `asyncio.TimeoutError` if the actor doesn't respond within `timeout` seconds.

#### `is_alive`

```python
@property
def is_alive(self) -> bool
```

Returns `True` if the actor is still running.

---

## ActorSystem

```python
from actor_for_agents import ActorSystem
```

Top-level container for root-level actors.

### Methods

#### `spawn(actor_cls, name, ...)`

```python
async def spawn(
    actor_cls: type[Actor],
    name: str,
    *,
    mailbox_size: int = 256,
    mailbox: Mailbox | None = None,
    middlewares: list[Middleware] | None = None,
) -> ActorRef
```

Spawn a root-level actor.

#### `shutdown(timeout)`

```python
async def shutdown(self, *, timeout: float = 10.0) -> None
```

Gracefully stop all actors.

#### `dead_letters`

```python
@property
def dead_letters(self) -> list[DeadLetter]
```

Messages that could not be delivered.

---

## Supervision strategies

### OneForOneStrategy

Restart only the failing child.

```python
from actor_for_agents import OneForOneStrategy

OneForOneStrategy(max_restarts=3, within_seconds=60)
```

### AllForOneStrategy

Restart all siblings when any one fails.

```python
from actor_for_agents import AllForOneStrategy

AllForOneStrategy(max_restarts=3, within_seconds=60)
```

### Directive

```python
from actor_for_agents import Directive

Directive.restart   # default
Directive.resume
Directive.stop
Directive.escalate
```
