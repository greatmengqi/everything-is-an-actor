# Paper 2 roadmap

What we have right now (v0) and what each subsequent milestone needs.

## v0 -- documentation snapshot (current)

What is in the tree today:

- **`paper.md`** -- complete method-first paper draft. Sections 1-5 and
  7-9 are written; Section 6 (results) is a protocol + hypotheses
  placeholder waiting on actual experiments.
- **`flowbench/README.md`** -- benchmark design rationale.
- **`flowbench/schema.py`** -- Pydantic schema for `BenchmarkTask`.
- **`flowbench/tasks/*.yaml`** -- 5 hand-written pilot tasks across
  the four categories.
- **`flowbench/runner.py`** -- task runner skeleton.
- **`jit_flow/`** -- reference implementation skeleton (prompt,
  parser, repair, inference loop).

What v0 is **not**:

- Not a runnable end-to-end system. The LLM client and concrete
  baselines are not wired.
- Not a complete benchmark. 5 pilot tasks, not 60.
- Not type-checked. The parser validates structure but not Python
  type compatibility across composition nodes.
- Not scored. Scorer functions are not implemented yet.

## v1 -- "first end-to-end run on one task" milestone

Goal: take `parallel_001.yaml`, run JIT Flow with Claude Opus 4.6
against the existing actor runtime, get a result, score it. No
baselines yet, no statistics, just one trial that produces a number.

Required deliverables:

1. **Mock agent catalog** -- `parallel_001` references
   `SummarizerAgent` and `MetaReviewAgent`. Implement them as actual
   `AgentActor` subclasses with realistic latency (a `sleep` of 5-10s
   to mimic frontier LLM call latency).
2. **Scorer functions** -- one Python function per task in
   `paper2/flowbench/scorers.py`, signature `(inputs, output) -> bool`.
   Wired through `CallableScorer` in the schema (callable reference,
   not eval).
3. **LLM client** -- thin wrapper around `anthropic.AsyncAnthropic` (or
   the project's existing integration if there is one) conforming to
   `LLMCallable`.
4. **Dispatch wrapper** -- glue between `JITFlowLoop._dispatch` and
   `AgentSystem.run_flow(...)`.
5. **Smoke test** -- a single-file script that loads
   `parallel_001.yaml`, runs it through JIT Flow with the mock
   agents and the LLM client, and prints the resulting `LoopOutcome`.

If v1 succeeds, we know the architecture is plausible. If v1 fails,
we know what is broken before scaling up.

## v2 -- "five pilot tasks, JIT Flow only" milestone

Goal: run JIT Flow against all 5 pilot tasks across the 4 categories,
3 trials each, with one frontier LLM. Validate that the FlowBench
schema is workable and that the parser handles real LLM output
shape variance.

Required deliverables:

- Mock agents for `race_001`, `quorum_001`, `recover_001`, `parallel_002`.
- Scorer functions for all 5 tasks.
- Run the runner; produce a 5-row table.
- Document any LLM output failures the parser/repair loop did not
  recover. These will inform prompt-engineering improvements.

## v3 -- "baselines" milestone

Goal: implement the four baselines (ReAct, Plan-and-Execute, LangGraph,
AutoGen) as `PlannerCallable`s and run them against the same 5
pilot tasks.

Required deliverables:

- Each baseline as a single Python module under `paper2/baselines/`.
- Each baseline shares the same agent catalog as JIT Flow.
- A 5-method by 5-task by 3-trial run produces a 75-trial dataset.
- The first comparison plot in the paper (categories on x, success
  rate on y, methods as colored bars).

If JIT Flow does not beat the baselines on at least one category in
v3, we need to revisit the FlowBench task design before authoring
the remaining 55 tasks.

## v4 -- "FlowBench full release" milestone

Goal: scale the benchmark from 5 to 60 tasks (15 per category).

Required deliverables:

- 55 additional tasks, hand-written and validated.
- A `flowbench/STYLE.md` describing the conventions any new task
  must follow (prompt phrasing, scorer style, oracle measurement).
- A scorer for each new task.

## v5 -- "full empirical sweep" milestone

Goal: run the 4 baselines plus JIT Flow on all 60 tasks across
3 frontier LLMs with 3 trials each = 2,700 trials. Produce the
headline tables in `paper.md` Section 6.

Required deliverables:

- Filled-in Section 6 of `paper.md`.
- Tables and figures.
- Discussion of which hypotheses (H1-H5 from Section 6.5) held and
  which failed.
- arXiv v2 of the preprint.

## What we are NOT doing

- We are not fine-tuning models. Everything is inference-time.
- We are not implementing constrained decoding. Standard prompting
  with parser+repair.
- We are not running on closed/private benchmarks. FlowBench ships
  with the paper.
- We are not making cross-language interpreters for v1-v5. The
  cross-runtime claim is inherited from the prior paper, not
  re-tested here.
