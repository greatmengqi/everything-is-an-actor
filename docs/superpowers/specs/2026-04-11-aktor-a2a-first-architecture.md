# aktor — A2A-First Architecture

**Status**: Draft
**Date**: 2026-04-11
**Supersedes**: [`2026-04-11-aktor-python-integration-design.md`](./2026-04-11-aktor-python-integration-design.md)
**Depends on**: [`2026-04-11-aktor-rust-repo-design.md`](./2026-04-11-aktor-rust-repo-design.md)
**Scope**: establish the A2A protocol as aktor's primary data-plane boundary,
collapse the Rust↔Python integration question into a special case of
A2A-client/A2A-server, and update the runtime's positioning accordingly.

---

## 1. The Framing Shift

Earlier design work treated aktor as a platform with **two tightly
coupled layers**:

- A Rust runtime (actor system, scheduler, Flow interpreter)
- A first-party Python SDK + custom RPC bridge for business logic

Under this framing, the critical architectural question was **"how
should the Rust runtime invoke Python tasks?"**, and a whole spec was
written to evaluate in-process (PyO3) vs out-of-process (private
protobuf over UDS) bridges.

**That framing is wrong.** It is one abstraction layer too low.

Once distributed multi-agent integration is taken seriously — cross
host, cross runtime instance, cross organization, cross framework — it
becomes clear that **every agent-to-agent boundary is a network
boundary**, and the industry has standardized on Google's A2A protocol
(HTTP + JSON-RPC + `AgentCard` discovery, published November 2024) for
that boundary. Under an A2A-first framing:

- The "first-party Python worker" and "third-party external agent"
  distinction disappears. Both are A2A servers; aktor is the A2A
  client in both cases.
- aktor does **not** need a private Rust↔Python bridge. The bridge is
  A2A, the same protocol used for every other agent-to-agent call.
- Python becomes **one of many** possible agent implementation
  languages, not the privileged one. Any A2A-compliant agent framework
  (LangGraph, AutoGen, CrewAI, bare FastAPI) is a first-class
  participant from day one.
- The Python SDK downgrades from "required protocol shim" to
  "optional ergonomic wrapper over A2A."

This spec captures the A2A-first architecture that results.

---

## 2. Positioning (Third Revision)

> **aktor is a Rust-based A2A orchestration runtime with Orleans-style
> virtual actors, Flow combinators, and source-available licensing.**
>
> aktor talks A2A on both sides: consuming external A2A agents as
> `Flow::Agent` nodes, and exposing managed agents as A2A HTTP
> endpoints. A Python SDK is provided for convenience, but any
> A2A-compliant agent framework works out of the box. You do not need
> to depend on any aktor library to use the runtime — you only need to
> speak A2A.

Previous positioning revisions and what was wrong with each:

| Revision | Framing | Problem |
|---|---|---|
| v1 (original spec) | "Native Rust actor framework" | Treated aktor as a user-facing Rust library; ignored that business logic is not Rust |
| v2 (cloud infra spec) | "Rust runtime + Python business layer" | Still treated Rust↔Python as a tightly coupled, privileged pair; required a private RPC bridge |
| **v3 (this spec)** | **"A2A orchestration runtime, polyglot by default"** | **Python is one of many A2A agent languages; no privileged bridge** |

---

## 3. Goals & Non-Goals

### 3.1 Goals

1. Make A2A the **uniform** data-plane protocol for all agent-to-agent
   communication that crosses an actor boundary — whether the callee
   is a first-party aktor-managed agent, an external third-party
   agent, or another aktor runtime in a cluster.
2. Eliminate the need for a custom Rust↔Python RPC protocol. Any
   gains there are re-framed as *optimizations of A2A transport*, not
   as a separate protocol layer.
3. Keep a clean separation between **data plane** (A2A: calling
   agents) and **control plane** (managing agent lifecycle, resource
   quotas, deployment). Define the control plane boundary explicitly
   rather than bolting it onto A2A as an afterthought.
4. Preserve aktor's differentiated runtime value (virtual actors,
   Flow ADT, supervision, BSL protection) while giving up the "custom
   SDK lock-in" that Ray and Modal rely on.
5. Make the integration with MCP (Model Context Protocol) orthogonal
   and clean — MCP is for agent↔tool calls, not agent↔agent, and does
   not compete with A2A.

### 3.2 Non-Goals

- Reimplementing A2A. aktor adopts the published spec as-is. Any
  extensions are additive headers or optional fields, not forks of
  the protocol.
- Defining a new polyglot SDK protocol. A2A + AgentCard is the
  polyglot interface. Per-language SDKs are conveniences, not
  protocol definitions.
- Supporting arbitrary non-A2A agent protocols in v1. OpenAI
  Assistants API, LangChain tool protocol, custom JSON-RPC dialects,
  and so on can be handled by user-written adapter agents (a thin
  A2A agent that translates to the foreign protocol). They are not
  first-class runtime concerns.
- Building a Plasma-style shared-memory object store for large
  tensors. A2A carries JSON; bulk data goes via URIs (S3, GCS, local
  blob store) referenced in the JSON payload.

---

## 4. The Uniform A2A Boundary

### 4.1 All crossings are A2A

```
┌────────────────────────────────────────────────────────────────┐
│  aktor runtime process (Rust, tokio)                           │
│                                                                │
│    ActorSystem, Flow interpreter, virtual actor registry       │
│              │                                                 │
│              ▼                                                 │
│        ┌────────────────────┐                                  │
│        │  A2A client        │   (consumes A2A agents)          │
│        └─┬──────────────────┘                                  │
│          │                                                     │
│        ┌─▼──────────────────┐                                  │
│        │  A2A server        │   (exposes managed agents)       │
│        └────────────────────┘                                  │
│              ▲                                                 │
└──────────────┼─────────────────────────────────────────────────┘
               │  HTTP / JSON-RPC (A2A)
      ┌────────┼────────────────┬──────────────┬──────────────┐
      │        │                │              │              │
      ▼        ▼                ▼              ▼              ▼
┌─────────┐ ┌─────────┐   ┌──────────┐  ┌──────────┐  ┌──────────┐
│ Python  │ │ Python  │   │ LangGraph│  │ External │  │ Another  │
│ worker  │ │ worker  │   │ / AutoGen│  │ 3rd-party│  │ aktor    │
│ (1st    │ │ (1st    │   │ (user's  │  │ A2A      │  │ runtime  │
│  party) │ │  party) │   │  choice) │  │ vendor)  │  │ (cluster)│
└─────────┘ └─────────┘   └──────────┘  └──────────┘  └──────────┘
```

Every arrow is A2A. The **protocol** is the same. The **transport**
may differ: Unix Domain Socket for local workers (fast), plain HTTP
for remote or cross-org (portable), optionally gRPC as a binary
transport for performance-critical paths (still A2A semantics, just
encoded differently).

### 4.2 What changes vs. the previous design

| Aspect | Previous design (superseded) | A2A-first design |
|---|---|---|
| Rust↔Python protocol | Custom protobuf over UDS | A2A over UDS (HTTP/JSON) |
| First-party vs third-party distinction | Separate code paths | Same code path |
| Python SDK | Required protocol shim | Optional ergonomic wrapper |
| Protocol surface | Private to aktor | Published, polyglot, community-governed |
| Framework lock-in | User must use aktor-python | User can use anything that speaks A2A |
| Node-to-node cluster protocol | TBD | A2A (with cluster-extension headers) |
| Vendor interop | Needs per-vendor adapter | Free (if they speak A2A) |

### 4.3 The AgentCard as the single contract type

The `AgentCard` struct already present in the scaffold is the pivot.
It was originally designed for intra-runtime capability discovery;
under A2A-first, it is **literally the A2A protocol's AgentCard**,
serialized as JSON at `GET /.well-known/agent.json` on every
aktor-exposed agent.

Scaffold change (small, immediate):

- `aktor_agents::AgentCard` must implement `serde::Serialize +
  serde::Deserialize`
- Its JSON shape must match the A2A spec's AgentCard schema field for
  field (name, description, version, capabilities, auth, endpoints, …)
- Non-A2A fields the runtime needs internally (e.g., tenant ID,
  internal routing hints) go in a separate `AgentMetadata` struct,
  never leaked across the A2A boundary

---

## 5. Data Plane vs Control Plane

A2A is a **data plane** protocol. It answers "how do I invoke an
agent?" It does not answer operational questions like "how does aktor
deploy, scale, health-check, drain, hot-restart a pool of Python
workers it manages?"

aktor needs a control plane for its first-party managed agents, and
nothing else (external A2A agents are managed by their owners).

### 5.1 Control plane design

Two viable approaches. aktor picks **Approach 2** (A2A-extension).

**Approach 1: Separate admin protocol** — a small gRPC or HTTP admin
API on the runtime side, spoken by a `aktor-py-sdk` worker loop for
registration, health, drain. Simpler to implement, but introduces a
second protocol to learn and version.

**Approach 2 (chosen): A2A lifecycle extension** — managed workers
expose an additional `__lifecycle__` capability in their AgentCard,
with standard methods `ping()`, `drain()`, `shutdown()`, `reload()`.
aktor's control plane calls these through the same A2A channel that
carries user task traffic, distinguished by the capability name.

Advantages of Approach 2:

- One protocol, one library, one set of observability primitives
- Non-first-party agents can opt into lifecycle participation by
  declaring the capability; third-party agents that do not expose it
  simply are not managed by aktor, which is the correct default
- "Everything is an agent" is preserved uniformly, matching the Python
  repository's guiding metaphor

### 5.2 What is still first-party

Even under A2A-first, aktor maintains first-party responsibility for:

- **Scheduling** — which A2A agent handles a given virtual actor
  invocation (locality, sticky session, load balancing)
- **Virtual actor state** — the per-actor state that the registry
  persists across activations; A2A calls carry state references, not
  state itself
- **Supervision** — what happens when a downstream A2A agent
  misbehaves (timeout, 5xx, crash): restart it via the lifecycle
  extension, route around it, escalate to a supervisor strategy
- **Flow orchestration** — the interpretation of `Flow<I, O>` ADT into
  a sequence of A2A calls, with combinator semantics (race, zip_all,
  at_least, recover) enforced at the runtime layer, not at each agent
- **Observability pipeline** — trace context injection, metrics
  aggregation, structured logs
- **Quotas and multi-tenancy** — per-tenant isolation, rate limits,
  fair scheduling

None of this is visible in the A2A wire. All of it is aktor's
differentiation and justifies the BSL 1.1 license.

---

## 6. MCP: Orthogonal, Not Competing

Model Context Protocol (MCP, Anthropic, November 2024) is sometimes
confused with A2A. They solve different problems:

| | A2A | MCP |
|---|---|---|
| Purpose | Agent↔agent | Agent↔tool / Agent↔data source |
| Transport | HTTP + JSON-RPC | stdio + JSON-RPC, or SSE |
| Discovery | `/.well-known/agent.json` | MCP server declares capabilities |
| Typical callee | Another reasoning agent | File system, database, API, search engine |
| Stateful | Yes (conversation-level) | Mostly request-scoped |

In aktor:

- **A2A** is the runtime concern. `aktor-a2a` is a core crate, speaks
  both client and server sides.
- **MCP** is a business-layer concern. It lives in the user's Python
  agent code, exposed via the Python SDK as a helper but not a
  runtime primitive. The runtime does not care that a given Python
  agent uses MCP internally to query a vector database.

Practical consequence: aktor gains MCP support "for free" the moment
the Python SDK adds a `aktor.mcp.connect(...)` helper. No runtime
changes, no new crates, no protocol implementation.

---

## 7. Impact on the Scaffold

The five-crate skeleton committed as `v0.0.1-scaffold` remains valid
under A2A-first. The changes required are incremental, not
structural.

### 7.1 Immediate scaffold adjustments (safe, no business logic)

1. **`aktor_agents::AgentCard` gains `serde::Serialize +
   serde::Deserialize`.** Add `serde = { workspace = true }` to
   `agents/Cargo.toml`. The AgentCard struct gains
   `#[derive(Serialize, Deserialize)]`. Field names match A2A JSON
   schema (snake_case).

2. **`aktor_agents::Task` documentation updated** to note that the
   canonical implementor is an `A2aAgentProxy` that invokes a remote
   A2A agent via HTTP, not a direct Rust task nor a custom RPC proxy.
   Rust-native Task impls remain possible for system-level tasks but
   are the exception.

3. **`aktor_flow::AgentFn` is re-explained** as the type-erased
   boundary that holds "something that takes `I` and returns
   `BoxFuture<Result<O>>`". In production, that "something" is an
   A2A call; in tests, it can be an inline closure.

### 7.2 New crate to add (next phase, not in scaffold)

| Crate | Purpose | License |
|---|---|---|
| `aktor-a2a` | A2A client + server, AgentCard serialization, `/.well-known/agent.json` handler, `A2aAgentProxy: Task` | BSL 1.1 |

This replaces the planned `aktor-protocol` and `aktor-bridge` crates
from the superseded spec. One crate, not two, because A2A is one
protocol, not a layered stack.

### 7.3 Crates that change meaning (same slots, different scope)

| Crate | Before | After |
|---|---|---|
| `aktor-agents` | "Task, AgentActor, streaming, A2A discovery" | "Task contract (implemented primarily by A2aAgentProxy), streaming event types, AgentCard (A2A-compatible)" |
| `aktor-integrations` | "Rust-native LLM provider adapters" | "Infrastructure-level integrations: trusted-directory federation, observability exporters, registry backends" |

Neither of these requires deleting files. Only documentation and
future content change.

### 7.4 Separate repositories (unchanged from previous spec)

- `aktor-python` — Python SDK, now **thin A2A server + ergonomic
  decorator** instead of "privileged RPC shim". Apache 2.0.
- No more `aktor-proto` repository. The protocol is A2A, governed
  externally. aktor publishes its AgentCard extension fields (if any)
  as a small addendum document, not a full `.proto` tree.

---

## 8. Commercial and Ecosystem Consequences

The A2A-first framing has significant downstream effects that should
be acknowledged before committing.

### 8.1 Lock-in reduction (feature, not bug)

Under the previous design, users who wrote agents against
`aktor-python` were locked in — the API was private. Under A2A-first,
users write A2A agents. They can point them at any A2A orchestrator.
**That is intentional.** It trades vendor lock-in for ecosystem
gravity.

The commercial hypothesis: the runtime value (virtual actors, Flow,
supervision, BSL protection) is strong enough that users stay because
aktor is the best A2A orchestrator, not because they are trapped.
This is Kubernetes's hypothesis about containers — make the interface
open, compete on runtime quality — and it worked.

### 8.2 Instant interop with the emerging agent ecosystem

As of early 2026, A2A adoption is spreading rapidly. LangGraph,
AutoGen, and CrewAI are all adding A2A endpoints. Google Cloud is
publishing A2A directory services. By committing to A2A as the
primary boundary, aktor **inherits this entire ecosystem on day one**
without per-framework adapter work.

### 8.3 BSL 1.1 + A2A is a coherent pair

BSL 1.1 prevents hyperscalers from offering aktor as a managed service
without commercial negotiation. A2A prevents users from being
permanently trapped if they outgrow aktor. Together, they produce a
clean open-core story: **the protocol is free, the runtime is
source-available, the managed service requires a commercial
agreement**. This is the same structure Elastic and MongoDB tried
(and failed, in different ways) — aktor gets to learn from both.

### 8.4 Risk: "just use LangGraph + curl"

If A2A makes every orchestrator replaceable, users may decide they do
not need aktor at all and just wire LangGraph agents together with
shell scripts. The mitigation: at non-trivial scale (10+ agents, 100+
concurrent sessions, any supervision requirement), the "just use
curl" story falls apart rapidly. aktor's target user is the one who
has already outgrown that, or is building a platform that will.

---

## 9. Open Questions

1. **AgentCard extension fields.** A2A AgentCard is extensible.
   aktor needs to declare: tenant ID routing hint, virtual actor ID
   affinity, trace propagation headers, supervision policy hints. Do
   we register these in a public addendum, or keep them as internal
   runtime headers the wire never sees outside aktor-to-aktor?
   Decision: internal, with a clear naming prefix (`x-aktor-*`) for
   auditability.
2. **Transport for cluster node-to-node traffic.** HTTP/JSON is clean
   but adds overhead when aktor runtime instances talk to each other.
   Options: keep HTTP/JSON; upgrade to A2A-over-gRPC (binary,
   still A2A semantics); introduce a private actor-routing protocol
   for the cluster layer only. Decision: start with HTTP/JSON for
   simplicity; introduce gRPC variant if measurement demands it.
3. **Control-plane lifecycle capability naming.** The `__lifecycle__`
   capability needs a well-known spec: method names, return shapes,
   required vs optional methods. Decision: define it as a small
   appendix in the `aktor-a2a` crate's documentation, reserve the
   right to propose upstream to A2A as a standard extension later.
4. **Authentication.** A2A supports several auth modes (API key,
   bearer token, mTLS). aktor's first-party workers use UDS on the
   same host (no auth needed); cross-host uses mTLS; cross-org uses
   bearer token signed by trusted directory. Decision: document the
   three modes, implement UDS + mTLS first, defer trusted-directory
   federation.
5. **How does Flow serialization interact with A2A?** A Flow
   definition containing only A2A agent references (no local
   closures) **is** serializable — because every `Flow::Agent` variant
   is just "an A2A endpoint URL + parameters". This is much more
   powerful than the current scaffold suggests. Flow-as-data becomes
   real rather than theoretical. Explore in a follow-up spec.

---

## 10. Decision Summary

| Decision | Value |
|---|---|
| Primary data-plane protocol | **A2A (HTTP + JSON-RPC + AgentCard)** |
| Rust↔Python bridge | **Does not exist as a distinct concept — it is A2A** |
| Python SDK role | **Optional ergonomic wrapper over A2A** |
| Control plane mechanism | **A2A lifecycle capability extension** |
| MCP integration layer | **Business-layer concern, not runtime** |
| New crate this unblocks | **`aktor-a2a`** (replaces planned `aktor-protocol` + `aktor-bridge`) |
| Scaffold changes required now | **`AgentCard: Serialize + Deserialize`, doc updates, nothing structural** |
| Scaffold `cargo check` status | **Remains green; all changes are additive** |

---

## 11. Next Steps

Immediate (this session):

- Update `aktor/README.md` to v3 positioning ("A2A orchestration
  runtime, polyglot by default")
- Update `aktor/CLAUDE.md` "Positioning" section accordingly
- Tag the superseded Python-integration spec with its superseded
  header (done)

Short-term (next session):

- Add `serde` derives to `AgentCard` in `aktor-agents/src/lib.rs` and
  the matching `serde` dependency in `agents/Cargo.toml`
- Create `aktor-a2a/` crate skeleton (new workspace member), BSL 1.1,
  with a `Cargo.toml`, `README.md`, and a `src/lib.rs` stub defining
  `A2aClient`, `A2aServer`, `A2aAgentProxy: Task`, and the lifecycle
  capability trait
- Run the full validation suite (`fmt`, `clippy`, `check`, `test`,
  `doc`) and extend the bootstrap commit history with a `v0.0.2-a2a`
  tag

Medium-term:

- Flow-as-data follow-up spec: with all agent nodes being A2A
  references, what does serializable Flow actually look like?
- Cluster transport decision: HTTP/JSON vs A2A-over-gRPC
- Python SDK bootstrap in a separate repository

---

## Appendix A: What the Previous Spec Got Right

Reading the superseded Python-integration spec, a few points remain
valid even under A2A-first and should not be lost:

- **Multi-tenant isolation requires process boundaries.** Still true.
  First-party aktor-managed workers run as separate OS processes per
  tenant. The A2A layer simply describes how aktor talks to them, not
  how they are isolated from each other.
- **Supervision of managed workers is a runtime responsibility.**
  Still true. The A2A lifecycle extension is the control channel for
  this, but the supervision strategy lives in `aktor-core`.
- **Pydantic is the right Python type layer.** Still true. The
  Python SDK wraps Pydantic models with the `@aktor.task` decorator
  and auto-generates the JSON schema that appears in the A2A
  AgentCard. Users think in types, the SDK takes care of the wire.
- **Hot reload, trace propagation, CI isolation.** All still apply,
  and all become cleaner under A2A because the protocol is a known
  published spec with community-maintained tooling.

The superseded spec's mistake was not in its technical analysis — it
was in the framing of the problem as "a private Rust↔Python problem"
when the correct frame is "a universal agent-to-agent problem that
happens to include Rust↔Python as one instance."
