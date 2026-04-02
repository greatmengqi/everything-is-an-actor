# Actor Lifecycle and Stop Policy

## Overview

Actors are persistent by default. They run until explicitly stopped. The `stop_policy` mechanism provides declarative auto-stop behavior for ephemeral actors.

## Stop Policy ADT

```python
from everything_is_an_actor import StopMode, AfterMessage, AfterIdle, StopPolicy

class StopMode(Enum):
    NEVER = auto()      # Never auto-stop (default)
    ONE_TIME = auto()   # Stop after processing one message

@dataclass
class AfterMessage:
    message: Any        # Stop after receiving this message

@dataclass
class AfterIdle:
    seconds: float      # Stop after idle for N seconds
```

## Usage

### One-Time Actor

Process one message then stop automatically:

```python
class OneTimeActor(Actor):
    def stop_policy(self) -> StopPolicy:
        return StopMode.ONE_TIME
```

### After Message

Stop when receiving a specific message:

```python
class StoppableActor(Actor):
    def stop_policy(self) -> StopPolicy:
        return AfterMessage(message="shutdown")
```

### After Idle

Stop after being idle for N seconds:

```python
class IdleActor(Actor):
    def stop_policy(self) -> StopPolicy:
        return AfterIdle(seconds=60.0)
```

## API Signatures

### tell(Actor, msg) — Fire and Forget

Spawns a temporary actor, sends message, actor stops itself via `stop_policy`:

```python
await self.tell(EchoActor, "hello")
# EchoActor processes message, then stops based on its stop_policy
```

**Type constraint**: `tell()` requires actor with non-NEVER stop_policy, otherwise raises `TypeError`.

### ask(Actor, msg) — Request/Response

Spawns a temporary actor, sends message, waits for reply, then **manually stops**:

```python
result = await self.ask(EchoActor, "hello")
# Waits for reply, then ref.stop() + ref.join()
```

Uses manual stop, not `stop_policy`.

### spawn(Actor, name) — Persistent Child

Spawns a persistent child actor under parent's supervision:

```python
ref = await self.spawn(WorkerActor, "worker")
# WorkerActor runs until parent stops it or it fails
```

Child actors are supervised and stopped when parent stops.

## Stop Priority

Manual stop and auto stop_policy work together:

1. **Manual stop** (`ref.stop()`) — puts `_Stop` in mailbox
2. **Auto stop_policy** — checked after each message is processed

Both result in graceful shutdown. There's no conflict.

## Examples

### One-Time Worker

```python
class OneTimeWorker(Actor):
    def stop_policy(self) -> StopPolicy:
        return StopMode.ONE_TIME

    async def on_receive(self, task):
        result = await process(task)
        return result
        # Actor stops after this

# Usage
await self.tell(OneTimeWorker, heavy_task)
```

### Idle Timeout

```python
class CacheActor(Actor):
    def stop_policy(self) -> StopPolicy:
        return AfterIdle(seconds=300.0)  # 5 minutes

    async def on_receive(self, message):
        return self.cache.get(message)
        # Resets idle timer after each message
```

### Graceful Shutdown Message

```python
class ServiceActor(Actor):
    def stop_policy(self) -> StopPolicy:
        return AfterMessage(message="shutdown")

    async def on_receive(self, message):
        if message == "shutdown":
            await self.cleanup()
            return "stopping"
        return handle(message)
```
