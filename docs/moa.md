# Mixture of Agents (MOA)

Composable multi-agent orchestration pattern — parallel proposers → quorum validation → aggregation, chained in layers.

Built on the [Flow API](flow.md) as a pattern library. Dependency direction: `moa/ → flow/ → agents/ → core/`.

---

## Quick Start

```python
import asyncio
from everything_is_an_actor.agents import AgentActor
from everything_is_an_actor.moa import MoASystem, moa_layer, moa_tree, LayerOutput
from everything_is_an_actor.flow.quorum import QuorumResult

# Proposers
class Researcher(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return f"Research on: {input}"

class Critic(AgentActor[str, str]):
    async def execute(self, input: str) -> str:
        return f"Critique of: {input}"

# Aggregator
class Synthesizer(AgentActor[QuorumResult[str], str]):
    async def execute(self, results: QuorumResult[str]) -> str:
        return "\n".join(results.succeeded)

# Build and run
system = MoASystem()
result = await system.run(
    moa_tree([
        moa_layer(
            proposers=[Researcher, Critic],
            aggregator=Synthesizer,
            min_success=1,
        ),
    ]),
    input="What is the actor model?",
)
await system.shutdown()
```

---

## Architecture

```
everything_is_an_actor/
  moa/
    patterns.py    moa_layer(), moa_tree()
    layer_output.py  LayerOutput directive carrier
    system.py      MoASystem (high-level entry point)
    utils.py       format_references()
```

MOA is purely compositional — it uses existing Flow primitives (`at_least`, `agent`, `pure`, `flat_map`) without modifying core or agents.

---

## Core Components

### `moa_layer()` — Single Layer

A single MOA layer: parallel proposers → quorum → aggregator.

```python
from everything_is_an_actor.moa import moa_layer

layer = moa_layer(
    proposers=[Agent1, Agent2, Agent3],
    aggregator=SynthesisAgg,
    min_success=2,       # at least 2 must succeed
    timeout=15.0,        # per-proposer timeout
)
```

Internally:
1. Injects directive into proposer input (if present from previous layer).
2. Runs proposers via `at_least(min_success, ...)` — Validated semantics.
3. Feeds `QuorumResult` to aggregator.
4. Extracts directive from `LayerOutput` (if returned).

### `moa_tree()` — Multi-Layer Pipeline

Chains layers via `flat_map` with directive passing between layers.

```python
from everything_is_an_actor.moa import moa_tree

pipeline = moa_tree([
    moa_layer(proposers=[R1, R2, R3], aggregator=Synth, min_success=2),
    moa_layer(proposers=[Critic], aggregator=Refiner),
])
```

- Wraps input as `(input, None)` for the first layer.
- Each layer outputs `(result, directive)`.
- Final layer result is unwrapped automatically.

### `MoASystem` — High-Level Entry Point

Owns the full `ActorSystem → AgentSystem` lifecycle. For users who don't need low-level control.

```python
from everything_is_an_actor.moa import MoASystem

system = MoASystem()
result = await system.run(pipeline, "query")

# Or stream events
async for event in system.run_stream(pipeline, "query"):
    print(event.type, event.data)

await system.shutdown()
```

---

## Validated Fault-Tolerance

MOA uses the `at_least` combinator from the Flow layer for quorum validation.

- All proposers run in parallel.
- Domain exceptions are recovered into `QuorumResult.failed` list.
- System exceptions (`MemoryError`, `SystemExit`) propagate immediately.
- If `>= min_success` proposers succeeded, the pipeline continues.
- If `< min_success`, a `RuntimeError` is raised.

```python
class SmartAgg(AgentActor[QuorumResult[str], str]):
    async def execute(self, results: QuorumResult[str]) -> str:
        # Inspect failures
        for err in results.failed:
            print(f"Proposer failed: {err}")
        # Use successes
        return "\n".join(results.succeeded)
```

---

## LayerOutput Directive

Aggregators can pass directives to the next layer's proposers by returning `LayerOutput`:

```python
from everything_is_an_actor.moa import LayerOutput

class DirectiveAgg(AgentActor[QuorumResult, LayerOutput[str]]):
    async def execute(self, results: QuorumResult) -> LayerOutput[str]:
        conflicts = find_conflicts(results.succeeded)
        return LayerOutput(
            result=summarize(results.succeeded),
            directive="focus on disagreements" if conflicts else None,
        )
```

When `directive` is set, next layer's proposers receive `{"input": result, "directive": directive}`. When `directive` is `None`, proposers receive the raw result.

---

## Proposer Timeout

Each `moa_layer` has a `timeout` parameter (default 30s). When a proposer exceeds this:

1. `TimeoutError` is raised.
2. The ephemeral actor is interrupted (forced cancellation).
3. `recover()` converts it to a failed entry in `QuorumResult`.
4. Pipeline continues if `min_success` is still met.

---

## `format_references` Utility

Convenience function for LLM-based aggregators:

```python
from everything_is_an_actor.moa import format_references

text = format_references(results)
# 1. [Researcher] quantum computing overview
# 2. [Critic] challenges in quantum computing
```

---

## Three Progressive API Levels

| Level | Entry Point | You Need to Understand |
|-------|-------------|----------------------|
| Beginner | `MoASystem.run(moa_tree([...]), input)` | Just fill in parameters |
| Intermediate | `AgentSystem.run_flow(flow, input)` | Flow composition |
| Advanced | `AgentSystem` + `ActorSystem` | Actor model |

### Intermediate: Direct Flow Composition

```python
from everything_is_an_actor import ActorSystem
from everything_is_an_actor.agents import AgentSystem
from everything_is_an_actor.flow import agent, at_least

pipeline = (
    at_least(2, agent(R1), agent(R2), agent(R3))
    .flat_map(agent(Synthesizer))
    .flat_map(
        at_least(1, agent(Critic))
        .flat_map(agent(Refiner))
    )
)

system = AgentSystem(ActorSystem())
result = await system.run_flow(pipeline, "input")
await system.shutdown()
```

---

## Public API

```python
from everything_is_an_actor.moa import (
    MoASystem,         # high-level entry point
    moa_layer,         # single layer: proposers + quorum + aggregator
    moa_tree,          # multi-layer pipeline with directive passing
    LayerOutput,       # aggregator output with optional directive
    format_references, # prompt formatting utility
)
```
