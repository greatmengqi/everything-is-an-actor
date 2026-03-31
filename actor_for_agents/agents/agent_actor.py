"""AgentActor — Level 4 of the progressive agent API."""

from __future__ import annotations

import warnings
from abc import abstractmethod
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from actor_for_agents.actor import Actor
from actor_for_agents.agents.task import Task, TaskEvent, TaskResult, TaskStatus

I = TypeVar("I")
O = TypeVar("O")

if TYPE_CHECKING:
    from actor_for_agents.ref import ActorRef


class AgentActor(Actor[Task[I]], Generic[I, O]):
    """Full-power agent actor (Level 4).

    Type parameters::

        I — input type  (the type of Task.input and execute()'s argument)
        O — output type (the return type of execute() and TaskResult.output)

    Override ``execute()`` to implement agent logic.
    Optionally override ``on_started()``, ``on_stopped()``, ``on_restart()``.
    Do NOT override ``on_receive()`` — it is managed by the framework.

    Example::

        class SummaryAgent(AgentActor[str, str]):
            async def execute(self, input: str) -> str:
                chunks: list[str] = []
                async for chunk in llm.stream(input):
                    await self.emit_progress(chunk)
                    chunks.append(chunk)
                return "".join(chunks)

        system = ActorSystem("app")
        ref = await system.spawn(SummaryAgent, "summarizer")
        result: TaskResult[str] = await ref.ask(Task(input="long document..."))
        output: str = result.output
    """

    def __init__(self) -> None:
        self._current_task_id: str | None = None
        # Injected by AgentSystem (M3) to route TaskEvents to a RunStream.
        # None in plain ActorSystem usage — events are silently dropped.
        self._event_sink: ActorRef | None = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "on_receive" in cls.__dict__:
            warnings.warn(
                f"{cls.__name__}: do not override on_receive() in AgentActor subclasses. "
                "Implement execute() instead — the framework manages on_receive().",
                UserWarning,
                stacklevel=2,
            )

    # ------------------------------------------------------------------
    # Public API — override these
    # ------------------------------------------------------------------

    @abstractmethod
    async def execute(self, input: I) -> O:
        """Implement agent logic here. Return value becomes TaskResult.output.

        Raise any exception to signal failure — the framework emits
        ``task_failed`` and supervision handles the restart.
        """

    async def emit_progress(self, data: Any) -> None:
        """Emit a ``task_progress`` event during execute().

        Use for streaming intermediate results to consumers.
        No-op if called outside of an active execute() call or
        if no event sink is attached.
        """
        if self._current_task_id is None:
            return
        await self._emit_event(
            TaskEvent(
                type="task_progress",
                task_id=self._current_task_id,
                agent_path=self.context.self_ref.path,
                data=data,
            )
        )

    # ------------------------------------------------------------------
    # Framework-managed — do not override
    # ------------------------------------------------------------------

    async def on_receive(self, message: Task[I]) -> TaskResult[O]:
        if not isinstance(message, Task):
            raise TypeError(
                f"{type(self).__name__} expects Task, got {type(message).__name__}. "
                "Wrap your input: ref.ask(Task(input=your_data))"
            )

        self._current_task_id = message.id
        await self._emit_event(
            TaskEvent(
                type="task_started",
                task_id=message.id,
                agent_path=self.context.self_ref.path,
            )
        )
        try:
            output = await self.execute(message.input)
            result: TaskResult[O] = TaskResult(task_id=message.id, output=output, status=TaskStatus.COMPLETED)
            await self._emit_event(
                TaskEvent(
                    type="task_completed",
                    task_id=message.id,
                    agent_path=self.context.self_ref.path,
                    data=output,
                )
            )
            return result
        except Exception:
            await self._emit_event(
                TaskEvent(
                    type="task_failed",
                    task_id=message.id,
                    agent_path=self.context.self_ref.path,
                )
            )
            raise
        finally:
            self._current_task_id = None

    async def _emit_event(self, event: TaskEvent) -> None:
        if self._event_sink is not None:
            await self._event_sink.tell(event)
