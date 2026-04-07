"""JIT Flow inference loop.

The main entry point that ties prompt + parser + repair + actor runtime
into a single iterative loop:

  1. Build the prompt from task statement, agent catalog, and prior
     observations.
  2. Call the LLM. Get back YAML text.
  3. Parse the text into a Flow AST. On parse error, run one repair
     attempt; if that also fails, mark the run as plan-failure and exit.
  4. Type-check (delegated to the runtime's existing checks).
  5. Dispatch the AST to the actor runtime via `AgentSystem.run_flow`.
  6. Capture the result as an observation, append to the prior list,
     and loop until either:
       - the LLM emits a terminal node (an Agent whose result is the
         final answer), or
       - the iteration budget is exhausted, or
       - a plan-failure occurs.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping

from .parser import ParseError, parse_flow_yaml
from .prompt import AgentSpec, build_prompt
from .repair import attempt_repair

LLMCallable = Callable[[str], Awaitable[str]]
"""Async callable: (prompt) -> raw LLM output text."""

DispatchCallable = Callable[[Any, Any], Awaitable[Any]]
"""Async callable: (flow_ast, input) -> result. Implemented by the test
harness as a thin wrapper over `AgentSystem.run_flow`."""


@dataclass(frozen=True)
class LoopConfig:
    max_iterations: int = 5
    wall_clock_budget_s: float = 30.0


@dataclass
class LoopOutcome:
    success: bool
    final_result: Any = None
    plan_failure_reason: str | None = None
    iterations_used: int = 0
    elapsed_s: float = 0.0
    raw_outputs: list[str] = field(default_factory=list)
    parsed_asts: list[Any] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    compile_time_catches: int = 0
    repair_attempts: int = 0


class JITFlowLoop:
    """Stateful inference-time loop.

    Each instance encapsulates one trial: one (model, task) pair, with
    its own configuration and observation history. Concurrent trials
    should use distinct instances.
    """

    def __init__(
        self,
        *,
        llm: LLMCallable,
        dispatch: DispatchCallable,
        agents: Mapping[str, AgentSpec],
        registry: Mapping[str, type],
        config: LoopConfig,
    ) -> None:
        self._llm = llm
        self._dispatch = dispatch
        self._agents = agents
        self._registry = registry
        self._config = config

    async def run(self, task_statement: str, task_input: Any) -> LoopOutcome:
        outcome = LoopOutcome(success=False)
        start = time.monotonic()
        observations: list[str] = []

        for iteration in range(self._config.max_iterations):
            outcome.iterations_used = iteration + 1

            elapsed = time.monotonic() - start
            if elapsed >= self._config.wall_clock_budget_s:
                outcome.plan_failure_reason = (
                    f"wall-clock budget exhausted after {elapsed:.1f}s "
                    f"(budget {self._config.wall_clock_budget_s}s)"
                )
                outcome.elapsed_s = elapsed
                return outcome

            prompt = build_prompt(
                task_statement=task_statement,
                agents=self._agents,
                prior_observations=observations or None,
            )
            raw_output = await self._llm(prompt)
            outcome.raw_outputs.append(raw_output)

            ast: Any
            try:
                ast = parse_flow_yaml(raw_output, self._registry)
            except ParseError as parse_error:
                outcome.compile_time_catches += 1
                outcome.repair_attempts += 1
                repair = await attempt_repair(
                    llm=self._llm,
                    original_prompt=prompt,
                    previous_output=raw_output,
                    error=parse_error,
                    registry=self._registry,
                )
                if not repair.success:
                    outcome.plan_failure_reason = (
                        f"parser+repair failed at iteration {iteration + 1}: "
                        f"{parse_error}"
                    )
                    outcome.elapsed_s = time.monotonic() - start
                    return outcome
                ast = repair.parsed_ast

            outcome.parsed_asts.append(ast)

            try:
                result = await self._dispatch(ast, task_input)
            except Exception as exc:
                # Runtime failure during dispatch is recorded as an
                # observation; the next iteration may try a different
                # plan. We do NOT mark this as a plan-failure: a plan
                # failure means the planner could not produce a valid
                # plan, which is distinct from a plan that ran but
                # produced a runtime exception.
                obs = f"iteration {iteration + 1}: runtime error -- {exc}"
                observations.append(obs)
                outcome.observations.append(obs)
                continue

            obs = f"iteration {iteration + 1}: produced {type(result).__name__}"
            observations.append(obs)
            outcome.observations.append(obs)

            # Heuristic terminal condition: a single-Agent or composed plan
            # whose result is the final answer for the task. The runner
            # decides whether the result satisfies the scorer; if so, we
            # exit successfully. The dispatch wrapper is responsible for
            # producing a terminal-shaped result on the last iteration.
            if _is_terminal(result):
                outcome.success = True
                outcome.final_result = result
                outcome.elapsed_s = time.monotonic() - start
                return outcome

        outcome.plan_failure_reason = (
            f"max_iterations ({self._config.max_iterations}) exhausted "
            "without producing a terminal result"
        )
        outcome.elapsed_s = time.monotonic() - start
        return outcome


def _is_terminal(result: Any) -> bool:
    """Heuristic: any non-None, non-exception result is terminal.

    The harness can override this by passing a wrapper that marks
    intermediate results explicitly. The simplest possible heuristic
    here keeps the loop minimal; the runner is the right place for
    smarter termination logic that depends on the task scorer.
    """
    return result is not None and not isinstance(result, BaseException)
