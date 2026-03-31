# CLAUDE.md

Coding principles for `actor-for-agents`. Follow these when writing or reviewing code in this repo.

---

## Layer separation

The codebase has two independent layers:

```
actor_for_agents.agents   ← AI-specific (Task, AgentActor, streaming)
actor_for_agents          ← generic actor runtime (Actor, ActorRef, ActorSystem)
```

- `actor_for_agents` must not import from `actor_for_agents.agents`
- `actor_for_agents.agents` only uses public APIs from the core layer
- Never reach into `_cell`, `_cell.actor`, or other private internals from outside the owning module — use factory patterns or messages instead

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
      from actor_for_agents.agents.run_stream import RunStream, make_collector_cls
      ...
  ```
- This is a deliberate pattern — keep it consistent

## Tests validate behavior contracts, not implementation

- Test observable behavior: correct output, correct events, no hangs on shutdown
- Do not assert internal state (e.g., `_active_runs` dict contents) unless testing the specific invariant
- Concurrency and cleanup tests must use real `asyncio` — no mocking of `asyncio.wait`, `asyncio.gather`, or task scheduling
- A passing test that relies on mocked internals is worse than no test

## Progressive API

- Every new abstraction must have a zero-knowledge entry point (plain class with `execute()`)
- Upgrading from Level 1 → 4 must not require rewriting existing logic
- More power should require more explicit opt-in, not hidden defaults
