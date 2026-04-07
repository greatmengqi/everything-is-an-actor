# JIT Flow: Inference-Time Compositional Planning with a Typed Plan Calculus for LLM Agents

**Mengqi Chen and Qianhui TODO_LAST_NAME**
*Independent researchers, 2026*

---

## Abstract

LLM agents currently choose between two extremes when composing
multi-step plans. Free-form planners such as ReAct and Plan-and-Execute
let the model emit natural-language steps or arbitrary Python code,
gaining maximum flexibility but losing all structural guarantees about
parallelism, fault recovery, and inter-agent type compatibility.
Static workflow frameworks such as LangGraph, AutoGen, and CrewAI
require humans to predefine an execution graph, gaining structure
but losing the ability to adapt the plan shape to runtime task variety.

We propose **JIT Flow**, a third path: at inference time, the LLM
emits expressions in a typed algebraic plan calculus (Flow), which
are parsed, type-checked, dispatched to an actor runtime, and whose
results are fed back to the model for the next sub-expression. Flow,
introduced in our prior work, is grounded in symmetric monoidal
category theory and provides eight primitive combinators (sequence,
parallel, race, branch, loop, recover, at-least, pure) closed under
composition. By constraining the model's output to typed Flow
expressions rather than free-form code or pre-bound graph nodes,
JIT Flow obtains structural guarantees (compile-time type checking,
guaranteed cleanup, well-defined fault semantics) while retaining
inference-time dynamism.

To evaluate JIT Flow we contribute **FlowBench**, the first multi-agent
benchmark explicitly designed to stress-test compositional combinators.
FlowBench contains tasks in four categories—parallel-heavy, race-heavy,
quorum-heavy, and recover-heavy—each with automated scorers and a
declared *minimum combinator* that any successful plan must include.

We describe the JIT Flow architecture, the FlowBench design rationale,
the evaluation protocol, and a reference implementation built on top
of an open-source actor runtime. **Empirical evaluation is in
progress and will appear in v2 of this preprint.** This v1 establishes
the method, the benchmark, and the protocol, and articulates falsifiable
hypotheses about when externalizing plan algebra helps and when it
does not.

---

## 1. Introduction

The last two years have seen a Cambrian explosion of frameworks for
*LLM agents*: systems that use a language model as a controller for
calling tools, spawning sub-agents, retrieving information, and
producing structured outputs. Despite the diversity, almost every
production framework occupies one of two extremes on a single design axis.

**Extreme 1: free-form planners.** ReAct (Yao et al., 2023) and its
descendants let the LLM emit a step at a time in natural language;
Plan-and-Execute variants emit a list of steps; tool-augmented agents
emit a function call. The plan is essentially a transcript: linear,
sequential, with no structural distinction between "do A then B" and
"do A and B in parallel." The model has full expressive freedom but
the plan has no compile-time semantics. Failures, retries, parallelism,
and quorum decisions all reduce to natural-language reasoning, which
the model performs implicitly and unverifiably.

**Extreme 2: static workflow frameworks.** LangGraph, AutoGen, CrewAI,
and OpenAI Swarm require a human to predefine a graph of nodes and
edges. The LLM is then constrained to the predefined graph: it picks
which node to enter, what arguments to pass, and when to halt. The
graph has structure—nodes are typed, edges are explicit, parallelism
can be encoded—but the structure is *static*. If a runtime task
demands a plan shape the human did not anticipate, the framework
cannot adapt.

These two extremes share a hidden assumption: that *plan structure*
and *plan content* must be authored by the same actor. Free-form
planners let the model author both. Static frameworks let the human
author the structure and the model author the content.

**This paper rejects that assumption.** We propose that plan structure
should be authored by the model *at inference time*, but constrained
to a *typed algebraic calculus*. The model emits a plan expression
in a small, well-defined grammar; the runtime parses and type-checks
the expression; if it is valid, the actor runtime executes it; the
result is fed back to the model, which emits the next expression.

This third path requires a calculus that is (i) small enough to be
generated reliably by an LLM under prompt control, (ii) expressive
enough to cover sequential, parallel, racing, fault-tolerant, and
quorum compositions, and (iii) typed enough that ill-formed plans
can be statically rejected before execution. In our prior work
(Chen and TODO, 2026) we introduced **Flow**, a plan calculus with
exactly these properties, grounded in symmetric monoidal category
theory and proven DAG-complete.

**Contributions of this paper.**

1. **JIT Flow**, an inference-time architecture that emits Flow YAML
   expressions, parses them with the existing Flow toolchain, type-checks
   them, dispatches them to an actor runtime, and feeds results back
   to the model. The architecture requires no fine-tuning and uses
   only standard prompting against frontier LLMs.

2. **FlowBench**, a benchmark of multi-agent tasks explicitly
   designed to require compositional combinators. Each task has an
   automated scorer and declares a minimum combinator that any
   successful plan must include. We release the v0 task set
   alongside the paper.

3. An **evaluation protocol** that compares JIT Flow against four
   baselines (ReAct, Plan-and-Execute, LangGraph, AutoGen) across
   three frontier models (Claude Opus 4.6, GPT-5, Llama 3.1 405B)
   on FlowBench, with explicit ablations isolating the contribution
   of type checking, the repair loop, and the algebraic surface.

4. A **set of falsifiable hypotheses** about when externalizing plan
   algebra helps frontier LLMs and when it does not, articulated
   *before* the empirical evaluation in order to commit the work to
   honest reporting of negative or null results.

## 2. Background and Related Work

### 2.1 Free-form LLM planners

ReAct (Yao et al., 2023) interleaves *Reason* and *Act* steps in a
single transcript: the LLM emits natural-language reasoning followed
by a single tool call, observes the result, and continues. The
expressive power of ReAct is the expressive power of natural language,
but its structural guarantees are zero. Plan-and-Execute (Wang et al.,
2023) adds a planning phase that produces a list of steps before any
execution, mitigating myopia but retaining the linear-list structure.
Tree of Thoughts (Yao et al., 2023b) generalizes to a search tree
over reasoning paths but does not address inter-agent composition.

### 2.2 Static workflow frameworks

LangGraph (LangChain, 2024) models agent systems as state machines
with typed state transitions, where transitions are functions and
the LLM picks which transition to fire. AutoGen (Wu et al., 2023)
provides a GroupChat abstraction in which agents take turns under
a conversation manager. CrewAI organizes agents into roles with
predefined task hand-offs. OpenAI Swarm (2024) uses *handoffs* as
the unit of inter-agent transfer. All four require humans to
preauthor the topology of agent interaction; the LLM merely
populates pre-existing slots.

### 2.3 Algebraic and categorical approaches to computation

The use of algebraic calculi to give computation a compositional
semantics is old: process algebras (CSP, CCS, π-calculus) decades
ago, monadic effects in functional programming (Wadler 1992), and
the categorical foundations of database queries and dataflow.
Symmetric monoidal categories underlie quantum circuit notation
(Coecke and Kissinger, 2017), petri nets, and the string diagrams
of applied category theory. Our prior work (Chen and TODO, 2026)
applies this lineage to multi-agent orchestration, defining Flow
as an ADT with combinators that satisfy functor and naturality laws.

### 2.4 Constrained decoding and structured generation

Recent work has shown that LLMs can be constrained to produce
outputs in a target grammar (Outlines, Guidance, llama.cpp's GBNF,
Anthropic tool use, OpenAI structured outputs). Constrained decoding
guarantees syntactic validity but does not, in general, enforce
semantic typing across the resulting AST. JIT Flow uses standard
unconstrained generation followed by parsing and type-checking;
constrained decoding could be added as an optimization but is not
required for our claims.

### 2.5 The gap

To our knowledge, no published system *both* (i) lets the LLM
author plan structure dynamically at inference time *and* (ii)
constrains that structure to a typed algebraic calculus. JIT
Flow occupies this gap.

## 3. The Flow Calculus (Recap)

This section briefly recaps the Flow calculus as introduced in our
prior work; readers are referred to (Chen and TODO, 2026) for the
full categorical treatment.

A Flow expression of type `Flow[I, O]` denotes a computation taking
input of type `I` and producing output of type `O`. Flow expressions
are built from eight primitive combinators:

| Combinator | Type | Semantics |
|---|---|---|
| `pure(f)` | `Flow[I, O]` | Lift a pure function |
| `agent(A)` | `Flow[I, O]` | Invoke agent class `A` |
| `seq(f, g)` | `Flow[I, O]` | Sequential composition (`f` then `g`) |
| `zip(f, g)` | `Flow[I, (O1, O2)]` | Run `f` and `g` in parallel; pair results |
| `race(f, g)` | `Flow[I, O]` | Run both; first non-failure wins; cancel sibling |
| `branch(p, t, e)` | `Flow[I, O]` | Predicate-driven choice |
| `recover(f, h)` | `Flow[I, O]` | Run `f`; on failure run `h` |
| `at_least(n, fs)` | `Flow[I, QuorumResult[O]]` | Validated quorum: succeed iff ≥`n` succeed |

The calculus is closed under composition, supports a YAML serialization
format with EBNF grammar, and has a reference interpreter built on
top of an actor runtime. The combinator set is DAG-complete: any
directed acyclic workflow over agents is expressible.

The key property we exploit in JIT Flow is **static type compatibility
checking**: an expression `seq(f, g)` is well-typed only if the output
type of `f` matches the input type of `g`. This check rejects
ill-formed compositions before any side effect occurs.

## 4. JIT Flow Architecture

### 4.1 Overview

JIT Flow runs an inference-time loop with four stages per iteration:

```
                        ┌──────────────────────┐
                        │  Frontier LLM        │
                        │  (Opus / GPT-5 /     │
                        │   Llama 405B)        │
                        └──────────┬───────────┘
                                   │  Flow YAML text
                                   ▼
                        ┌──────────────────────┐
                        │  YAML parser         │
                        │  → Flow AST          │
                        └──────────┬───────────┘
                                   │
                          ┌────────┴────────┐
                          │                 │
                       parse error?      parse OK
                          │                 │
                          ▼                 ▼
                  ┌──────────────┐  ┌──────────────┐
                  │ Repair loop  │  │ Type checker │
                  │ (1 retry)    │  └──────┬───────┘
                  └──────────────┘         │
                                  ┌────────┴────────┐
                                  │                 │
                               type err          type OK
                                  │                 │
                                  ▼                 ▼
                          ┌──────────────┐  ┌──────────────┐
                          │ Repair loop  │  │ Actor runtime│
                          └──────────────┘  │   dispatch   │
                                            └──────┬───────┘
                                                   │
                                               TaskResult
                                                   │
                                                   ▼
                                         ┌──────────────────┐
                                         │ Feedback to LLM  │
                                         │  for next round  │
                                         └──────────────────┘
```

### 4.2 Inference-time prompt

The prompt to the LLM consists of four sections, in this order:

1. **Task statement** — natural language description of what the
   user wants the agent system to accomplish, plus any inputs.
2. **Available agents** — a typed catalog of agent classes the LLM
   may invoke, with their input and output types and a one-line
   description of what each agent does.
3. **Flow grammar reference** — a compressed cheat-sheet of the
   eight combinators with type signatures and 5-10 worked examples.
4. **Output format** — instructions to emit a single Flow YAML
   expression representing the *next sub-expression* to execute,
   plus an optional commentary block.

The model emits a Flow YAML expression. Crucially, the model is
*not* required to emit the entire plan at once. Each iteration
produces one sub-expression that runs to completion before the
next iteration is prompted with the result.

### 4.3 Parser and type checker

The YAML output is parsed by `everything_is_an_actor.flow.serialize.from_dict`,
which is the existing parser shipped with the Flow runtime. Parser
errors trigger the repair loop (Section 4.5).

The parser produces a Flow AST. The AST is passed through a type
checker that walks the tree and verifies, at each composition node,
that the output type of the upstream expression matches the input
type of the downstream expression. Agents declare their input and
output types via Python generics (`AgentActor[I, O]`), which the type
checker introspects. Type errors trigger the repair loop.

### 4.4 Dispatch and feedback

A well-typed Flow AST is dispatched to the actor runtime via
`AgentSystem.run_flow(...)`, which spawns ephemeral actors for each
node, threads the data through, and guarantees cleanup. The result
is a `TaskResult[O]` containing either the value, the failed sub-tree
(in the case of `at_least`), or an exception (in the case of an
unrecovered failure).

The result is serialized to a compact representation and appended
to the LLM's context for the next iteration. The loop terminates
when the LLM emits a `pure(answer)` node, or when a maximum iteration
budget is reached.

### 4.5 Repair loop

When the parser or the type checker rejects an LLM output, JIT Flow
issues a single repair attempt: the original prompt is augmented
with the rejected output and the error message (parser error with
line and column, or type error with the offending composition node),
and the LLM is asked to produce a corrected expression. If the
repair attempt also fails, the iteration is logged as a *plan
failure* and the run is marked failed.

Limiting repair to one retry reflects realistic deployment: in
production, an unbounded repair loop would be a denial-of-service
vector. We measure plan failure rate as one of the headline metrics
in Section 6.

## 5. FlowBench

### 5.1 Design rationale

Existing multi-agent benchmarks (GAIA, AgentBench, BrowseComp) test
end-to-end task completion but rarely require parallelism, racing,
quorum, or fault recovery in their reference solutions. A planner
that can only emit linear sequences of tool calls performs nearly
as well on these benchmarks as one that can express the full
algebraic surface. We therefore cannot use existing benchmarks to
evaluate JIT Flow against baselines: they would not separate the
treatment from the control.

FlowBench is designed to provide that separation. Each task is
constructed so that an *optimal* solution requires at least one
specific combinator from `{traverse, race, at_least, recover}`.
Tasks are organized into four categories, one per combinator
family, with declared *minimum combinators* for each task.

### 5.2 Task schema

Every FlowBench task is a YAML document conforming to the schema
in Listing 1.

```yaml
# Listing 1: FlowBench task schema (excerpt)
task_id: parallel_001
category: parallel-heavy
minimum_combinator: traverse
prompt: "..."
inputs:
  papers: [...]
ground_truth:
  required_outputs: 10
  required_aggregation: meta-review
scorer:
  type: rule_based
  rules:
    - "len(output.summaries) == len(input.papers)"
    - "output.meta_review references all paper IDs"
constraints:
  wall_clock_budget_s: 30
  max_iterations: 5
oracle:
  serial_baseline_runtime_s: 80
```

The `wall_clock_budget_s` constraint is the *anti-cheat lever*: it
is set so that a strictly serial baseline cannot finish in time,
forcing the planner to introduce parallelism. The
`serial_baseline_runtime_s` field documents the empirical serial
time so that reviewers can verify the constraint is meaningful.

### 5.3 Categories and pilot tasks

**Parallel-heavy** (15 planned, 1 pilot in v0). A task is parallel-heavy
if its inputs naturally factor over a list of independent items
*and* the wall-clock budget rules out serial processing. Reference
combinator: `traverse` or `zip_all`.

**Race-heavy** (15 planned, 1 pilot). Multiple agents pursue
different strategies for the same goal; the planner must launch
them concurrently and accept the first non-failure result.
Reference combinator: `race` or `recover_with`.

**Quorum-heavy** (15 planned, 1 pilot). Multiple proposers generate
candidate answers; the answer is accepted iff at least *n* of *m*
proposers agree (or pass an automated check). Reference combinator:
`at_least`.

**Recover-heavy** (15 planned, 1 pilot). The primary plan can fail
in known ways; the planner must specify a fallback chain so that
the system degrades gracefully rather than aborting. Reference
combinator: `recover`, `fallback_to`, or `branch_on`.

The five pilot tasks accompanying this preprint are documented in
the project repository.

### 5.4 Scoring

All FlowBench scorers are deterministic and rule-based wherever
possible. A small fraction of tasks (those whose ground truth is a
synthesis or a meta-review) use LLM-as-judge with three independent
judges and majority vote. The judge model is held fixed across all
runs and is **distinct from the planner model**, to avoid
self-evaluation bias.

### 5.5 What FlowBench does *not* claim

FlowBench is not a general-purpose multi-agent benchmark. It is
specifically engineered to surface the difference between planners
that can express algebraic compositions and planners that cannot.
A planner that scores 100% on FlowBench is not necessarily a
better general agent than one that scores 50%; the benchmark tests
a *specific affordance*, not overall capability. We expect FlowBench
to be used in conjunction with general-purpose benchmarks.

## 6. Experimental Protocol and Hypotheses

This section defines the experimental sweep that will populate the
empirical results in v2 of this preprint.

### 6.1 Models

| Model | Provider | Reason for inclusion |
|---|---|---|
| Claude Opus 4.6 (1M context) | Anthropic | Frontier reasoning, large context, native tool use |
| GPT-5 | OpenAI | Frontier alternative, tests cross-vendor generality |
| Llama 3.1 405B Instruct | Meta (open weights) | Open-weight frontier; tests whether JIT Flow's gains depend on closed APIs |

### 6.2 Baselines

| Baseline | Implementation |
|---|---|
| **ReAct** | LangChain ReAct agent with the same tool/agent catalog; natural-language reasoning |
| **Plan-and-Execute** | LangChain Plan-and-Execute with explicit planner/executor split |
| **LangGraph** | A generic LangGraph agent graph with conditional edges, populated by the LLM |
| **AutoGen** | AutoGen GroupChat with the same agent catalog as participants |
| **JIT Flow (ours)** | Flow YAML emission with parse + type-check + dispatch + feedback |

All baselines see the same agent catalog and the same task statements.
Differences in performance are attributable to the *planning interface*,
not the underlying tools.

### 6.3 Sweep

| Dimension | Levels |
|---|---|
| Models | 3 (Opus, GPT-5, Llama 405B) |
| Methods | 5 (ReAct, P&E, LangGraph, AutoGen, JIT Flow) |
| Tasks (FlowBench v0 → full) | 5 → 60 |
| Trials per (model, method, task) | 3 |
| **Total runs (v0)** | **225** |
| **Total runs (full)** | **2,700** |

### 6.4 Metrics

1. **Task success rate** — fraction of (model, method, task) trials whose
   final output passes the task scorer.
2. **Plan failure rate** — fraction of trials in which the planner produces
   no executable plan within the iteration budget (parser/type-checker
   rejection after repair, or invalid graph for LangGraph baselines).
3. **Compile-time catch rate** (JIT Flow only) — fraction of trials in
   which the type checker rejected at least one LLM output during the
   run, preventing a runtime error.
4. **Token cost** — total prompt + completion tokens per trial.
5. **Wall-clock latency** — end-to-end time per trial.
6. **Fault recovery rate** — for tasks in the recover-heavy category,
   fraction of trials in which an injected agent fault was recovered.

### 6.5 Hypotheses (committed before evaluation)

We commit to the following falsifiable hypotheses:

**H1.** On parallel-heavy and race-heavy categories, JIT Flow achieves
strictly higher task success rate than ReAct and Plan-and-Execute,
across all three models.

**H2.** On quorum-heavy and recover-heavy categories, JIT Flow achieves
strictly higher fault recovery rate than LangGraph and AutoGen,
across all three models.

**H3.** JIT Flow's compile-time catch rate is non-trivial: at least
10% of trials see at least one type-check rejection that, if
unchecked, would have caused a runtime error. (If this is below
10%, the type checker is decoration, not a load-bearing component.)

**H4. (Anti-hype null hypothesis.)** On general-purpose benchmarks
that do *not* require compositional combinators (e.g., GAIA), JIT Flow
performs no better than ReAct. The JIT Flow advantage is *task-shape
specific*, not a general intelligence boost.

**H5. (Smaller-model amplification.)** The relative gain of JIT Flow
over ReAct is larger for Llama 3.1 405B than for Opus 4.6, because
externalized structure compensates for weaker internal planning.
If this fails, externalized structure is purely a frontier-model
phenomenon.

We commit to reporting the result of each hypothesis test in v2,
including those that fail.

## 7. Discussion

### 7.1 What externalized plan algebra is for

We have argued throughout that the value of JIT Flow is not "making
the LLM smarter." Frontier LLMs already construct, internally, plans
that are roughly equivalent in structure to Flow expressions when
asked. The value is *making the plan a first-class artifact* that
can be type-checked, dispatched, audited, persisted, replayed,
diffed, version-controlled, and ported across runtimes. These are
software engineering properties, not modeling properties, and they
are exactly the properties that production agent systems lack today.

### 7.2 When JIT Flow should not be used

If a task is solved by a single tool call, JIT Flow has overhead
without benefit. If a task can be expressed as a fixed pipeline
known at compile time, a static workflow framework is simpler. JIT
Flow's regime is the *intersection*: tasks whose plan shape is
unknown at compile time *and* whose execution requires structural
guarantees a free-form planner cannot provide.

### 7.3 The honest case for benchmarks of compositional affordance

Most LLM agent benchmarks today test capability ceilings: can the
agent do this hard task? FlowBench tests an affordance: does the
planning interface let the agent express the structurally correct
solution? These are different questions. We believe affordance
benchmarks are underrepresented and that the field has implicit
biases that reward larger models on capability benchmarks while
ignoring the structural quality of how those models compose work.

## 8. Limitations

**Single-author engineering.** This work is conducted without
institutional fine-tuning resources. We rely entirely on
inference-time methods against frontier APIs and open weights.
This is a strength (no training data leakage, no compute-budget
disparity) and a weakness (we cannot test training-time hypotheses).

**FlowBench v0 is small.** The pilot release contains only 5 tasks
across 4 categories. The full release of 60 tasks is a substantial
authoring effort and may reveal that some categories are harder to
populate than others. We will document any imbalance.

**Reference implementation in Python.** The Flow runtime is
implemented in Python on asyncio. The cross-runtime portability
claim from our prior work is therefore tested only in spirit
here; cross-language interpreters are future work.

**LLM-as-judge bias.** Tasks that require LLM-as-judge scoring
introduce a small risk of model-specific bias. We mitigate this
by holding the judge model fixed and distinct from the planner,
and by triangulating with rule-based scorers wherever possible.

## 9. Conclusion

LLM agent planning today is split between the unstructured freedom
of free-form planners and the rigid structure of static workflow
frameworks. We have argued for a third path: typed algebraic plan
expressions emitted by the LLM at inference time, parsed,
type-checked, and dispatched to an actor runtime, with results
fed back to the model for incremental composition. We described
the JIT Flow architecture, contributed the FlowBench benchmark
of compositionally demanding tasks, defined a falsifiable
experimental protocol, and committed to honest reporting of
hypotheses—including the anti-hype null. The empirical evaluation
will appear in v2 of this preprint.

If the hypotheses hold, externalizing plan algebra at inference
time gives production agent systems a cheap, training-free path to
structural guarantees they currently lack. If the hypotheses fail,
we will have learned that frontier LLMs are sufficient on their
own and that structural externalization is decorative, which is
itself a useful finding for the field.

---

## Acknowledgments

This work builds directly on the open-source `everything-is-an-actor`
project. We thank the project's contributors and early users for
the runtime that made the JIT Flow architecture implementable in a
single sitting.

## References

(Reference list to be assembled in v2; key citations are inline in
Section 2.)

---

**Status:** v0 draft. Sections 1-5, 7-9 complete. Section 6 specifies
the protocol; the empirical fill-in is pending. FlowBench v0 (5 pilot
tasks) and JIT Flow reference implementation (skeleton) accompany
this draft in the project repository.
