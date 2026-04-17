# CLAUDE.md

Coding principles for `everything-is-an-actor`. Follow these when writing or reviewing code in this repo.

---

## Layer separation

The codebase has five layers:

```
everything_is_an_actor.integrations  ← LLM adapters (LangChain, OpenAI, Anthropic)
everything_is_an_actor.flow          ← Flow ADT + categorical combinators + actor interpreter
everything_is_an_actor.moa           ← MOA pattern library (moa_layer, moa_tree, MoASystem)
everything_is_an_actor.agents        ← AI-specific (Task, AgentActor, streaming, A2A multi-turn)
everything_is_an_actor.core          ← generic actor runtime (Actor, ActorRef, ActorSystem)
```

Dependency direction: `integrations/ → flow/ → agents/ → core/` (and `moa/ → flow/ → agents/ → core/`).

- `core/` must not import from `agents/`, `moa/`, `flow/`, or `integrations/`
- `agents/` must not import from `moa/`, `flow/`, or `integrations/`
- `flow/` must not import from `moa/` or `integrations/`
- `moa/` imports from `flow/`, `agents/`, and `core/` — it is a pattern library on top of Flow
- Never reach into `_cell`, `_cell.actor`, or other private internals from outside the owning module — use factory patterns or messages instead

## Flow API coding constraints

- `Flow[I, O]` is a pure ADT — all variants are `@dataclass(frozen=True)`, no execution until interpreted
- Combinators build ADT nodes (data); the `Interpreter` gives them meaning against the actor runtime
- Method-chain API (`map`, `flat_map`, `zip`, `branch`, `recover`, etc.) returns new frozen nodes — never mutates
- `_Branch.mapping` is normalized to `MappingProxyType` in `__post_init__` — dict input is defensively copied
- `_ZipAll.flows` and `_Race.flows` are normalized to tuples in `__post_init__`
- Constructor functions (`agent`, `pure`, `race`, `zip_all`, `loop`, `at_least`) validate invariants at creation time (e.g., `zip_all` requires >= 2 flows)
- Serialization (`to_dict`/`from_dict`): only structural variants (no lambdas) are serializable; variants containing callables raise `TypeError`
- `at_least(n, *flows)` uses Validated semantics (accumulate errors), not fail-fast Either — domain exceptions go to `QuorumResult.failed`, system exceptions (`MemoryError`) propagate immediately
- The interpreter spawns ephemeral actors per `_Agent` node; cleanup is in `finally` blocks

## MOA coding constraints

MOA is a pattern library built on Flow — it uses `at_least`, `agent`, `pure`, `flat_map` from the Flow layer.

- `moa_layer()` composes: `pure(_inject_directive) → at_least(min_success, proposers) → agent(aggregator) → map(_extract_directive)`
- `moa_tree()` wraps input as `(input, None)`, chains layers via `flat_map`, unwraps final result
- `LayerOutput(result, directive)`: plain output resets directive; `LayerOutput` injects `{"input": current, "directive": directive}` into next layer
- `MoASystem` owns the full `ActorSystem → AgentSystem` lifecycle — a convenience wrapper, not a new runtime

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

## Exceptions must leave a trace — never silently swallowed

The first instinct in cleanup paths, fire-and-forget tasks, and user-callback
loops is `except Exception: pass` so the surrounding flow doesn't break. That
turns the exception into a black hole — operators have no way to know
something failed. **Catching is allowed; swallowing is not.**

- `except Exception: pass` is banned. Every catch must end with at least
  `logger.exception(...)` (preferred — captures stack), or a deliberate
  `logger.warning/error(...)` if the exception type is expected and the
  stack would be noise.
- `except BaseException` is almost always wrong. If you must catch
  `asyncio.CancelledError`, do it in a separate, explicit `except` arm
  (CancelledError is `BaseException` since 3.8 — bare `except Exception`
  doesn't catch it, and you don't want it to).
- Fire-and-forget tasks have no caller to receive the exception — the
  logger is the only signal channel. Use `logger.exception` so the
  traceback is preserved.
- Callback loops (dead-letter listeners, deactivation hooks, middleware)
  must isolate failures (one bad callback can't block the others) AND
  log them. Both, not either.
- The one exception is `join()` / similar "wait for completion" APIs that
  intentionally swallow `CancelledError` because the cancellation has
  already been handled upstream — those need an inline comment explaining
  *why* the swallow is correct.

If the catch is genuinely a no-op (e.g. cleaning up an already-cleaned
resource), write `# noqa: BLE001 — already cleaned` and an explanation,
not bare `pass`.

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
- Flow interpreter cleanup: each `_interpret_agent` spawns + stops in `try/finally`, then removes from `_root_cells`

## A2A multi-turn protocol

A2A support lives in `agents/` — no separate package. Two additions:

- `AgentCard`: frozen dataclass on `AgentActor.__card__` — static capability metadata (name, description, skills). Agents without `__card__` work as before, just not discoverable
- `discover_one(match)` / `discover_all(match)` on `AgentSystem`: match function receives full catalog (root + children, recursively) of `(ActorRef, AgentCard)` pairs, returns selection. Strategy is caller's — filter, score, LLM, anything

Multi-turn is a usage pattern, not a framework feature. Actor is already a state machine — `execute()` is called per message, `self` tracks conversation state, parent decides when the conversation is done via an ask loop. No new types, no new status, no framework involvement.

## Minimal public surface

- `ActorContext` exposes: `self_ref`, `parent`, `children`, `spawn`, `ask`, `sequence`, `traverse`, `race`, `zip`, `stream`, `dispatch`, `dispatch_parallel`, `dispatch_stream`, `run_in_executor`
- Internal state (`_active_sink`, `_current_task_id`, `_stream`) is private to its module
- Do not add public attributes or methods to satisfy a single call site

## Async-native

- No blocking calls inside the actor loop — use `context.run_in_executor(fn, *args)` for blocking I/O or CPU-bound work
- Do not use `time.sleep` — use `asyncio.sleep`

## Python compatibility floor: 3.12

`pyproject.toml` declares `requires-python = ">=3.12"`. Any contribution must
run on a stock 3.12 interpreter — do NOT use 3.13+-only features
(e.g. PEP 702 `@deprecated`, PEP 705 `ReadOnly` TypedDict, `copy.replace`,
the new free-threaded build flags).

3.12 features are in scope and may be used freely:
- PEP 695 generic syntax — `class Foo[T]:`, `def f[T](...)`, `type Alias = ...`
- PEP 692 `TypedDict` unpacking in `**kwargs`
- `typing.override` decorator
- `asyncio.TaskGroup`, `typing.Self`

Existing code uses the classic `TypeVar` / `Generic[T]` / `Protocol[T]` forms
for historical reasons; both styles are equivalent at runtime, mix freely.
Do not run a stylistic migration just to switch forms.

## Dispatcher is for blocking work only

`Dispatcher` (and `PoolDispatcher`) exist solely to offload **blocking sync
work** to a thread pool. Combining a dispatcher with an **async** handler is
conceptually incoherent and the runtime rejects it at `spawn` time
(`ValueError: async on_receive ... but was spawned with dispatcher`).

- Sync actor (`def receive`) + dispatcher: receive is called inside the pool.
  This is the intended use.
- Sync actor, no dispatcher: receive is auto-offloaded via the default executor.
- Async actor (`async def on_receive`), no dispatcher: handler runs on the
  caller loop. No offloading needed — async handlers don't block.
- Async actor + dispatcher: **rejected**. Bridging async handlers into a
  thread pool requires a throwaway event loop per message, which breaks
  any cross-loop object the handler touches (ContextVars, registered
  futures, sink propagation). If the async handler needs to offload
  blocking work within its body, use `context.run_in_executor(fn, *args)`.

The same rule extends to `AgentActor` — async `execute()` + dispatcher is
rejected. Implement sync `receive()` on the subclass if the handler is
genuinely blocking.

## ComposableFuture contracts

`ComposableFuture` is eager by default — Scala `Future` semantics. Constructing
one from a coroutine in an async context immediately schedules it as an
`asyncio.Task`; the effect is on the wire, not queued. In a sync context (no
running loop) the coroutine is retained and scheduled on first `await` /
`result()`. Do not rely on lazy-description semantics: there is no point at
which `ComposableFuture(coro)` "hasn't run yet" inside an async function.

### `race` vs `first_success` — named for their true semantics

- `ComposableFuture.race(...)` and `ActorContext.race(...)`:
  **first-wins, outcome-agnostic**. Whichever branch finishes first —
  success or failure — determines the result. Losers are cancelled. Aligns
  with Scala `Future.firstCompletedOf` and `cats.effect.IO.race`.
- `ComposableFuture.first_completed(...)` and `ActorContext.first_success(...)`:
  **success-biased**. Failures are skipped; the call waits for any
  success to arrive, and only raises when every branch has failed.

Pick the one whose semantics match the call site. Do not fall back to `race`
just because "we want whichever finishes first" — if a fast failure would
be a wrong answer, you want `first_success`.

### `eager` vs `fire_and_forget` — cancel handle is not optional

- `ComposableFuture.eager(coro, *, name)` returns an `EagerHandle` with
  `.future` and `.cancel`. Keep both alive; dropping the handle drops the
  interrupt channel. Tuple unpacking `fut, cancel = Cf.eager(coro)` still
  works for the common `_run_future, _cancel_run = ...` pattern.
- `ComposableFuture.fire_and_forget(coro, *, name)` is the explicit
  "I give up interrupt control" API. Use it when the task must run to
  completion regardless — cleanup watchers, background event drainers,
  side-effect logging. The name documents intent at the call site.

Do **not** use `eager` and then ignore the return value. That pattern
silently lost the cancel callable before the split; it now leaves the
caller holding an `EagerHandle` they never inspect, which is equally
useless. `fire_and_forget` says what you mean.

### `promise()` — cross-loop safe for resolve/reject, not for await

`ComposableFuture.promise()` returns `(cf, resolve_fn, reject_fn)`. The
resolve/reject callables are **thread- and loop-safe** (they hop back to
the owner loop via `call_soon_threadsafe`). The returned `cf` must be
**awaited on the same loop that called `promise()`** — the underlying
`asyncio.Future` is owner-loop-bound. Cross-loop `await` is rejected with
an explicit `RuntimeError`, not silent breakage.

Use this pattern to bridge events from outside the owner loop (worker
threads, MQ listeners, other subsystems) into code that awaits on the
owner loop — never the reverse.

## Upward extension from core: use hooks and adapters, not imports

`core/` must have zero imports from `agents/`, `flow/`, `moa/`, `integrations/`
— eager **or** lazy. A method-body `from everything_is_an_actor.agents import ...`
is still a reverse dependency; it only postpones the failure to runtime. Do not
reintroduce it.

When `core/` behaviour must be parameterized by a higher layer, prefer:

1. **Class-level hooks on `Actor`** — the subclass provides the specialization.
   - `Actor.__validate_spawn_class__(cls, *, mode)` — per-class spawn-time validation
     (`AgentActor` uses this to enforce async `execute()`).
   - `Actor.__wrap_traverse_input__(cls, inp)` — per-class input lifting
     (`AgentActor` wraps as `Task(input=inp)` so `ActorContext.traverse` stays in core).

2. **Adapter protocols injected on `ActorSystem`** — higher layers install a
   concrete implementation.
   - `StreamAdapter` (defined in `core/ref.py`) is the streaming hook.
     `ActorRef._ask_stream` delegates to `self._cell.system._stream_adapter`;
     `AgentSystem.__init__` installs `AgentStreamAdapter` which knows about
     `Task` / `StreamEvent`.

Why this pattern beats lazy imports:
- `core/` stays independently importable, testable, and distributable
- The dependency direction in the type system matches the dependency direction
  in the runtime — no hidden reverse edges
- Adapters are swappable (e.g. a distributed `StreamAdapter` backed by MQ)
- Hooks make the extension point explicit in the `Actor` contract

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

## Documentation

- All user-facing docs are in English
- Diagrams use Mermaid format with Morandi color palette
- Doc site config: `mkdocs.yml` with Material theme
- All new features must have a corresponding doc page in `docs/` and be added to the `mkdocs.yml` nav
