# aktor — Python Integration Boundary: In-Process vs Out-of-Process

**Status**: Draft — decision pending
**Date**: 2026-04-11
**Depends on**: [`2026-04-11-aktor-rust-repo-design.md`](./2026-04-11-aktor-rust-repo-design.md)
**Decision owner**: user
**Scope**: choose how the Rust `aktor` runtime invokes Python-implemented
business tasks, and sketch the resulting architecture.

---

## 1. Context

The `aktor` Rust scaffold was originally designed as a standalone native
Rust actor framework. The intended deployment, clarified after
brainstorming, is different:

- `aktor` is **multi-tenant cloud infrastructure** — the actor runtime,
  Flow interpreter, virtual-actor registry, scheduler, and supervision.
- **Business logic is Python** — task implementations, prompt templates,
  LLM provider SDKs, tool calls. Users write Python; `aktor` invokes
  their Python code from the Rust runtime.

This document picks the boundary that sits between those two layers. The
choice determines deployment topology, failure isolation, throughput
ceiling, observability story, and the shape of the Python SDK. It must
be made before any real business-logic code crosses the boundary.

---

## 2. Goals & Non-Goals

### 2.1 Goals

1. Enable user-authored Python tasks to be invoked from Rust actors as
   if they were ordinary `Task` implementors.
2. Preserve `aktor`'s concurrency ceiling (100K+ virtual actors per
   runtime instance) to the extent the Python layer allows.
3. Isolate Python crashes from the Rust runtime. A panicking or
   deadlocked Python task must not take down the tenant, and must not
   take down the runtime itself.
4. Support multi-tenant deployments where different tenants run
   different Python code bases, possibly with different dependencies.
5. Allow hot-update of Python business code without restarting the Rust
   runtime (ideally without even draining actors).
6. Be observable — per-tenant, per-task, per-actor metrics that show
   where time is spent on both sides of the boundary.

### 2.2 Non-Goals

- Zero-copy sharing of large tensors between Rust and Python. (Ray's
  Plasma store exists for this reason; aktor does not need it in v1.)
- Supporting languages other than Python in the SDK layer. Polyglot
  support is a future concern, not a v1 constraint.
- Replacing or wrapping existing Python frameworks (LangChain,
  LlamaIndex). Users keep their preferred tools; aktor only controls
  the runtime boundary.

---

## 3. Two Candidate Architectures

The decision space is essentially binary: in-process or out-of-process.

### 3.1 Route A — In-Process: PyO3 + Sub-Interpreters

```
┌────────────────────────────────────────────────────────────┐
│  aktor runtime process                                     │
│                                                            │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  tokio runtime (Rust)                                │  │
│  │   ├─ ActorSystem                                     │  │
│  │   ├─ Flow interpreter                                │  │
│  │   └─ Actor tasks  ◄──┐                               │  │
│  └──────────────────────┼───────────────────────────────┘  │
│                         │ PyO3 call (acquire GIL)          │
│  ┌──────────────────────▼───────────────────────────────┐  │
│  │  Embedded CPython                                    │  │
│  │   ├─ Sub-interpreter pool (one per tenant?)          │  │
│  │   ├─ User Task modules loaded via importlib          │  │
│  │   └─ Task.execute(input) returns output              │  │
│  └──────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────┘
```

- **Bridge**: PyO3 crate, same process, shared address space.
- **Invocation cost**: acquire GIL (or per-interpreter GIL on 3.12+) →
  call Python function → convert return value → release GIL.
  Order of magnitude: ~1–10 µs per call on a warm path.
- **Serialization**: direct Python object conversion through PyO3
  traits; no wire format needed.
- **Isolation primitive**: Python sub-interpreters (PEP 684,
  stabilized 3.12). Each tenant gets its own interpreter with its own
  GIL.
- **Failure mode**: Python exceptions propagate as Rust `Result::Err`.
  A Python `SIGSEGV` or hard crash takes down the whole `aktor`
  runtime process.
- **Deployment**: single Rust binary with embedded CPython. Python
  dependencies installed into a bundled virtual environment.
- **Hot update**: `importlib.reload()` on a sub-interpreter. Brittle,
  leaks, but works for small modules.

#### Route A strengths

1. **Lowest possible latency.** Per-call overhead is 1–2 orders of
   magnitude smaller than any IPC alternative.
2. **Zero serialization cost.** Objects pass by PyO3 pointer conversion,
   not through a wire format.
3. **Simplest deployment story.** One process, one binary, one container
   image. Operators love this.
4. **Unified tracing.** A single tokio trace span spans both Rust and
   Python code paths without span-propagation plumbing.

#### Route A weaknesses

1. **GIL is a hard ceiling on Python-side parallelism.** Even with
   per-interpreter GIL (3.12+), each sub-interpreter executes Python
   bytecode serially. A runtime with 32 CPU cores and 1000 actors, all
   of which are Python-bound, can only run as many concurrent Python
   calls as there are sub-interpreters. Tokio's 32-worker scheduler is
   wasted on Python-heavy workloads.
2. **Python crashes kill the runtime.** A C-extension bug in a user's
   dependency (numpy, torch, rare native extension) takes down every
   tenant in the process. This is a cross-tenant blast radius that
   violates multi-tenant isolation requirements.
3. **Sub-interpreter support is fragile.** As of CPython 3.13, many
   popular C extensions do not yet handle per-interpreter state
   correctly. `numpy` and `torch` are high-risk. This is improving but
   not solved.
4. **Hot reload is unreliable.** `importlib.reload` does not invalidate
   class instances, closures, or anything that holds a reference to the
   old module. Practical hot update requires subprocess restart, which
   defeats the in-process design.
5. **Dependency hell is user-visible.** Different tenants with different
   `torch` versions cannot coexist — sub-interpreters share the
   filesystem-level `site-packages`.
6. **Cargo test story is painful.** Every test binary that touches the
   boundary embeds CPython, making CI slow and environment-sensitive.

### 3.2 Route B — Out-of-Process: Python Workers over gRPC / Unix Socket

```
┌────────────────────────────────────┐     ┌─────────────────────────┐
│  aktor runtime process (Rust)      │     │  Python worker process  │
│                                    │     │  (one per tenant, pool) │
│  ┌────────────────────────────┐    │     │                         │
│  │  tokio runtime             │    │     │  ┌─────────────────┐    │
│  │   ├─ ActorSystem           │    │     │  │  aktor-py-sdk   │    │
│  │   ├─ Flow interpreter      │    │     │  │   ├─ @task      │    │
│  │   └─ Actor tasks           │    │     │  │   └─ worker loop│    │
│  │         │                  │    │     │  └────────┬────────┘    │
│  │         ▼                  │    │     │           │             │
│  │  PythonTaskProxy           │    │     │           ▼             │
│  │         │                  │    │     │  User task modules      │
│  │         ▼                  │    │     │  (LangChain, OpenAI,    │
│  │  Bridge client             │◄───┼─────┼──► Bridge server        │
│  │  (tonic gRPC or UDS)       │    │     │                         │
│  └────────────────────────────┘    │     │                         │
└────────────────────────────────────┘     └─────────────────────────┘
```

- **Bridge**: gRPC over Unix Domain Socket (UDS) on the same host;
  optionally TCP for cross-host scheduling.
- **Invocation cost**: serialize input → UDS round-trip → deserialize
  output. ~50–200 µs per call on warm UDS; ~500 µs–2 ms on TCP
  loopback. **Compare to the ~500 ms P50 of any real LLM call.**
- **Serialization**: protobuf (tonic) with opaque `bytes` payload
  fields for user-defined types that the runtime does not need to
  introspect.
- **Isolation primitive**: OS process. One Python worker process per
  tenant (or pool of workers per tenant). Linux cgroups / namespaces
  provide resource enforcement.
- **Failure mode**: Python crash kills the worker process only. The
  runtime receives a `TaskError::WorkerDied` and either retries on
  another worker in the pool or surfaces the error through supervision.
  Blast radius is one worker.
- **Deployment**: Rust runtime binary + per-tenant Python worker
  container image. In managed-service deployment, workers are managed
  by the runtime's control plane; in self-hosted, by the operator.
- **Hot update**: replace the worker binary / image and recycle workers
  in the pool. This is industry standard (Temporal, Celery, Sidekiq).

#### Route B strengths

1. **True Python parallelism.** Each worker process has its own GIL.
   A tenant with 10 worker processes runs 10 Python tasks concurrently,
   scaling linearly with worker count until the LLM RTT dominates.
2. **Blast radius = 1 worker.** Python crash, OOM, stuck thread, bad
   C-extension: the worker dies, the runtime recycles it, other tenants
   are unaffected.
3. **Rock-solid hot update.** Rolling replacement of workers in a pool
   is a well-understood operational pattern. Users deploy new Python
   code by pushing a new worker image.
4. **Dependency isolation by default.** Each tenant's worker image has
   its own `site-packages`. `torch==2.1` vs `torch==2.3` coexist without
   conflict.
5. **Language-neutral protocol.** The same bridge can later be used by a
   Go SDK, a TypeScript SDK, etc. Polyglot is a future option, not a
   rewrite.
6. **Clean separation for testing.** `aktor-core` tests never touch
   Python. Integration tests start a Python worker and talk to it
   through the bridge — isolated, deterministic, reproducible.
7. **Observability is explicit.** Every call crosses a protobuf
   boundary with a trace context header; distributed tracing falls out
   of the design instead of being bolted on.

#### Route B weaknesses

1. **Higher per-call latency.** 50–200 µs vs ~1 µs. For workloads where
   Python calls dominate and each call is tiny, this is real. For LLM
   agent workloads where each task makes at least one network call to a
   model provider, the overhead is in the noise.
2. **More moving parts.** The runtime now has to supervise Python
   worker processes, not just actor threads. This is non-trivial
   engineering — but it is exactly the problem Temporal and Celery
   solved, and there is plenty of prior art.
3. **Protobuf schema evolution.** Adding fields to the bridge protocol
   requires thought. Breaking changes are worse than in-process
   refactoring.
4. **Serialization format for user types.** The bridge speaks protobuf
   for runtime messages, but user task inputs/outputs are arbitrary
   Python objects. The SDK must pick a codec. Pydantic + JSON is the
   clean choice — it forces schema-first contracts and matches the
   Rust type system's rigor. Legacy Python binary serialization
   formats are considered unsafe for untrusted payloads and should
   not be the default.
5. **Two processes to deploy and monitor.** Operators who expect a
   single binary will find this heavier. Single-binary deployment
   remains possible for self-hosted small scale by bundling a supervisor.

### 3.3 Side-by-side summary

| Dimension | Route A (in-process PyO3) | Route B (out-of-process workers) |
|---|---|---|
| Per-call latency | ~1–10 µs | ~50–200 µs |
| Python parallelism | GIL-limited | Linear in worker count |
| Blast radius on crash | Entire runtime | One worker |
| Hot reload | Brittle | Standard rolling update |
| Multi-tenant isolation | Weak (shared process) | Strong (separate processes) |
| Dependency coexistence | Single `site-packages` | Per-tenant images |
| Deployment complexity | One binary | Runtime + worker processes |
| Serialization overhead | None | protobuf + user codec |
| Observability | Shared trace | Distributed trace (explicit) |
| CI / test story | Slow, environment-sensitive | Isolated, reproducible |
| Prior art | PyO3 itself, maturin, tokio bindings | Ray workers, Temporal, Celery, Sidekiq, Modal |
| Failure at scale | Correlated (process-global) | Independent (per worker) |

---

## 4. Recommendation

**Choose Route B — out-of-process Python workers over gRPC (UDS for
local, TCP for cross-host).**

The reasoning is dominated by three factors, in order:

1. **LLM workloads absorb IPC cost.** The ~100 µs of serialization
   overhead is three orders of magnitude smaller than a single LLM
   provider round-trip. A Python agent that makes even one LLM call
   per task cannot tell the difference between Route A and Route B.
   The "latency advantage" of Route A is purely theoretical for the
   target workload.

2. **Multi-tenant isolation is a hard requirement, not a feature.** A
   cloud infrastructure platform that shares a single process across
   tenants is one numpy bug away from a cross-tenant outage. The Route
   A blast radius is unacceptable for the stated positioning.

3. **Operational sanity.** Hot update, dependency isolation, and
   observability are not nice-to-haves — they are daily operational
   needs. Route B gets all three by construction; Route A fights them.

The one case where Route A would win is a workload where Python tasks
are CPU-bound micro-operations (<1 ms) with no LLM calls. That is not
the target workload for aktor.

### 4.1 Route B details worth pinning now

These choices follow from picking Route B and should be captured in the
next spec that describes the bridge itself:

- **Transport**: gRPC (tonic) over Unix Domain Socket for local worker
  pools; TCP for cross-host. `hyper-util::UnixConnector` on the Rust
  side, `grpc.aio` insecure channel on the Python side.
- **Task user payload codec**: default to **Pydantic BaseModel +
  JSON**. The "schema-first" discomfort is a feature, not a bug — it
  makes task contracts explicit and matches the Rust type system's
  rigor. Legacy Python binary formats are explicitly excluded for
  safety reasons.
- **Worker lifecycle**: supervised by the Rust runtime via a
  `WorkerSupervisor` actor. Policies: `min_workers`, `max_workers`,
  idle timeout, restart-on-crash with back-off, graceful drain on
  deploy.
- **Worker registration**: workers call the runtime's
  `Register(worker_id, supported_tasks, capability_card)` RPC on start.
  Runtime maintains a registry and routes `PythonTaskProxy` invocations
  to the appropriate worker pool.
- **Trace propagation**: W3C trace-context injected into every bridge
  call; Python SDK re-emits it through `opentelemetry-python` into the
  user's observability stack.

---

## 5. Impact on the Current Scaffold

The `v0.0.1-scaffold` repository at `~/IdeaProjects/aktor/` was written
before this positioning was clear. Route B selection implies the
following changes, to be executed in follow-up work (not in this spec):

1. **New crate**: `aktor-protocol` — protobuf definitions, tonic
   codegen, shared `ProtoError` types. No business logic.
2. **New crate**: `aktor-bridge` — Rust side of the bridge. tonic client
   stubs, worker-pool management, `PythonTaskProxy` that implements
   `Task` by dispatching over the bridge.
3. **Revised crate**: `aktor-agents` — `Task` stays, but the canonical
   implementor becomes `PythonTaskProxy`. System-level Rust tasks
   remain possible but are the exception.
4. **Revised crate**: `aktor-integrations` — original "Rust-native LLM
   provider adapters" scope is deleted. The crate slot is kept for
   infrastructure-level integrations (observability exporters, registry
   backends, metric sinks). Final scope: TBD.
5. **New repository**: `aktor-python` — the Python SDK. `@aktor.task`
   decorator, worker entry point, protobuf Python codegen,
   `AgentCard` publisher, Pydantic type helpers. Apache 2.0 licensed
   (SDK is community layer, not BSL).
6. **New repository**: `aktor-proto` — the canonical `.proto` files,
   versioned independently so both `aktor` and `aktor-python` depend
   on the same source of truth. Apache 2.0 (protocol is community
   layer).

None of this invalidates the existing scaffold — it is all additive.
The current five crates keep their purposes, with the above revisions
to `agents/` and `integrations/` applied in-place.

---

## 6. Open Questions

1. **Sub-interpreter fallback for single-tenant dev mode?** A
   stripped-down in-process mode would give developers a fast iteration
   loop without running a separate worker process. Feasible as an
   optional feature flag, non-goal for production. Decision: defer to
   developer-experience phase.
2. **How does the worker discover user task modules?** Options:
   entry-point plugin system (setuptools entry_points), env var
   `AKTOR_TASK_MODULES`, config file. Decision: prefer explicit config
   file so the runtime can surface a "what tasks does this worker
   expose" introspection without importing anything.
3. **What happens to a stuck worker?** Bridge RPC timeouts kill the
   worker process with SIGKILL after a configurable deadline. The
   Task call returns `Error::Timeout`. Supervision decides retry/stop.
4. **Do we need Plasma-style shared memory for large objects?** Not
   in v1. Revisit when a user actually complains about moving
   100 MB tensors across the bridge.
5. **What about Python workers on different hosts?** TCP-mode gRPC
   supports this; it is a deployment configuration question, not a
   protocol question. Decision: support from day one in the protocol,
   defer deployment tooling.

---

## 7. Decision Gate

This spec does not bind the decision. The user reviews it, confirms
Route B (or overrides to Route A with new rationale), and the next step
is to produce the concrete protocol and crate-level design documents
named in §5.

Once the decision is made, update the `aktor` repository's `README.md`
"Status" section and `CLAUDE.md` "Positioning" section to remove the
"integration boundary under review" language.
