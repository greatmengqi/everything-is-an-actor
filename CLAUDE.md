# CLAUDE.md

Coding principles for `everything-is-an-actor`. Follow these when writing or reviewing code in this repo.

---

## Layer separation

The codebase has five layers:

```
everything_is_an_actor.integrations  ← LLM adapters (LangChain, OpenAI, Anthropic)
everything_is_an_actor.flow          ← Flow ADT + categorical combinators + actor interpreter
everything_is_an_actor.moa           ← MOA orchestration pattern (MoATree, MoABuilder)
everything_is_an_actor.agents        ← AI-specific (Task, AgentActor, streaming)
everything_is_an_actor.core          ← generic actor runtime (Actor, ActorRef, ActorSystem)
```

Dependency direction: `integrations/ → flow/ → agents/ → core/` (and `moa/ → agents/ → core/`).

- `core/` must not import from `agents/`, `moa/`, `flow/`, or `integrations/`
- `agents/` must not import from `moa/`, `flow/`, or `integrations/`
- `flow/` must not import from `moa/` or `integrations/`
- `moa/` only uses public APIs from `agents/` and `core/`
- Never reach into `_cell`, `_cell.actor`, or other private internals from outside the owning module — use factory patterns or messages instead

## MOA coding constraints

- `MoATree` and `MoANode` are `@dataclass(frozen=True)` — must remain immutable
- `MoANode.proposers` and `MoATree.nodes` are tuples, normalized in `__post_init__` (defensive copy)
- `ProposerSpec` is recursive: `type[AgentActor] | MoATree` (leaf or subtree)
- `min_success` invariants enforced at construction: `>= 1` and `<= len(proposers)`
- Validated semantics in `_run_proposers`: domain exceptions are recovered into failed `TaskResult`; system exceptions (`MemoryError`, `SystemExit`) propagate immediately
- `LayerOutput(result, directive)`: plain output resets directive; `LayerOutput` injects `{"input": current, "directive": directive}` into next layer
- `proposer_timeout` flows: `MoANode → ResolvedNode → context.ask(timeout=...)`

## Virtual Actor model (Orleans style)

Two kinds of actor coexist:

- **Declarative actors**: code-defined via `system.spawn()`, live with the process, recreated by code on restart
- **Virtual actors**: managed by `VirtualActorRegistry`, activated on demand, deactivated on idle, flat structure (no parent-child)

Design constraints:
- Virtual actors are flat — no parent-child relationships between virtual actors. Use registry ID-based addressing, not `context.spawn()`
- `context.spawn()` is for ephemeral child actors only (run and discard)
- Runtime does not manage state — `on_started`/`on_stopped` are business extension points
- Runtime does not recover dynamic topology — declarative actors are rebuilt by code, virtual actors reactivate on demand
- `RegistryStore` is pluggable — default in-memory, swap to Redis/DB for persistence across restarts

## Lifecycle guarantees

- `on_started` is guaranteed: actor does not start without it completing successfully
- `on_stopped` is guaranteed: protected by `asyncio.shield`, has timeout, retries once on `CancelledError`
- Only exception: process killed by OS (kill -9) — no user-space code can handle this
- Retry logic for `on_stopped` failures is the business's responsibility, not the runtime's

## Actor encapsulation

- Actors communicate only through messages — no shared mutable state
- Expose `ActorRef` to callers; never expose `_ActorCell`
- When an actor needs a dependency at construction time, use a class factory (see `make_collector_cls`) — not post-spawn attribute injection

## Let-it-crash

- `execute()` raises directly — do not catch exceptions to return error values
- Supervision (`supervisor_strategy`) handles restart/stop/escalate
- `join()` suppresses only `CancelledError`; actor teardown failures must propagate

## Fail-fast over deferred detection

- Invalid state at call boundaries raises immediately (duplicate `run_id`, stopped actor, etc.)
- `dispatch_parallel` cancels siblings on first failure — do not let expensive or irreversible side effects continue after a known error
- Cleanup is still guaranteed: `dispatch()` finally blocks run on `CancelledError`

## Strong typing

- Type parameters flow end-to-end: `Task[I]` → `execute(input: I) -> O` → `TaskResult[O]`
- Generic type arguments on public APIs must be preserved — do not widen to `Any` mid-chain
- Use sealed ADTs (`StreamItem = StreamEvent | StreamResult`) for sum types; supports exhaustive `match/case`
- Avoid `Any` except at genuine system boundaries (serialization, external APIs)
- Use `TYPE_CHECKING` blocks for imports that would cause circular dependencies; keep runtime imports lazy

## Streaming API boundaries

- `yield` is only meaningful inside `execute()` — lifecycle hooks (`on_started`, `on_stopped`, `on_restart`) are plain coroutines
- `emit_progress()` is for status/progress updates ("how is the task going")
- `yield` inside `execute()` is for output content ("here is the next chunk")
- These two are semantically distinct — do not mix them

## ContextVar for cross-cutting concerns

- Event sink (`_run_event_sink`) and active task ID (`_current_task_id_var`) propagate via `asyncio.create_task()` context copy — not as explicit parameters
- Use ContextVars for things that need to be invisible to the call chain but available deep in nested async tasks
- Set/reset ContextVars with `token = var.set(value)` / `var.reset(token)` in try/finally — never leave a ContextVar set after the scope ends

## Cleanup guarantees

- Ephemeral actors spawned by `dispatch()` or `dispatch_stream()` must be stopped in a `finally` block — no exceptions
- `ref.stop()` + `await ref.join()` is the canonical cleanup pair
- `dispatch_stream()` uses an async generator `try/finally` so cleanup runs even when the caller breaks early

## Minimal public surface

- `ActorContext` exposes only what actors need: `self_ref`, `parent`, `children`, `spawn`, `dispatch`, `dispatch_parallel`, `dispatch_stream`, `run_in_executor`
- Internal state (`_active_sink`, `_current_task_id`, `_stream`) is private to its module
- Do not add public attributes or methods to satisfy a single call site

## Async-native

- No blocking calls inside the actor loop — use `context.run_in_executor(fn, *args)` for blocking I/O or CPU-bound work
- Do not use `time.sleep` — use `asyncio.sleep`

## Lazy imports for circular dependency breaking

- `ref.py` is core; it cannot top-level import from `agents/`
- Import `agents/` types inside method bodies when needed:
  ```python
  async def ask_stream(self, ...):
      from everything_is_an_actor.agents.run_stream import RunStream, make_collector_cls
      ...
  ```
- This is a deliberate pattern — keep it consistent

## Tests validate behavior contracts, not implementation

- Test observable behavior: correct output, correct events, no hangs on shutdown
- Do not assert internal state (e.g., `_active_runs` dict contents) unless testing the specific invariant
- Concurrency and cleanup tests must use real `asyncio` — no mocking of `asyncio.wait`, `asyncio.gather`, or task scheduling
- A passing test that relies on mocked internals is worse than no test
- Every async test file **must** have `pytestmark = pytest.mark.anyio` at module level — without it, async tests silently fail with "async def functions are not natively supported"
- Do not use `pytest.mark.asyncio` — the project uses `anyio` as the async backend

## Progressive API

- Every new abstraction must have a zero-knowledge entry point (plain class with `execute()`)
- Upgrading from Level 1 → 4 must not require rewriting existing logic
- More power should require more explicit opt-in, not hidden defaults
