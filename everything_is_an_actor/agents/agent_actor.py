# Copyright (c) 2025 everything-is-an-actor contributors
#
# This file is part of actor-for-agents, a multi-language agent runtime system.
# The agents/ components are licensed under the Business Source License 1.1 (BSL).
# See LICENSE file in agents/ directory for details.
#
# Permitted uses:
# - Enterprise internal deployment
# - Personal learning/research
# - Fork for secondary development (not for selling competing SaaS)
# - Embedded as a component in your product
#
# Prohibited uses:
# - Building a competing "XX Agent Runtime Cloud" service
# - White-labeling as a standalone commercial product
#
# After 4 years from first publication, this will convert to Apache 2.0.

"""AgentActor — Level 4 of the progressive agent API."""

from __future__ import annotations

import inspect
import warnings
from abc import abstractmethod
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from everything_is_an_actor.core.actor import Actor
from everything_is_an_actor.agents.card import AgentCard
from everything_is_an_actor.agents.task import Task, TaskEvent, TaskResult, TaskStatus

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")

if TYPE_CHECKING:
    from everything_is_an_actor.core.ref import ActorRef


class AgentActor(Actor[Task[InputT], TaskResult[OutputT]], Generic[InputT, OutputT]):
    """Full-power agent actor (Level 4).

    Type parameters::

        I — input type  (the type of Task.input and execute()'s argument)
        O — output type (the return type of execute() and TaskResult.output)

    Override ``execute()`` for async logic or ``receive()`` for sync (blocking) logic.
    Optionally override ``on_started()``, ``on_stopped()``, ``on_restart()``.
    Do NOT override ``on_receive()`` — it is managed by the framework.

    Set ``__card__`` to declare capabilities for discovery::

        class MyAgent(AgentActor[str, str]):
            __card__ = AgentCard(skills=("search",), description="Searches the web")


    Example (async)::

        class SummaryAgent(AgentActor[str, str]):
            async def execute(self, input: str) -> str:
                chunks: list[str] = []
                async for chunk in llm.stream(input):
                    await self.emit_progress(chunk)
                    chunks.append(chunk)
                return "".join(chunks)

        system = ActorSystem("app")
        ref = await system.spawn(SummaryAgent, "summarizer")
        result: TaskResult[str] = await system.ask(ref, Task(input="long document..."))
        output: str = result.output

    Example (sync with blocking code)::

        class FileAgent(AgentActor[str, str]):
            def receive(self, input: str) -> str:
                # Runs in thread pool automatically, won't block other actors
                with open(input) as f:
                    return f.read()

        system = ActorSystem("app")
        ref = await system.spawn(FileAgent, "reader")
        result: TaskResult[str] = await system.ask(ref, Task(input="file.txt"))
        output: str = result.output

    """

    __card__: AgentCard = AgentCard()

    def __init__(self) -> None:
        super().__init__()
        self._current_task_id: str | None = None
        self._current_parent_task_id: str | None = None  # span link; set per on_receive call
        self._active_sink: ActorRef | None = None  # effective sink for the current on_receive call
        # Read from ContextVar set by AgentSystem.run() — propagates automatically
        # to all child actors via asyncio task context inheritance.
        # None in plain ActorSystem usage — events are silently dropped.
        from everything_is_an_actor.agents.run_stream import _run_event_sink

        self._event_sink: ActorRef | None = _run_event_sink.get()
        # Check if subclass implements sync receive() instead of async execute()
        self._has_sync_receive = (
            "receive" in self.__class__.__dict__
            and callable(self.__class__.__dict__["receive"])
            and not inspect.iscoroutinefunction(self.__class__.__dict__["receive"])
        )

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "on_receive" in cls.__dict__:
            warnings.warn(
                f"{cls.__name__}: do not override on_receive() in AgentActor subclasses. "
                "Implement execute() or receive() instead — the framework manages on_receive().",
                UserWarning,
                stacklevel=2,
            )

    @classmethod
    def __wrap_traverse_input__(cls, inp: Any) -> Any:
        """Wrap a raw input as a ``Task`` so ``ActorContext.traverse`` works
        against AgentActor without ``core/`` importing ``Task``.
        """
        return Task(input=inp)

    @classmethod
    def __validate_spawn_class__(cls, *, mode: str = "unknown") -> None:
        """Enforce AgentActor handler shape at spawn-time.

        - Async ``execute()`` is required; sync ``execute()`` is a hard error.
        - Sync ``receive()`` / ``on_receive()`` is deprecated — warn with
          migration path. Will become a hard error in a future version.
        """
        super().__validate_spawn_class__(mode=mode)

        from everything_is_an_actor.core.validation import find_sync_handler

        sync_handler = find_sync_handler(cls, "receive", "on_receive")
        if sync_handler is not None:
            defining_cls, attr = sync_handler
            warnings.warn(
                f"DEPRECATION: Actor '{cls.__name__}' has sync {attr}() "
                f"(defined in '{defining_cls.__name__}'). "
                "AgentActor now requires async execute() instead of sync receive(). "
                "This will become a hard error in a future version. "
                "Please migrate by implementing 'async def execute(self, input)' instead. "
                "See docs/COMPATIBILITY_MATRIX.md for migration guide.",
                DeprecationWarning,
                stacklevel=3,
            )

        if "execute" not in cls.__dict__:
            return

        execute_method = cls.__dict__.get("execute")
        if execute_method is not None and not (
            inspect.iscoroutinefunction(execute_method) or inspect.isasyncgenfunction(execute_method)
        ):
            raise TypeError(
                f"Actor '{cls.__name__}' has sync execute() method; "
                "AgentActor requires async execute(). "
                "Change 'def execute(self, input)' to 'async def execute(self, input)'."
            )

    # ------------------------------------------------------------------
    # Public API — override these
    # ------------------------------------------------------------------

    @abstractmethod
    async def execute(self, input: InputT) -> OutputT:
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
        parent = self.context.parent
        await self._emit_event(
            TaskEvent(
                type="task_progress",
                task_id=self._current_task_id,
                agent_path=self.context.self_ref.path,
                data=data,
                parent_task_id=self._current_parent_task_id,
                parent_agent_path=parent.path if parent is not None else None,
            )
        )

    # ------------------------------------------------------------------
    # Framework-managed — do not override
    # ------------------------------------------------------------------

    async def on_receive(self, message: Task[InputT]) -> TaskResult[OutputT]:
        if not isinstance(message, Task):
            raise TypeError(
                f"{type(self).__name__} expects Task, got {type(message).__name__}. "
                "Wrap your input: system.ask(ref, Task(input=your_data))"
            )

        from everything_is_an_actor.agents.run_stream import _current_task_id_var, _run_event_sink

        # Capture parent span before overwriting the ContextVar
        self._current_parent_task_id = _current_task_id_var.get()
        token = _current_task_id_var.set(message.id)

        # Per-ask sink (event_sink_ref) overrides actor-level sink (_event_sink).
        # Set the ContextVar so child actors spawned during execute() inherit the sink.
        self._active_sink = message.event_sink_ref or self._event_sink
        sink_token = _run_event_sink.set(self._active_sink)

        # Parent agent path: dispatch() makes the caller the supervision parent
        parent = self.context.parent
        parent_agent_path = parent.path if parent is not None else None

        self._current_task_id = message.id
        await self._emit_event(
            TaskEvent(
                type="task_started",
                task_id=message.id,
                agent_path=self.context.self_ref.path,
                parent_task_id=self._current_parent_task_id,
                parent_agent_path=parent_agent_path,
            )
        )
        try:
            gen_or_coro = self.execute(message.input)
            if inspect.isasyncgen(gen_or_coro):
                chunks: list[Any] = []
                async for chunk in gen_or_coro:
                    await self._emit_event(
                        TaskEvent(
                            type="task_chunk",
                            task_id=message.id,
                            agent_path=self.context.self_ref.path,
                            data=chunk,
                            parent_task_id=self._current_parent_task_id,
                            parent_agent_path=parent_agent_path,
                        )
                    )
                    chunks.append(chunk)
                output: Any = chunks
            else:
                output = await gen_or_coro
            result: TaskResult[OutputT] = TaskResult(task_id=message.id, output=output, status=TaskStatus.COMPLETED)
            await self._emit_event(
                TaskEvent(
                    type="task_completed",
                    task_id=message.id,
                    agent_path=self.context.self_ref.path,
                    data=output,
                    parent_task_id=self._current_parent_task_id,
                    parent_agent_path=parent_agent_path,
                )
            )
            return result
        except Exception as exc:
            await self._emit_event(
                TaskEvent(
                    type="task_failed",
                    task_id=message.id,
                    agent_path=self.context.self_ref.path,
                    data=str(exc),
                    parent_task_id=self._current_parent_task_id,
                    parent_agent_path=parent_agent_path,
                )
            )
            raise
        finally:
            self._current_task_id = None
            self._current_parent_task_id = None
            self._active_sink = None
            _current_task_id_var.reset(token)
            _run_event_sink.reset(sink_token)

    async def _emit_event(self, event: TaskEvent) -> None:
        if self._active_sink is not None:
            await self._active_sink._tell(event)
