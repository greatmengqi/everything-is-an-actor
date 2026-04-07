# FlowBench v0

A multi-agent benchmark that explicitly stress-tests compositional combinators
(`traverse`, `race`, `at_least`, `recover`) instead of generic capability ceilings.

## Why FlowBench

Existing multi-agent benchmarks (GAIA, AgentBench, BrowseComp) test whether
an agent can complete a hard task end-to-end. They rarely require parallelism,
racing, quorum, or fault recovery in their reference solutions. A planner
that emits only linear sequences of tool calls scores almost as well on
those benchmarks as one that can express the full algebraic surface.

FlowBench is engineered to **separate planners by their compositional
affordance**. Each task is constructed so that an *optimal* solution requires
at least one specific combinator from `{traverse, race, at_least, recover}`,
and a wall-clock budget rules out trivially serial solutions.

## Categories

| Category | Minimum combinator | What it tests |
|----------|--------------------|---------------|
| `parallel-heavy` | `traverse` / `zip_all` | Independent items must be processed concurrently |
| `race-heavy` | `race` / `recover_with` | Multiple strategies; first non-failure wins |
| `quorum-heavy` | `at_least` | Validated multi-proposer consensus |
| `recover-heavy` | `recover` / `fallback_to` / `branch_on` | Graceful degradation under injected faults |

## v0 task set

| Task ID | Category | Min combinator | Wall-clock budget |
|---------|----------|----------------|-------------------|
| `parallel_001` | parallel-heavy | `traverse` | 30 s |
| `race_001` | race-heavy | `race` | 20 s |
| `quorum_001` | quorum-heavy | `at_least` | 45 s |
| `recover_001` | recover-heavy | `recover` | 30 s |
| `parallel_002` | parallel-heavy | `zip_all` | 25 s |

These five pilot tasks exist to validate that:
1. The schema is expressive enough to specify a real multi-agent task.
2. The scorers can be written automatically.
3. A free-form ReAct baseline cannot trivially solve them.
4. JIT Flow on a Frontier LLM produces a well-formed Flow expression for them.

If any of these four reality checks fails on the v0 task set, the entire
benchmark design must be revisited before scaling to 60 tasks.

## Schema

See `schema.py` for the Pydantic definition. Each task YAML must conform.

## Scoring

Scorers are deterministic and rule-based wherever possible. The only exception
is when the ground truth is itself a synthesis (e.g. a meta-review); in those
cases an LLM-as-judge with three independent judges and majority vote is used.
The judge model is **always different from the planner model** to avoid
self-evaluation bias.

## Anti-cheat: the wall-clock lever

Each task has a `wall_clock_budget_s` constraint set so that a strictly serial
baseline cannot complete the task in time. This forces the planner to either
introduce parallelism or fail. Without this lever, a sufficiently fast model
could brute-force the task with ReAct and JIT Flow's combinator advantage
would not be visible in the metrics.

The `oracle.serial_baseline_runtime_s` field documents the empirical serial
runtime so reviewers can verify the constraint is meaningful.

## What FlowBench is *not*

- Not a general-purpose multi-agent benchmark.
- Not a test of overall LLM capability.
- Not a substitute for GAIA, AgentBench, etc.

It tests one thing: whether the planning interface lets the LLM express the
structurally correct solution to a task that needs structure.
