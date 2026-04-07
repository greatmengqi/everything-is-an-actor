"""Prompt template and few-shot examples for Flow YAML generation.

The grammar exposed to the LLM is the *serializable* subset of the Flow
ADT introduced in our prior work --- variants that contain Python
callables (Pure, Map, Recover, Filter, AndThen, BranchOn, DivertTo) are
intentionally excluded, because the LLM cannot author Python lambdas
that would round-trip through the parser. The remaining variants are
DAG-complete on their own (sketch in the paper), so the LLM still has
full expressive power over compositional plans.

This module is small but load-bearing: the few-shot examples must
cover every grammar node JIT Flow expects to see, otherwise the model
will not know how to compose them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class AgentSpec:
    """A typed entry in the agent catalog passed to the planner."""

    name: str
    input_type: str
    output_type: str
    description: str


_GRAMMAR_REFERENCE = """\
Flow YAML wire format (every node is a YAML mapping with a `type` field).

Leaves:
  {type: Agent, cls: <AgentClassName>}                  -- invoke an agent
  {type: Agent, cls: <AgentClassName>, timeout: <float>}-- with custom timeout

Sequential composition (output of `first` flows into input of `next`):
  {type: FlatMap, first: <node>, next: <node>}

Parallel composition (independent sub-plans):
  {type: Zip, left: <node>, right: <node>}              -- pair of two
  {type: ZipAll, flows: [<node>, <node>, ...]}          -- N >= 2, list result

Competitive parallelism (first to succeed wins, others cancelled):
  {type: Race, flows: [<node>, <node>, ...]}            -- N >= 2

Fault tolerance:
  {type: RecoverWith, source: <node>, handler: <node>}  -- handler runs if source fails
  {type: FallbackTo, source: <node>, fallback: <node>}  -- fallback runs with ORIGINAL input if source fails

Iteration (advanced; usually not needed):
  {type: Loop, body: <node>, max_iter: <int>}           -- body must return a Continue/Done value

Type rules:
  - In a FlatMap, the OUTPUT type of `first` must match the INPUT type of `next`.
  - In a Zip, both sides receive the SAME input; the result is a (left_out, right_out) pair.
  - In a Race, all branches must have the SAME input and SAME output type.
  - In a RecoverWith, source and handler must have the SAME output type.

Constraints on what you may emit:
  - You may NOT emit nodes with `type` other than the ones above.
  - You may NOT emit Python lambda functions or callable references.
  - You may ONLY reference `cls:` names that appear in the catalog below.
"""


_FEWSHOT_EXAMPLES = """\
Example 1 -- two-stage sequential pipeline:
```yaml
type: FlatMap
first: {type: Agent, cls: SearchAgent}
next:  {type: Agent, cls: SummarizerAgent}
```

Example 2 -- three independent lookups in parallel:
```yaml
type: ZipAll
flows:
  - {type: Agent, cls: YearLookupAgent}
  - {type: Agent, cls: AuthorLookupAgent}
  - {type: Agent, cls: CitationLookupAgent}
```

Example 3 -- racing two strategies for a single goal:
```yaml
type: Race
flows:
  - {type: Agent, cls: PyPIDirectAgent}
  - {type: Agent, cls: GoogleSearchAgent}
```

Example 4 -- five classifiers in parallel followed by an aggregator:
```yaml
type: FlatMap
first:
  type: ZipAll
  flows:
    - {type: Agent, cls: SentimentClassifierA}
    - {type: Agent, cls: SentimentClassifierB}
    - {type: Agent, cls: SentimentClassifierC}
    - {type: Agent, cls: SentimentClassifierD}
    - {type: Agent, cls: SentimentClassifierE}
next: {type: Agent, cls: QuorumAggregator}
```

Example 5 -- recover from a failing primary into a backup, then a final fallback:
```yaml
type: RecoverWith
source:
  type: RecoverWith
  source:  {type: Agent, cls: PrimaryCodeGen}
  handler: {type: Agent, cls: BackupCodeGen}
handler:   {type: Agent, cls: TemplateCodeGen}
```
"""


_OUTPUT_INSTRUCTIONS = """\
Emit exactly ONE Flow YAML expression representing the next sub-plan to
execute. Wrap the YAML in a fenced code block tagged `yaml`. Do not put
any other content inside the fence. Outside the fence you may write
one short line of commentary. The expression must:

  - reference only `cls:` names from the catalog above,
  - use only `type` values listed in the grammar above,
  - be well-typed (input/output types compatible at every composition).

If a previous iteration produced an observation, use it to choose the
next sub-plan rather than retrying the same node.
"""


def _format_catalog(agents: Mapping[str, AgentSpec]) -> str:
    lines = ["Available agents (input -> output : description):"]
    for name in sorted(agents):
        spec = agents[name]
        lines.append(
            f"  {spec.name}: {spec.input_type} -> {spec.output_type}    # {spec.description}"
        )
    return "\n".join(lines)


def build_prompt(
    *,
    task_statement: str,
    agents: Mapping[str, AgentSpec],
    prior_observations: list[str] | None = None,
) -> str:
    """Assemble the prompt for the LLM.

    Sections (in order):
      1. Task statement (the user-facing prompt from FlowBench)
      2. Agent catalog (typed)
      3. Flow grammar reference
      4. Few-shot examples
      5. Output format instructions
      6. (optional) Prior iteration observations
    """
    parts = [
        "## Task",
        task_statement,
        "",
        "## Catalog",
        _format_catalog(agents),
        "",
        "## Flow grammar",
        _GRAMMAR_REFERENCE,
        "",
        "## Few-shot examples",
        _FEWSHOT_EXAMPLES,
        "",
        "## Output",
        _OUTPUT_INSTRUCTIONS,
    ]
    if prior_observations:
        parts.append("")
        parts.append("## Prior observations from earlier iterations")
        for i, obs in enumerate(prior_observations, start=1):
            parts.append(f"  [{i}] {obs}")
    return "\n".join(parts)
