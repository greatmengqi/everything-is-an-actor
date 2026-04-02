"""Task lifecycle types for the agent layer.

The task layer implements categorical structures for async computation:

- Task: A computation indexed by input type (input → F[output])
- TaskResult: Either-like structure for success/failure, typed by output
- TaskEvent: Coproduct type for event variants
- StreamItem: Sealed ADT for streaming results

These form a Kleisli triple / Monad structure:
    Task[I] → TaskResult[O]  (akin to IO monad)
    flatMap: Task[I] → (O → Task[J]) → Task[J]
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Generic, TypeVar

from uuid import uuid4

if TYPE_CHECKING:
    from everything_is_an_actor.ref import ActorRef

InputT = TypeVar("InputT")  # input type
OutputT = TypeVar("OutputT")  # output type
P = TypeVar("P")  # mapped output type


class TaskStatus(Enum):
    """Task completion status — a finite set (categorically: a finite sum type).

    The status transitions form a state machine:
        PENDING → RUNNING → COMPLETED
                           ↘ FAILED
                           ↘ CANCELLED
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskError:
    """Tagged error type for explicit error channels.

    Categorically: This is the error representation in Either[E, A].
    Encapsulates error type, message, and optional context.

    Use with TaskResult for explicit error handling:
        TaskResult[str] with error=TaskError(type=ValueError, message="invalid input")
    """

    __slots__ = ("error_type", "message", "context")

    def __init__(
        self,
        error_type: type[Exception],
        message: str,
        context: Any = None,
    ) -> None:
        self.error_type = error_type
        self.message = message
        self.context = context

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.error_type.__name__,
            "message": self.message,
            "context": self.context,
        }

    def __repr__(self) -> str:
        return f"TaskError({self.error_type.__name__}: {self.message})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TaskError):
            return False
        return self.error_type == other.error_type and self.message == other.message


@dataclass
class Task(Generic[InputT]):
    """A unit of work sent to an AgentActor.

    Categorically: Task[I] is the "input" to a Kleisli arrow (I → TaskResult[O]).
    The type parameter I indexes the computation by its input type.

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

    Categorically: TaskResult is an Either-like sum type (Success ⊕ Failure).

    - Success: output is populated, error is None, status is COMPLETED
    - Failure: error is populated, output is None, status is FAILED

    The type parameters track the computation:
    - OutputT: the output type

    Use is_success() / is_failure() for categorical decomposition.

    Example::

        result: TaskResult[str] = await ref.ask(Task(input="..."))
        match result:
            case TaskResult(output=o) if o is not None:
                print(f"Success: {o}")
            case TaskResult(error=e) if e is not None:
                print(f"Failed: {e}")
    """

    task_id: str
    output: OutputT | None = None
    error: Any = None
    status: TaskStatus = TaskStatus.COMPLETED

    def is_success(self) -> bool:
        """Returns True if this is a successful result (Right branch)."""
        return self.status == TaskStatus.COMPLETED and self.error is None

    def is_failure(self) -> bool:
        """Returns True if this is a failed result (Left branch)."""
        return self.status == TaskStatus.FAILED or self.error is not None

    def get_or_raise(self) -> OutputT:
        """Extract output, raising if failed — total function for successful results."""
        if self.is_failure():
            raise RuntimeError(f"Task failed: {self.error!r}")
        return self.output

    def map(self, f: Callable[[OutputT], P]) -> "TaskResult[P]":
        """Functor map — applies f only to successful results (Right functor).

        Law: map(id) = id, map(f ∘ g) = map(f) ∘ map(g)
        """
        if self.is_failure():
            return self  # type: ignore
        return TaskResult(
            task_id=self.task_id,
            output=f(self.output) if self.output is not None else None,
            error=self.error,
            status=self.status,
        )

    def flatMap(self, f: Callable[[OutputT], "TaskResult[P]"]) -> "TaskResult[P]":
        """Monad bind — chains a new TaskResult-producing function.

        Law: flatMap(pure(x)) = x, flatMap(f) ∘ flatMap(g) = flatMap(λx. g(x) >>= f)
        """
        if self.is_failure():
            return self  # type: ignore
        if self.output is None:
            return TaskResult(task_id=self.task_id, status=TaskStatus.FAILED, error="output is None")
        return f(self.output)

    @classmethod
    def pure(cls, value: OutputT, task_id: str | None = None) -> "TaskResult[OutputT]":
        """Applicative pure — wraps a value in a successful TaskResult."""
        return cls(task_id=task_id or uuid4().hex, output=value, status=TaskStatus.COMPLETED)

    def apply(self, f: Callable[[OutputT], P]) -> "TaskResult[P]":
        """Applicative ap — applies a wrapped function to this result.

        Equivalent to self.map(f), kept for Applicative consistency.
        """
        return self.map(f)


@dataclass
class TaskEvent:
    """An event emitted during task execution.

    Categorically: A product type with labeled fields (analogue of a record type).
    Each field is a lens accessor — use ``optics()`` for polymorphic updates.

    type:
        - ``task_started``   — execute() began
        - ``task_chunk``     — one yielded value from a streaming execute() (async generator)
        - ``task_progress``  — status/progress update via emit_progress()
        - ``task_completed`` — execute() returned successfully
        - ``task_failed``    — execute() raised an exception

    ``parent_task_id`` links this event to the calling agent's task,
    enabling trace reconstruction (e.g. Gantt charts, OpenTelemetry spans).
    None for the root agent of a run.

    For optics-compatible updates, use ``with_type()``, ``with_data()``, etc.:
        event.with_type("task_completed")  # returns new TaskEvent
        event.with_data(new_data)           # returns new TaskEvent
    """

    type: str
    task_id: str
    agent_path: str
    data: Any = None
    parent_task_id: str | None = None
    parent_agent_path: str | None = None

    def with_type(self, type: str) -> "TaskEvent":
        """Return a new TaskEvent with updated type (Lens: TaskEvent.type)."""
        return TaskEvent(
            type=type,
            task_id=self.task_id,
            agent_path=self.agent_path,
            data=self.data,
            parent_task_id=self.parent_task_id,
            parent_agent_path=self.parent_agent_path,
        )

    def with_data(self, data: Any) -> "TaskEvent":
        """Return a new TaskEvent with updated data (Lens: TaskEvent.data)."""
        return TaskEvent(
            type=self.type,
            task_id=self.task_id,
            agent_path=self.agent_path,
            data=data,
            parent_task_id=self.parent_task_id,
            parent_agent_path=self.parent_agent_path,
        )

    def with_parent(self, parent_task_id: str | None, parent_agent_path: str | None) -> "TaskEvent":
        """Return a new TaskEvent with updated parent context (Lens: TaskEvent.parent_*)."""
        return TaskEvent(
            type=self.type,
            task_id=self.task_id,
            agent_path=self.agent_path,
            data=self.data,
            parent_task_id=parent_task_id,
            parent_agent_path=parent_agent_path,
        )


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
