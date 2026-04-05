"""MOA utilities — convenience functions for common patterns."""

from __future__ import annotations

from everything_is_an_actor.agents.task import TaskResult


def format_references(
    results: list[TaskResult[str]],
    *,
    include_failures: bool = False,
) -> str:
    """Format proposer outputs as a numbered list (Together-style prompt injection).

    This is a convenience for LLM-based aggregators, not a framework primitive.
    Aggregators can consume TaskResult lists directly without this helper.
    """
    lines: list[str] = []
    for i, r in enumerate(results, 1):
        if r.is_success():
            lines.append(f"{i}. {r.output}")
        elif include_failures:
            lines.append(f"{i}. [FAILED: {r.error}]")
    return "\n".join(lines)
