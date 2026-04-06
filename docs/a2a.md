# A2A: Agent-to-Agent Multi-Turn Protocol

Single-machine A2A support for agent discovery and multi-turn conversations.

## Overview

Two additions to the agent layer:

1. **AgentCard** — declare what an agent can do
2. **discover()** — find agents by skill

Multi-turn is a usage pattern, not a framework feature. Actor is already a state machine.

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

## Multi-Turn Pattern

Actor is a state machine. `execute()` is called per message. `self` tracks state.

### Child agent — stateful

```python
class TranslateAgent(AgentActor[str, str]):
    __card__ = AgentCard(skills=("translation",))
    _pending: str | None = None
    _state: str = "init"

    async def execute(self, input: str) -> str:
        match self._state:
            case "init":
                self._pending = input
                self._state = "waiting_lang"
                return "Chinese or English?"
            case "waiting_lang":
                self._state = "waiting_style"
                self._lang = input
                return "Formal or casual?"
            case "waiting_style":
                self._state = "init"
                return f"{input} {self._lang}: {self._pending}"
```

### Parent agent — ask loop

```python
class OrchestratorAgent(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        ref = await self.context.spawn(TranslateAgent, "translator")
        result = await self.context.ask(ref, Task(input=input))

        while self.needs_more(result):
            answer = self.decide(result.output)
            result = await self.context.ask(ref, Task(input=answer))

        return result.output
```

Each round is a normal `ask` → `TaskResult`. No new framework mechanism.

## Design Rationale

### Why no special multi-turn support?

Actor is already a state machine. Adding Inquiry types, InputRequired status, callback methods, receive primitives, or become mechanisms would duplicate what the actor model already provides. Multi-turn is just repeated `ask` with a stateful actor.

### Why AgentCard as class attribute?

Agent capabilities are static metadata — they don't change per instance. A class attribute is the simplest correct representation.

### A2A mapping

| A2A concept | Framework equivalent |
|-------------|---------------------|
| Agent Card | `AgentActor.__card__` |
| Task | `Task[I]` |
| `input-required` | Actor state + ask loop |
| Artifact | `TaskResult[O].output` |
| Streaming | `ask_stream()` |
| Discovery | `AgentSystem.discover()` |
