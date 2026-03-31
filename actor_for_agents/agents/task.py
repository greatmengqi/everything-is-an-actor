"""Task lifecycle types for the agent layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Task:
    """A unit of work sent to an AgentActor.

    Args:
        input: The input data for the agent.
        id: Unique task identifier. Auto-generated if not provided.
    """

    input: Any
    id: str = field(default_factory=lambda: uuid4().hex)


@dataclass
class TaskResult:
    """The outcome of a task execution.

    Returned by AgentActor.on_receive() after execute() completes.
    """

    task_id: str
    output: Any = None
    error: str | None = None
    status: TaskStatus = TaskStatus.COMPLETED


@dataclass
class TaskEvent:
    """An event emitted during task execution.

    type:
        - ``task_started``   — execute() began
        - ``task_progress``  — intermediate update via emit_progress()
        - ``task_completed`` — execute() returned successfully
        - ``task_failed``    — execute() raised an exception
    """

    type: str
    task_id: str
    agent_path: str
    data: Any = None


@dataclass
class ActorConfig:
    """Optional actor-level configuration for plain agent classes (Level 1-3).

    Used by AgentSystem.spawn() to configure mailbox and supervision.
    Ignored when spawning AgentActor subclasses (which configure via
    supervisor_strategy() directly).

    Example::

        class SearchAgent:
            __actor__ = ActorConfig(mailbox_size=64, max_restarts=5)

            async def execute(self, input): ...
    """

    mailbox_size: int = 256
    max_restarts: int = 3
    within_seconds: float = 60.0
