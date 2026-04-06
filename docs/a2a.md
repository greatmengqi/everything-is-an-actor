# A2A: Agent-to-Agent Multi-Turn Protocol

Single-machine A2A support for agent discovery and multi-turn conversations.

## Overview

Three additions to the agent layer:

1. **AgentCard** — declare what an agent can do
2. **Inquiry + INPUT_REQUIRED** — signal "I need more input" from `execute()`
3. **discover()** — find agents by skill

No new communication primitives. Multi-turn is just `ask` in a loop.

## AgentCard

Declare capabilities via a class attribute:

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
```

## Multi-Turn Conversations

### Single-shot (unchanged)

```python
result = await self.context.ask(ref, Task(input="hello"))
# result.status == TaskStatus.COMPLETED
```

### Multi-turn

Child agent returns `Inquiry` when it needs more info. Parent asks again.

**Child agent:**

```python
from everything_is_an_actor.agents.message import Inquiry

class TranslateAgent(AgentActor[str, str]):
    _pending: str | None = None

    async def execute(self, input: str) -> str | Inquiry:
        if self._pending is None:
            self._pending = input
            return Inquiry("Chinese or English?")
        else:
            result = f"{input}: {self._pending}"
            self._pending = None
            return result
```

**Parent agent:**

```python
class OrchestratorAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        ref = await self.context.spawn(TranslateAgent, "translator")
        result = await self.context.ask(ref, Task(input=input))

        while result.status == TaskStatus.INPUT_REQUIRED:
            answer = self.decide(result.output.question)
            result = await self.context.ask(ref, Task(input=answer))

        return result.output
```

Each round is a normal `ask` → `TaskResult`. The child maintains conversation state via `self`. No new communication mechanism.

### Multiple rounds

Return `Inquiry` as many times as needed:

```python
class DetailedTranslateAgent(AgentActor[str, str]):
    _text: str | None = None
    _lang: str | None = None

    async def execute(self, input: str) -> str | Inquiry:
        if self._text is None:
            self._text = input
            return Inquiry("Chinese or English?")
        elif self._lang is None:
            self._lang = input
            return Inquiry("Formal or casual?")
        else:
            result = f"{input} {self._lang}: {self._text}"
            self._text = self._lang = None
            return result
```

## Design Rationale

### Why not callback / side-channel / receive?

`execute()` already supports returning values. Returning `Inquiry` instead of a final result is the simplest possible signal. The parent uses `ask` — which already exists — in a loop. No new primitives needed.

### Why not a separate A2A package?

AgentCard, Inquiry, and INPUT_REQUIRED are agent-layer concepts. They extend existing types (`AgentActor`, `TaskStatus`) rather than introducing new abstractions.

### A2A mapping

| A2A concept | Framework equivalent |
|-------------|---------------------|
| Agent Card | `AgentActor.__card__` |
| Task | `Task[I]` |
| `input-required` | `TaskStatus.INPUT_REQUIRED` |
| Artifact | `TaskResult[O].output` |
| Streaming | `ask_stream()` |
| Discovery | `AgentSystem.discover()` |
