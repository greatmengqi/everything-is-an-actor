# Core Actor API Reference

## Actor

```python
from everything_is_an_actor import Actor
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

#### `stop_policy()`

```python
def stop_policy(self) -> StopPolicy
```

Override to customize automatic lifecycle management.

Default: `StopMode.NEVER` (actor never auto-stops).

```python
from everything_is_an_actor import StopMode, AfterMessage, AfterIdle, StopPolicy

# Auto-stop after one message
def stop_policy(self) -> StopPolicy:
    return StopMode.ONE_TIME

# Auto-stop after receiving specific message
def stop_policy(self) -> StopPolicy:
    return AfterMessage(message="shutdown")

# Auto-stop after idle timeout
def stop_policy(self) -> StopPolicy:
    return AfterIdle(seconds=60.0)
```

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
from everything_is_an_actor import ActorRef
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

#### `free_ask(message)`

```python
def free_ask(self, message: Any) -> Free[ActorF, Any]
```

Lift an ask operation into the Free monad for composable workflows.

#### `free_tell(message)`

```python
def free_tell(self, message: Any) -> Free[ActorF, None]
```

Lift a tell operation into the Free monad for composable workflows.

#### `free_stop()`

```python
def free_stop(self) -> Free[ActorF, None]
```

Lift a stop operation into the Free monad for composable workflows.

---

## ActorSystem

```python
from everything_is_an_actor import ActorSystem
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

#### `get_actor(path)`

```python
async def get_actor(path: str) -> ActorRef | None
```

Get actor ref by path. Returns `None` if not found.

Path format: `/system-name/actor-name/.../actor-name`

Example: `/app/workers/collector`

#### `ask(path, message, timeout)`

```python
async def ask(path: str, message: Any, timeout: float = 30.0) -> Any
```

Shorthand for `get_actor(path)` + `ref.ask(message)`.

Raises `ValueError` if actor not found.

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
from everything_is_an_actor import OneForOneStrategy

OneForOneStrategy(max_restarts=3, within_seconds=60)
```

### AllForOneStrategy

Restart all siblings when any one fails.

```python
from everything_is_an_actor import AllForOneStrategy

AllForOneStrategy(max_restarts=3, within_seconds=60)
```

### Directive

```python
from everything_is_an_actor import Directive

Directive.restart   # default
Directive.resume
Directive.stop
Directive.escalate
```

---

## Stop Policy ADT

### StopMode

```python
from everything_is_an_actor import StopMode, StopPolicy
```

```python
class StopMode(Enum):
    NEVER = auto()    # Never auto-stop (default)
    ONE_TIME = auto() # Stop after processing one message
```

### AfterMessage

```python
from everything_is_an_actor import AfterMessage
```

```python
@dataclass
class AfterMessage:
    message: Any  # Stop after receiving this specific message
```

### AfterIdle

```python
from everything_is_an_actor import AfterIdle
```

```python
@dataclass
class AfterIdle:
    seconds: float  # Stop after being idle for N seconds
```

### StopPolicy

```python
from everything_is_an_actor import StopPolicy
```

Union type: `StopMode | AfterMessage | AfterIdle`
