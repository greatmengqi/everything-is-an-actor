"""FlowBench task schema.

Every FlowBench task is a YAML document conforming to `BenchmarkTask`.
The schema is intentionally minimal and forces every task to declare:

  * which combinator family it tests (`category`)
  * which combinator a *correct* solution must include (`minimum_combinator`)
  * an automated scorer (rule-based or LLM-judge)
  * a wall-clock budget that rules out trivially serial solutions
  * an oracle field documenting the empirical serial runtime so reviewers
    can verify the budget is non-trivial

The schema is the contract: if a task cannot be expressed against this
schema, either the schema is wrong or the task does not belong in
FlowBench.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Union

from pydantic import BaseModel, Field, model_validator


class Category(str, Enum):
    PARALLEL_HEAVY = "parallel-heavy"
    RACE_HEAVY = "race-heavy"
    QUORUM_HEAVY = "quorum-heavy"
    RECOVER_HEAVY = "recover-heavy"


class Combinator(str, Enum):
    """Allowed minimum-combinator values.

    Each task declares the combinator that any *correct* Flow expression
    for it must include. The benchmark runner can use this to detect
    "structural cheating" --- e.g. a planner that solves a parallel-heavy
    task by sequential calls within the wall-clock budget by exploiting
    fast tool latency. If the wall-clock lever is set correctly such
    cheating is impossible, but the runner cross-checks anyway.
    """

    TRAVERSE = "traverse"
    ZIP_ALL = "zip_all"
    RACE = "race"
    RECOVER_WITH = "recover_with"
    AT_LEAST = "at_least"
    RECOVER = "recover"
    FALLBACK_TO = "fallback_to"
    BRANCH_ON = "branch_on"


_VALID_COMBINATORS_FOR_CATEGORY: dict[Category, set[Combinator]] = {
    Category.PARALLEL_HEAVY: {Combinator.TRAVERSE, Combinator.ZIP_ALL},
    Category.RACE_HEAVY: {Combinator.RACE, Combinator.RECOVER_WITH},
    Category.QUORUM_HEAVY: {Combinator.AT_LEAST},
    Category.RECOVER_HEAVY: {
        Combinator.RECOVER,
        Combinator.FALLBACK_TO,
        Combinator.BRANCH_ON,
    },
}


class Constraints(BaseModel):
    wall_clock_budget_s: float = Field(
        ...,
        description=(
            "Hard wall-clock budget per trial. Set so a strictly serial "
            "baseline cannot complete the task in time."
        ),
    )
    max_iterations: int = Field(
        5,
        description=(
            "Maximum number of JIT Flow iterations (LLM emit -> execute -> "
            "feedback) before the trial is marked as plan-failure."
        ),
    )


class Oracle(BaseModel):
    serial_baseline_runtime_s: float = Field(
        ...,
        description=(
            "Empirical wall-clock time of a hand-written serial solution. "
            "Must exceed `constraints.wall_clock_budget_s` so the budget "
            "rules out serial solutions."
        ),
    )
    notes: str | None = Field(
        None,
        description="Free-text notes on how the oracle runtime was measured.",
    )


class CallableScorer(BaseModel):
    type: Literal["callable"] = "callable"
    module: str = Field(
        ...,
        description=(
            "Python module path containing the scorer function, e.g. "
            "'paper2.flowbench.scorers'."
        ),
    )
    function: str = Field(
        ...,
        description=(
            "Function name within `module`. Signature: "
            "`(inputs: dict, output: Any) -> bool`. Returns True iff the "
            "trial passes."
        ),
    )


class LLMJudgeScorer(BaseModel):
    type: Literal["llm_judge"] = "llm_judge"
    judge_model: str = Field(
        ...,
        description=(
            "Judge model identifier. Must be DIFFERENT from any planner "
            "model used in the experiment, to avoid self-evaluation bias."
        ),
    )
    rubric: str = Field(
        ...,
        description="Natural-language rubric handed to the judge.",
    )
    n_judges: int = Field(
        3,
        description="Number of independent judge runs (majority vote).",
    )


Scorer = Union[CallableScorer, LLMJudgeScorer]


class GroundTruth(BaseModel):
    """Free-form ground truth.

    Concrete tasks specialize this with their own fields. Held as
    `dict[str, Any]` here so the schema does not need to know every
    task's expected output shape.
    """

    fields: dict[str, Any] = Field(default_factory=dict)


class BenchmarkTask(BaseModel):
    task_id: str = Field(
        ...,
        pattern=r"^(parallel|race|quorum|recover)_\d{3}$",
        description="Stable task identifier of the form `<category>_<NNN>`.",
    )
    category: Category
    minimum_combinator: Combinator
    prompt: str = Field(
        ...,
        description=(
            "Natural-language task statement handed to every planner. "
            "Must NOT mention the word 'parallel', 'race', 'quorum', "
            "'recover', or any combinator name --- the planner must "
            "discover the structure from the task semantics, not from "
            "leaked keywords."
        ),
    )
    inputs: dict[str, Any]
    ground_truth: GroundTruth
    scorer: Scorer = Field(..., discriminator="type")
    constraints: Constraints
    oracle: Oracle
    available_agents: list[str] = Field(
        ...,
        description=(
            "List of agent class names from the benchmark agent catalog "
            "that this task is allowed to invoke. The catalog itself is "
            "shared across the benchmark."
        ),
    )

    @model_validator(mode="after")
    def _validate(self) -> "BenchmarkTask":
        # 1. minimum_combinator must be in the set allowed for category
        allowed = _VALID_COMBINATORS_FOR_CATEGORY[self.category]
        if self.minimum_combinator not in allowed:
            raise ValueError(
                f"task {self.task_id}: combinator {self.minimum_combinator} "
                f"is not valid for category {self.category}; "
                f"allowed: {sorted(c.value for c in allowed)}"
            )

        # 2. wall-clock budget must be strictly less than oracle serial runtime
        if self.constraints.wall_clock_budget_s >= self.oracle.serial_baseline_runtime_s:
            raise ValueError(
                f"task {self.task_id}: wall-clock budget "
                f"({self.constraints.wall_clock_budget_s}s) must be strictly "
                f"less than serial oracle runtime "
                f"({self.oracle.serial_baseline_runtime_s}s); otherwise the "
                f"budget does not rule out serial solutions and the task "
                f"does not separate planners by combinator use."
            )

        # 3. prompt must not leak combinator names
        leaked = [
            kw for kw in ("parallel", "concurrent", "race", "quorum", "recover", "fallback")
            if kw in self.prompt.lower()
        ]
        if leaked:
            raise ValueError(
                f"task {self.task_id}: prompt leaks structural keywords "
                f"{leaked}; rewrite the prompt to express the same task "
                f"without telling the planner what shape to use."
            )

        return self


__all__ = [
    "BenchmarkTask",
    "CallableScorer",
    "Category",
    "Combinator",
    "Constraints",
    "GroundTruth",
    "LLMJudgeScorer",
    "Oracle",
    "Scorer",
]
