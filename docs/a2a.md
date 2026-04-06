# A2A: Agent-to-Agent Multi-Turn Protocol

Single-machine A2A support for agent discovery and multi-turn conversations.

## Overview

Two capabilities added to the agent layer:

1. **Capability discovery** — find agents by skill, not by address
2. **Multi-turn** — agents exchange information mid-execution via `tell` + `receive`

No new packages. No new communication primitives. Everything builds on existing actor messaging.

## AgentCard

Declare what an agent can do via a class attribute:

```python
from everything_is_an_actor.agents.card import AgentCard

class TranslateAgent(AgentActor[str, str]):
    __card__ = AgentCard(
        skills=("translation", "summarization"),
        description="Translates and summarizes documents",
    )

    async def execute(self, input: str) -> str:
        return f"translated: {input}"
```

Agents without `__card__` work normally — they just won't appear in `discover()` results.

## Discovery

Find running agents by skill:

```python
refs = system.discover("translation")
# Returns list of ActorRef whose __card__ declares "translation"
```

## Multi-Turn Conversations

### Single-shot (unchanged)

```python
result = await self.context.ask(ref, Task(input="hello"))
```

### Multi-turn

Both sides use `tell` + `receive`. Symmetric pattern.

**Child agent** — asks parent for more info:

```python
class TranslateAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        # Ask parent for language preference
        await self.message.sender.tell(Inquiry("Chinese or English?"))
        msg = await self.context.receive()
        lang = msg.body

        # Ask another question
        await self.message.sender.tell(Inquiry("Formal or casual?"))
        msg = await self.context.receive()
        style = msg.body

        result = f"{style} {lang}: {input}"
        await self.message.sender.tell(result)
        return result
```

**Parent agent** — delegates and handles inquiries:

```python
class OrchestratorAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        ref = await self.context.spawn(TranslateAgent, "translator")
        await self.context.tell(ref, Task(input=input))

        while True:
            msg = await self.context.receive()
            match msg.body:
                case Inquiry(question=q):
                    await msg.sender.tell("Chinese")
                case str() as result:
                    return result
```

### Message

Every message in `execute()` is accessible via `self.message`:

```python
async def execute(self, input: str) -> str:
    sender = self.message.sender  # who sent this task
    body = self.message.body      # the payload
    ...
```

`receive()` returns a `Message` with the same structure:

```python
msg = await self.context.receive()
msg.body    # payload
msg.sender  # who sent it (Sender with .tell())
```

### External Callers

Non-actor code uses `converse()`:

```python
async for msg in system.converse(TranslateAgent, "hello world"):
    match msg.body:
        case Inquiry(q):
            await msg.sender.tell("Chinese")
        case result:
            print(result)
            break
```

## Design Rationale

### Why `tell` + `receive`, not `ask` + callback?

`ask` blocks the caller's mailbox — the actor can't process new messages until `ask` returns. For multi-turn, both sides need to send and receive freely. `tell` is non-blocking; `receive` pulls the next message from the mailbox.

### Why not a separate A2A package?

`AgentSystem` already manages actor lifecycle, messaging, and discovery (via `VirtualActorRegistry`). A2A concepts (Card, Message, receive) are agent-layer concerns, not a new abstraction layer.

### Why `__card__` as class attribute?

Agent capabilities are static metadata — they don't change per instance. A class attribute is the simplest correct representation. No decorator magic, no method override.
