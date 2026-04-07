# JIT Flow reference implementation

This directory holds the reference implementation of the JIT Flow
inference loop described in Section 4 of `paper2/paper.md`. It is a
**skeleton**: the core control flow is in place, but baselines and
production hardening are deliberately deferred.

## Architecture

```
┌─────────────────┐
│  Frontier LLM   │  (Claude / GPT-5 / Llama 405B)
└────────┬────────┘
         │  Flow YAML text in fenced ```yaml block
         ▼
┌─────────────────┐
│  parser.py      │  YAML -> Flow AST (with registry-based agent resolution)
└────────┬────────┘
         │  ParseError on failure
         ▼
┌─────────────────┐
│  repair.py      │  one-shot repair attempt; max 1 retry per iteration
└────────┬────────┘
         │  ParseError on second failure -> trial marked plan-failure
         ▼
┌─────────────────┐
│  Flow runtime   │  AgentSystem.run_flow(...) -- existing actor runtime
└────────┬────────┘
         │  TaskResult or runtime exception
         ▼
┌─────────────────┐
│  inference_loop │  appends observation, loops until terminal or budget exhausted
└─────────────────┘
```

## Files

| File | Purpose | Status |
|---|---|---|
| `prompt.py` | Build the four-section LLM prompt: task, catalog, grammar, examples, instructions, prior observations | written |
| `parser.py` | YAML -> Flow AST with agent registry; raises `ParseError(phase, message)` | written |
| `repair.py` | Issue exactly one repair attempt with the parser error message in the prompt | written |
| `inference_loop.py` | Main `JITFlowLoop` class: prompt -> parse -> dispatch -> feedback, with iteration budget and observation history | written |
| `__init__.py` | Public surface | written |

## Wire format

The YAML grammar exposed to the LLM is the **serializable subset** of
the Flow ADT introduced in our prior work, plus the `ZipAll` variant:

| Type | Fields | Semantics |
|---|---|---|
| `Agent` | `cls`, `timeout?` | Invoke an `AgentActor` class from the registry |
| `FlatMap` | `first`, `next` | Sequential composition (output of `first` -> input of `next`) |
| `Zip` | `left`, `right` | Pair-wise parallel composition |
| `ZipAll` | `flows` (>= 2) | N-way parallel; result is a list |
| `Race` | `flows` (>= 2) | Competitive parallelism; first non-failure wins |
| `RecoverWith` | `source`, `handler` | Run `handler` if `source` fails |
| `FallbackTo` | `source`, `fallback` | Run `fallback` with original input if `source` fails |
| `Loop` | `body`, `max_iter` | Bounded iteration |

Variants that contain Python callables (`Pure`, `Map`, `Filter`,
`Recover`, `BranchOn`, `DivertTo`, `AndThen`) are intentionally
excluded from the wire format because the LLM cannot author Python
lambdas. The remaining variants are DAG-complete.

## Open questions

These are deliberately not solved in v0; they are listed here so future
sessions know what to tackle next.

1. **Type checking.** Right now `parser.py` only validates structure,
   not Python type compatibility across composition nodes. The paper
   claims compile-time type checking; we need to walk the AST and
   verify input/output types using `typing.get_type_hints` on each
   `AgentActor` subclass. This is the most important gap.
2. **Termination heuristic.** `_is_terminal` in `inference_loop.py`
   currently returns True for any non-None, non-exception result.
   The runner should override this with a task-specific check that
   asks "does this output satisfy the scorer?".
3. **Observation serialization.** `LoopOutcome.observations` are
   strings; structured observation objects (with token cost, latency,
   error class) would be more useful for analysis.
4. **Baselines.** ReAct, Plan-and-Execute, LangGraph, and AutoGen
   baselines are not implemented. Each needs a `PlannerCallable`
   conforming to `flowbench.runner.PlannerCallable`.
5. **LLM clients.** No concrete LLM client is wired; `LLMCallable`
   is an injection point. Connect to the Anthropic / OpenAI / vLLM
   backends in a separate module so the loop stays vendor-neutral.
