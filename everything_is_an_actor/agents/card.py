"""AgentCard — static capability metadata for agent discovery.

Declare what an agent can do. Enables capability-based discovery
via ``AgentSystem.discover()``.

Usage::

    class TranslateAgent(AgentActor[str, str]):
        __card__ = AgentCard(
            name="translator",
            description="Translates documents between languages",
            skills=("translation", "summarization"),
        )
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentCard:
    """Static capability metadata for an agent.

    Fields are intentionally minimal — extend as needed.
    Discovery is predicate-based, so new fields are automatically queryable.
    """

    name: str = ""
    description: str = ""
    skills: tuple[str, ...] = ()
