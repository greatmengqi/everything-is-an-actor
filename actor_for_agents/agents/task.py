"""Task lifecycle types for the agent layer."""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Generic, TypeVar
from uuid import uuid4

if TYPE_CHECKING:
    from actor_for_agents.ref import ActorRef

InputT = TypeVar("InputT")  # input type
OutputT = TypeVar("OutputT")  # output type


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Task(Generic[InputT]):
    """A unit of work sent to an AgentActor.

    Args:
        input: The input data for the agent.
        id: Unique task identifier. Auto-generated if not provided.

    Example::

        task: Task[str] = Task(input="summarize this")
        task: Task[dict] = Task(input={"query": "actor model", "limit": 5})
    """

    input: InputT
    id: str = field(default_factory=lambda: uuid4().hex)
    event_sink_ref: ActorRef | None = field(default=None, repr=False, compare=False)


@dataclass
class TaskResult(Generic[OutputT]):
    """The outcome of a task execution.

    Returned by AgentActor.on_receive() after execute() completes.
    The type parameter ``OutputT`` matches the return type of execute().

    Example::

        result: TaskResult[str] = await ref.ask(Task(input="..."))
        output: str = result.output
    """

    task_id: str
    output: OutputT | None = None
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

    ``parent_task_id`` links this event to the calling agent's task,
    enabling trace reconstruction (e.g. Gantt charts, OpenTelemetry spans).
    None for the root agent of a run.
    """

    type: str
    task_id: str
    agent_path: str
    data: Any = None
    parent_task_id: str | None = None
    parent_agent_path: str | None = None


class StreamItem(ABC):
    """Sealed base for items yielded by ``ActorRef.ask_stream()``.

    Variants::

        StreamEvent(event: TaskEvent)   — intermediate lifecycle event
        StreamResult(result: TaskResult) — final outcome (last item in stream)

    Idiomatic consumer::

        async for item in ref.ask_stream(Task(input="...")):
            match item:
                case StreamEvent(event=e):
                    print(e.type, e.data)
                case StreamResult(result=r):
                    print(r.output)
    """


@dataclass(frozen=True)
class StreamEvent(StreamItem):
    """Wraps a TaskEvent emitted during execution."""

    event: TaskEvent


@dataclass(frozen=True)
class StreamResult(StreamItem, Generic[OutputT]):
    """Wraps the final TaskResult — always the last item in ask_stream()."""

    result: TaskResult[OutputT]


@dataclass
class ActorConfig:
    """Optional actor-level configuration for plain agent classes (Level 1-3).

    Used by AgentSystem.spawn() to configure mailbox and supervision.
    Ignored when spawning AgentActor subclasses (which configure via
    supervisor_strategy() directly).

    Example::

        class SearchAgent:
            __actor__ = ActorConfig(mailbox_size=64, max_restarts=5)

            async def execute(self, input: str) -> list[str]: ...
    """

    mailbox_size: int = 256
    max_restarts: int = 3
    within_seconds: float = 60.0
