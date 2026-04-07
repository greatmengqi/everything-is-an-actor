# Flow DSL vs Graph

Semantics, representation, and originality boundaries for the project's Flow model.

## Thesis

For agent orchestration, `Flow` should be the semantic source of truth, `YAML` and `JSON` should be the portable representation, and graph should be a derived view for visualization and execution inspection.

This is the key distinction:

- **Graph-first systems** are excellent at showing topology.
- **Flow-first systems** are better at defining composition semantics.

Agent orchestration usually fails on semantic boundaries, not on drawing nodes and edges.

## Questions this document answers

1. Why is `Flow` a better canonical representation than graph for agent orchestration?
2. Why can `Flow` be expressed precisely in `YAML` and `JSON`?
3. What exactly is original about this repository's Flow model, and what is not?
4. What is the smallest useful semantic core for a Flow YAML DSL?

## Executive summary

### 1. `YAML` and `JSON` can represent Flow precisely

They can, provided Flow is defined as a closed ADT with explicit variants and explicit composition rules.

Once the model is defined in terms of variants such as:

- `Agent`
- `FlatMap`
- `Zip` / `Broadcast` / `Race` / `AtLeast`
- `Branch`
- `RecoverWith` / `FallbackTo`
- `Loop`
- `Notify` / `Tap` / `Guard`

then `YAML` and `JSON` are no longer vague configuration formats. They are serialization formats for the same syntax tree.

### 2. Flow is a better semantic source of truth than graph

Not because it is more abstract, but because it makes the important parts explicit:

- input and output types
- composition rules
- data propagation rules
- failure propagation rules
- recovery semantics
- loop termination semantics
- side-effect boundaries

Graph can carry some of this information, but usually only indirectly, through node metadata, edge annotations, and engine-specific conventions. That works for visualization, but it is a weak place to anchor semantics.

### 3. Graph is still essential

This is not an anti-graph argument.

Graph remains the best surface for:

- editors
- runtime inspection
- Mermaid diagrams
- trace playback
- execution-plan visualization

The recommendation is not Flow *instead of* graph. It is Flow *under* graph.

```text
Flow DSL / ADT      = semantic layer (source of truth)
YAML / JSON         = storage and transport layer
Graph / Mermaid DAG = derived projection layer
Interpreter         = execution semantics
```

## Originality boundary

This section matters because originality claims are easy to overstate.

### What this repository is **not** claiming

It is **not** claiming to have invented any of the following general ideas:

- flow
- workflow
- dataflow
- DAG orchestration
- reactive pipelines

Those ideas all have substantial prior history.

### What this repository **is** claiming

The originality claim is about a *specific* Flow model for agent orchestration.

The model combines these properties into one coherent semantic system:

- `Flow[I, O]` as a typed ADT, not just a runtime graph
- equivalent Python, `YAML`, and `JSON` representations over one semantic core
- graph as a projection, not as the semantic authority
- an actor-native interpreter that maps combinators onto the actor runtime
- first-class primitives for recovery, quorum, loop, guard, and side-channel behavior

That is the right level of originality claim.

### Recommended wording

Use language like this:

> This repository introduces an original Flow model for agent orchestration: a typed `Flow[I, O]` semantic core with equivalent Python, YAML, and JSON representations, where graph is a derived view rather than the source of truth.

Avoid language like this:

- “we invented flow”
- “we invented workflow orchestration”
- “we are the first to represent orchestration without graphs”

## Why Flow can be represented precisely in YAML and JSON

“Precisely represented” means more than “serializable somehow”. It means the language has a stable, reversible, and checkable semantic mapping.

### 1. The structure is closed and enumerable

The combinator set is finite. A parser can map each YAML or JSON fragment onto a known ADT variant instead of guessing behavior from free-form strings.

For example:

- `steps` means sequential composition
- `all` means shared-input broadcast parallelism
- `each` means split-input distributed parallelism
- `race` means first-complete wins
- `at_least` means quorum semantics
- `branch` means typed or tagged dispatch
- `recover_with` means recovery through another flow
- `fallback_to` means retry with the original input on an alternate path
- `loop` means explicit iterative control

### 2. Tree-shaped data matches composition naturally

Flow is fundamentally a tree of compositions.

`YAML` and `JSON` are already good at representing:

- arrays for ordered steps
- objects for named parameters
- nested objects for subflows
- dictionaries for branch mappings

Example:

```yaml
steps:
  - agent: Search
  - all:
      - agent: Analyst
      - agent: Summarizer
  - fallback_to:
      source: {agent: Writer}
      fallback: {agent: BackupWriter}
```

This does not merely store topology. It stores ordered semantics: first sequence, then broadcast parallelism, then failure fallback.

### 3. The representation is statically checkable

Once Flow is an ADT, uploaded YAML can be validated *before* execution.

Examples:

- `flat_map(A, B)`: `A.O == B.I`
- `race(A, B)`: same input type, same output type
- `branch(source, mapping)`: branch keys must match the dispatch space of `source.O`
- `guard(A, check)`: `check` must return `bool`

Graph systems can add validation too, but they often do so after the graph model already exists. Flow makes the validation rules native to the model.

### 4. The mappings can be reversible

The ideal relationship is:

```text
YAML <-> Flow ADT <-> JSON
```

That only works if each layer carries the same semantics, rather than splitting meaning across:

- YAML structure
- graph annotations
- runtime-only inference

If meaning is scattered across those layers, the representation is no longer precise. It is patched together.

## Why graph is a weak semantic source of truth

Graph is strong at expressing adjacency and dependencies.

It is weaker at expressing the semantics that matter most for agents:

- Is a value broadcast to every branch, or split across branches?
- Does failure trigger retry, fallback, recovery, or cancellation?
- Is a side path fire-and-forget, synchronous tap, or post-condition guard?
- Does a loop feed back the intermediate value, or return a final terminal value?
- Is a branch keyed by type, tag, predicate, or string lookup?

Graph can represent these distinctions, but usually with extra node types, extra edge types, or engine-specific metadata. That makes graph an excellent execution view and a poor semantic center.

## Why agent orchestration needs Flow semantics more than classic DAGs do

### 1. Agents exchange typed messages, not just task completion signals

Classic DAGs often care about task dependencies. Agent systems care much more about the shape and meaning of the values flowing between stages.

Questions like these matter constantly:

- What exactly does the upstream agent return?
- Can the next agent consume it safely?
- Should this result be routed to a specialized agent?
- Should the same input be sent to several agents for ensemble or quorum behavior?

That makes agent orchestration look more like composable program construction than like generic job scheduling.

### 2. Failure is part of the primary semantics

In agent systems, these are normal paths, not edge cases:

- model timeout
- tool failure
- structured output violation
- guard rejection
- partial quorum failure

That is why primitives such as `recover_with`, `fallback_to`, `guard`, and `at_least` need to be first-class.

### 3. Substitutability matters

Agent systems constantly need local replacement and local recomposition:

- swap one agent implementation for another
- package a subsequence into a reusable subflow
- define a stable YAML skeleton and extend it in Python where callables are needed

This is more natural when the source representation is a program-like ADT instead of a graph editing surface.

## Recommended layering

The clean architecture is four-layered.

### 1. Flow ADT

Defines:

- the combinator set
- type signatures
- data propagation
- failure propagation
- loop termination

This is the semantic specification.

### 2. YAML / JSON

Handles:

- human authoring
- versioned storage
- cross-process transport
- cross-language protocols

This layer should not invent semantics. It should carry Flow.

### 3. Graph

Handles:

- Mermaid export
- visual editors
- runtime views
- trace expansion

Graph should be derived from Flow, not the other way around.

### 4. Interpreter

Handles execution:

- actor topology
- ask / race / zip / supervision
- local agent invocation
- A2A / gRPC / MCP dispatch

This lets one Flow program target multiple runtimes without changing its top-level semantics.

## Minimal Flow YAML semantic core

If the Flow DSL is meant to be real, its semantic core should stay small, but it must still cover the main orchestration needs.

### Minimal primitive set

| Category | Primitive | Purpose |
|----------|-----------|---------|
| leaf | `agent` | invoke one agent |
| sequential | `steps` | pass one stage's output into the next |
| parallel | `all` | broadcast the same input to multiple flows |
| parallel | `each` | distribute split inputs to multiple flows |
| branching | `branch` | route by type or tag |
| recovery | `fallback_to` / `recover_with` | define failure recovery paths |
| constraint | `guard` | enforce quality or contract checks |
| iteration | `loop` | continue until `Done` |

Derived capabilities can sit on top of this set:

- `race`
- `at_least`
- `notify`
- `tap`

### Minimal data rules

The primitives are not enough by themselves. Data propagation rules must also be explicit.

| Primitive | Input rule | Output rule |
|-----------|------------|-------------|
| `agent` | consumes one input `I` | produces one output `O` |
| `steps` | step `n` feeds step `n+1` | output of final step is the whole output |
| `all` | broadcasts the same input to every branch | aggregates into tuple or list |
| `each` | splits a compound input across branches | aggregates into tuple or list |
| `branch` | consumes upstream output and selects one branch | returns selected branch output |
| `fallback_to` | retries an alternate flow with the original input | returns the successful output |
| `recover_with` | passes an exception value to a recovery flow | returns recovery output |
| `guard` | checks upstream output while preserving the value by default | passes through on success, raises on failure |
| `loop` | feeds `Continue` back into the next iteration, exits with `Done` | returns the `Done` payload |

### Minimal YAML shape

```yaml
flow: AnswerPipeline
steps:
  - agent: Retriever
  - branch:
      source: {agent: Classifier}
      mapping:
        SimpleQuestion:
          agent: FastResponder
        ComplexQuestion:
          steps:
            - all:
                - agent: Researcher
                - agent: Analyst
            - agent: Writer
  - guard:
      source: {agent: QualityChecker}
      check: {agent: Acceptable}
  - fallback_to:
      source: {agent: Publisher}
      fallback: {agent: SafePublisher}
```

Even this small shape already covers sequential composition, branching, broadcast parallelism, guard checks, and fallback behavior.

### Minimal static validation set

The first validator does not need to be huge. It does need to be strict where the semantics matter.

1. Adjacent `steps` nodes must type-check end-to-end.
2. Every `all` branch must accept the same input type.
3. `each` inputs must split cleanly across all child branches.
4. `branch.mapping` must be non-empty and must match the dispatch space.
5. `fallback_to` source and fallback flows must share input and output types.
6. `recover_with` must accept an exception-shaped input.
7. `guard.check` must return `bool`.
8. `loop.body` must return `Continue[T] | Done[U]`.

With these rules in place, the YAML is no longer “just config”. It is a statically bounded orchestration language.

## Implications for this repository

The current repository direction is coherent.

The design already points toward:

- `Flow[I, O]` as the ADT
- Python, YAML, and JSON as equivalent representations
- Mermaid graph as a visualization product
- pre-execution type validation

That combination matters because it concentrates semantics in one place while still allowing portability, validation, and graph-based tooling.

## Bottom line

If the question is:

> Can Flow be represented precisely in JSON and YAML?

the answer is yes, and that is one reason it works well as a canonical orchestration IR.

If the question is:

> Does that make Flow a better fit than graph for agent orchestration?

the precise answer is:

- **yes** as the semantic source of truth
- **no** as a replacement for visualization and editing surfaces

The right architecture is therefore not “Flow or graph”. It is:

```text
Flow defines semantics.
YAML and JSON carry the protocol.
Graph provides the projection.
The interpreter executes the meaning.
```
