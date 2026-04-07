"""One-shot repair loop for malformed LLM output.

When the parser or type checker rejects the LLM's first attempt, we
issue a single repair attempt with a focused prompt that includes:
  - the original task,
  - the offending output,
  - the structured error message.

We deliberately limit repair to ONE attempt. Unbounded repair loops are
a denial-of-service vector and they hide modeling weaknesses behind
brute force. The plan-failure rate after one repair attempt is one of
the headline metrics in the paper (Section 6.4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Mapping

from .parser import ParseError, parse_flow_yaml


@dataclass(frozen=True)
class RepairResult:
    success: bool
    parsed_ast: object | None
    repair_output: str
    repair_prompt: str


LLMCallable = Callable[[str], Awaitable[str]]
"""Async callable: (prompt) -> raw LLM output text."""


_REPAIR_TEMPLATE = """\
Your previous Flow YAML output could not be used. Below is the original
task, your previous output, and the exact error from the parser. Produce
a corrected Flow YAML expression in a single fenced ```yaml ... ``` block.
Do not apologize, do not explain, just emit the corrected YAML.

## Original task prompt
{original_prompt}

## Your previous output
{previous_output}

## Parser error
phase: {phase}
message: {message}

## Corrected output
"""


async def attempt_repair(
    *,
    llm: LLMCallable,
    original_prompt: str,
    previous_output: str,
    error: ParseError,
    registry: Mapping[str, type],
) -> RepairResult:
    """Issue exactly one repair attempt and return the outcome.

    The caller is expected NOT to retry on RepairResult.success == False.
    Logging the failure as a plan-failure metric is the caller's job.
    """
    repair_prompt = _REPAIR_TEMPLATE.format(
        original_prompt=original_prompt,
        previous_output=previous_output,
        phase=error.phase,
        message=error.message,
    )
    repair_output = await llm(repair_prompt)

    try:
        parsed = parse_flow_yaml(repair_output, registry)
    except ParseError:
        return RepairResult(
            success=False,
            parsed_ast=None,
            repair_output=repair_output,
            repair_prompt=repair_prompt,
        )

    return RepairResult(
        success=True,
        parsed_ast=parsed,
        repair_output=repair_output,
        repair_prompt=repair_prompt,
    )
