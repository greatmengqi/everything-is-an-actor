# Flow API Design Spec

Actor-native agent orchestration with categorical concurrency primitives.

## Problem

LangGraph's orchestration model has fundamental design flaws:
- Shared mutable state dict between nodes (no encapsulation)
- Stringly-typed conditional routing (no exhaustive checking)
- No lifecycle management or supervision for nodes
- Weak composability (SubGraph is awkward)
- No type safety (IDE can't help, refactoring is risky)

## Solution

A `Flow[I, O]` ADT — composable workflow combinators derived from category theory, built as data (free construction), interpreted into actor topology at runtime. LangChain provides LLM primitives; the existing actor runtime provides execution.

## Architecture

### Layer Diagram

```
everything_is_an_actor/
  core/                ← actor runtime / supervision / ComposableFuture (existing)
  agents/              ← AgentActor / Task / TaskResult (existing)
  moa/                 ← MOA pattern (existing)
  flow/                ← Flow ADT + combinators + interpreter (new)
  integrations/        ← LLM adapter layer (new)
    langchain/
    openai/
    anthropic/
```

### Dependency Direction

```
integrations/ → flow/ → agents/ → core/
```

- `flow/` only knows `AgentActor[I, O]` — zero external dependencies
- Each integration provides a convenience base class inheriting `AgentActor`
- LangChain (and others) are optional deps: `pip install everything-is-an-actor[langchain]`

## Flow ADT

Flow is a syntax tree in a free category. All combinators build ADT nodes without executing.

### Variants (Sum Type)

```python
Flow[I, O] =
  | Agent(cls: type[AgentActor[I, O]])
  | Pure(f: Callable[[I], O])
  | FlatMap(first: Flow[I, M], next: Flow[M, O])
  | Zip(left: Flow[A, B], right: Flow[C, D])       # → Flow[tuple[A,C], tuple[B,D]]
  | Map(source: Flow[I, M], f: Callable[[M], O])
  | Branch(mapping: dict[type, Flow])
  | Race(flows: list[Flow[I, O]])
  | Recover(source: Flow[I, O], handler: Callable[[Exception], O])
  | RecoverWith(source: Flow[I, O], handler: Flow[Exception, O])
  | FallbackTo(source: Flow[I, O], fallback: Flow[I, O])
  | DivertTo(source: Flow[I, O], side: Flow[O, Any], when: Callable[[O], bool])
  | Loop(body: Flow[I, Continue[I] | Done[O]], max_iter: int)
  | LoopWithState(body: Flow[tuple[I, S], Continue[I] | Done[O]], init_state: S, max_iter: int)
  | AndThen(source: Flow[I, O], callback: Callable[[O], None])
  | Filter(source: Flow[I, O], predicate: Callable[[O], bool])
```

### Control Types

```python
@dataclass(frozen=True)
class Continue(Generic[A]):
    """Loop continues — feed value back as next iteration's input."""
    value: A

@dataclass(frozen=True)
class Done(Generic[B]):
    """Loop terminates — produce final result."""
    value: B
```

## Concurrency Primitives

Eight primitives derived from symmetric monoidal category + coproduct + trace:

| Primitive | Method | Constructor | Category |
|---|---|---|---|
| Sequential | `flat_map(flow)` | — | Monad bind / Kleisli composition |
| Parallel | `zip(flow)` | — | Tensor product |
| Transform | `map(f)` | `pure(f)` | Functor / arr |
| Conditional | `branch({T: flow})` | — | Coproduct dispatch |
| Race | — | `race(*flows)` | First completed |
| Recovery | `recover(h)` / `recover_with(flow)` / `fallback_to(flow)` | — | Supervision |
| Divert | `divert_to(flow, when)` | — | Side-channel (Akka divertTo) |
| Loop | — | `loop(body)` / `loop_with_state(body, init)` | tailRecM / trace |

Plus two utilities:
- `and_then(callback)` — tap for side effects (logging, metrics)
- `filter(predicate)` — guard / assertion

## User API

### Method Chain (Scala Future Style)

```python
class Flow(Generic[I, O]):
    def map(self, f: Callable[[O], O2]) -> Flow[I, O2]
    def flat_map(self, next: Flow[O, O2]) -> Flow[I, O2]
    def zip(self, other: Flow[I2, O2]) -> Flow[tuple[I, I2], tuple[O, O2]]
    def branch(self, mapping: dict[type, Flow]) -> Flow[I, O2]
    def recover(self, handler: Callable[[Exception], O]) -> Flow[I, O]
    def recover_with(self, handler: Flow[Exception, O]) -> Flow[I, O]
    def fallback_to(self, other: Flow[I, O]) -> Flow[I, O]
    def divert_to(self, side: Flow[O, Any], when: Callable[[O], bool]) -> Flow[I, O]
    def and_then(self, callback: Callable[[O], None]) -> Flow[I, O]
    def filter(self, predicate: Callable[[O], bool]) -> Flow[I, O]
```

### Constructors

```python
def agent(cls: type[AgentActor[I, O]]) -> Flow[I, O]
def pure(f: Callable[[I], O]) -> Flow[I, O]
def race(*flows: Flow[I, O]) -> Flow[I, O]
def loop(body: Flow[I, Continue[I] | Done[O]], max_iter: int = 10) -> Flow[I, O]
def loop_with_state(body: Flow[tuple[I, S], Continue[I] | Done[O]], init_state: S, max_iter: int = 10) -> Flow[I, O]
```

### Branch Semantics

Condition = output type. `branch` does `isinstance` dispatch:

```python
# Upstream produces tagged union → branch dispatches by type
agent(Classifier).branch({
    SimpleQ:  agent(QuickAnswer),
    ComplexQ: agent(DeepResearch),
})

# Convenience for binary predicate
agent(Scorer).branch_on(
    lambda s: s.score > 0.8,
    then=agent(Premium),
    otherwise=agent(Basic),
)
```

### Loop Semantics (tailRecM)

Body returns `Continue[A]` (keep going) or `Done[B]` (exit):

```python
refine = loop(
    agent(Writer).flat_map(agent(Critic)),  # str → Continue[str] | Done[str]
    max_iter=3,
)
# max_iter exhausted → raise exception (let-it-crash, recoverable via recover)
```

`loop_with_state` for explicit feedback state (trace):

```python
refine = loop_with_state(
    agent(WriterWithHistory),    # (draft, history) → Continue | Done
    init_state=list,
    max_iter=5,
)
```

## Actor Interpreter

Recursive interpret over the Flow ADT, using the existing actor infrastructure:

| Variant | Actor Strategy |
|---|---|
| `Agent(cls)` | `context.ask(cls, Task(input=...))` |
| `Pure(f)` | `f(input)` — no actor |
| `FlatMap(f, g)` | `interpret(g, interpret(f, input))` |
| `Zip(f, g)` | `ComposableFuture.sequence([...])` — parallel |
| `Map(f, fn)` | `fn(interpret(f, input))` |
| `Branch(m)` | `interpret(upstream); interpret(m[type(result)], result)` |
| `Race(flows)` | `asyncio.wait(FIRST_COMPLETED)` + cancel remaining |
| `Recover(f, h)` | `try interpret(f) except → h(e)` |
| `DivertTo(f, s, p)` | interpret f; if p(result): fire-and-forget s; return result |
| `Loop(body)` | `while True: match interpret(body): Continue → again; Done → return` |

The interpreter itself is an `AgentActor` with a `context`, so it can spawn child actors and use `ComposableFuture`.

### Entry Points

```python
system = AgentSystem()
result = await system.run(flow, input="query")
async for event in system.run_stream(flow, input="query"):
    print(event)
```

## Integration Layer

Each integration provides a declarative `AgentActor` subclass with LLM-specific convenience:

```python
# langchain adapter
class LangChainAgent(AgentActor[I, O], Generic[I, O]):
    model: BaseChatModel
    tools: list[BaseTool] = []
    system_prompt: str = ""
    output_parser: BaseOutputParser | None = None

    async def execute(self, input: I) -> O:
        # auto: messages → bind tools → invoke → parse
        ...
    # Users can override execute() for full control
```

Design constraints:
- `flow/` never imports any integration — it only knows `AgentActor`
- Integrations don't wrap LangChain abstractions — `model` IS `BaseChatModel`
- Override `execute()` as escape hatch for full control

## Serialization

Flow is data → serialization is natural:

```python
flow.to_dict() → dict       # JSON-compatible
flow.to_yaml() → str
flow.to_json() → str
Flow.from_dict(d, registry) → Flow
Flow.from_yaml(s, registry) → Flow
```

`registry: dict[str, type[AgentActor]]` — agent classes stored as string names in serialized form, resolved via registry on deserialization.

## Visualization

Flow ADT → mermaid diagram:

```python
flow.visualize() → str       # mermaid source
flow.type_signature → str    # e.g. "str → str"
```

## File Structure

```
everything_is_an_actor/
  flow/
    __init__.py          # public API re-exports
    flow.py              # Flow ADT definition (all variant dataclasses)
    combinators.py       # agent(), pure(), race(), loop(), loop_with_state()
    interpreter.py       # actor interpreter (recursive interpret over ADT)
    serialize.py         # to_dict / from_dict / to_yaml / from_yaml
    visualize.py         # mermaid generation
  integrations/
    __init__.py
    langchain/
      __init__.py
      agent.py           # LangChainAgent base class
    openai/
      __init__.py
      agent.py           # OpenAIAgent base class
    anthropic/
      __init__.py
      agent.py           # AnthropicAgent base class
```

## Usage Example

```python
from everything_is_an_actor.flow import agent, pure, race, loop, Flow, Continue, Done
from everything_is_an_actor.integrations.langchain import LangChainAgent
from everything_is_an_actor.agents import AgentSystem
from langchain_openai import ChatOpenAI

# Define agents
class Researcher(LangChainAgent[str, str]):
    model = ChatOpenAI(model="gpt-4o")
    system_prompt = "Search and summarize."

class Analyst(LangChainAgent[str, str]):
    model = ChatOpenAI(model="gpt-4o")
    system_prompt = "Analyze documents."

class Writer(LangChainAgent[str, str]):
    model = ChatOpenAI(model="gpt-4o-mini")
    system_prompt = "Write a report."

class Critic(LangChainAgent[str, Continue[str] | Done[str]]):
    model = ChatOpenAI(model="gpt-4o")
    system_prompt = "Accept or request revision."

class Fallback(LangChainAgent[str, str]):
    model = ChatOpenAI(model="gpt-4o-mini")
    system_prompt = "Best-effort answer."

# Compose
pipeline = (
    agent(Researcher)
    .zip(agent(Analyst))
    .map(lambda pair: f"Web:\n{pair[0]}\n\nDocs:\n{pair[1]}")
    .flat_map(loop(
        agent(Writer).flat_map(agent(Critic)),
        max_iter=3,
    ))
    .recover_with(agent(Fallback))
    .and_then(lambda r: print(f"[done] {len(r)} chars"))
)

# Run
async def main():
    system = AgentSystem()
    result = await system.run(pipeline, input="What is quantum computing?")
    print(result)

    # Or stream
    async for event in system.run_stream(pipeline, input="Compare RLHF vs DPO"):
        print(event)

    # Inspect
    print(pipeline.visualize())
    print(pipeline.type_signature)
```

## Non-Goals

- Not replacing LangChain (use its LLM/Tool/Callback layer)
- Not reimplementing MOA (MOA is a separate pattern, already exists)
- Not providing LangGraph migration path
- Not supporting non-actor backends (Flow interprets to actors only)
