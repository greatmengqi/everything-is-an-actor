# Paper 2: JIT Flow

Working draft, code, and benchmark for the follow-up paper to
*Beyond Graphs: Algebraic Primitives for Agent Orchestration*.

**Working title:** *JIT Flow: Inference-Time Compositional Planning with a Typed Plan Calculus for LLM Agents*

## What this paper claims

> Frontier LLMs, when prompted to emit Flow YAML expressions incrementally
> at inference time (parse → type-check → dispatch → feedback), achieve
> higher task success and higher fault-recovery on multi-agent tasks
> requiring parallel / race / quorum / recover composition than ReAct,
> LangGraph, AutoGen, and Plan-and-Execute baselines, **without any
> fine-tuning**.

The contribution is a third path between ReAct-style free-form planners
(no structure) and LangGraph-style static workflows (no dynamism):
**inference-time generation of typed algebraic plans**.

## Layout

```
paper2/
├── README.md              this file
├── ROADMAP.md             v0 -> v5 milestones, what each one needs to ship
├── paper.md               the paper draft (markdown, target: arXiv preprint)
├── flowbench/
│   ├── README.md          benchmark spec and design rationale
│   ├── schema.py          Pydantic schema for FlowBench task metadata
│   └── tasks/             pilot task set (v0: 5 hand-written tasks across 4 categories)
└── jit_flow/
    ├── README.md          architecture overview, wire format reference, open questions
    ├── __init__.py
    ├── prompt.py          prompt template + few-shot examples for Flow YAML generation
    ├── parser.py          YAML text → Flow AST (self-contained build, registry-resolved agents)
    ├── repair.py          one-shot repair loop on parser errors
    └── inference_loop.py  main JIT loop: prompt → parse → dispatch → feedback
```

## Status (v0 = documentation snapshot)

This snapshot is **doc-first**. Code under `flowbench/` and `jit_flow/`
is skeletons whose purpose is to pin down interfaces, not to run.
Implementation is sequenced in `ROADMAP.md`.

| Component | Status |
|-----------|--------|
| `paper.md` Sections 1-5, 7-9 | written |
| `paper.md` Section 6 (results) | protocol + falsifiable hypotheses; empirical fill-in is a v5 deliverable |
| `flowbench/README.md` (design rationale) | written |
| `flowbench/schema.py` (Pydantic) | written |
| `flowbench/tasks/*.yaml` (5 pilot tasks) | written |
| `flowbench/runner.py` | not yet written (v1 deliverable; needs scorer-callable design) |
| `flowbench/scorers.py` (per-task scorer functions) | not yet written (v1 deliverable) |
| `jit_flow/README.md` (architecture) | written |
| `jit_flow/prompt.py` | written |
| `jit_flow/parser.py` | written |
| `jit_flow/repair.py` | written |
| `jit_flow/inference_loop.py` | written |
| Type checker on Flow AST | not yet implemented (open question in `jit_flow/README.md`) |
| LLM client wiring | not yet implemented (v1 deliverable) |
| Baselines (ReAct / Plan-and-Execute / LangGraph / AutoGen) | not yet implemented (v3 deliverable) |

## How to use this directory

The paper is meant to be submittable to arXiv as a **method-first preprint
v1** that contains: motivation, related work, method, architecture,
benchmark design, experimental protocol, expected results, and limitations.

The empirical Section 6 should be filled in **v2** of the preprint, after:
1. Implementing the four baselines on FlowBench v0
2. Expanding FlowBench to ~60 tasks (15 per category)
3. Running the full 4×3×60×3 = 2160-run sweep
4. Producing the headline tables and ablation studies

## Build / submission targets

- **arXiv v1** (method-first preprint): submit when paper draft + FlowBench v0 + reference implementation are stable
- **arXiv v2** (full empirical): submit when Section 6 is filled in
- **Conference target**: NeurIPS Workshop on Foundation Models for Decision Making, ICLR Agentic AI workshop, or ACL ARR
