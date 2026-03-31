"""RunStream — async-iterable event stream for agent runs (M3)."""

from __future__ import annotations

import asyncio
import contextvars
from typing import TYPE_CHECKING

from actor_for_agents.actor import Actor
from actor_for_agents.agents.task import TaskEvent

if TYPE_CHECKING:
    from actor_for_agents.ref import ActorRef

# ContextVar injected by AgentSystem.run() so every AgentActor spawned
# within a run automatically routes TaskEvents to the RunStream collector.
# None in plain ActorSystem usage — events are silently dropped (existing behavior).
_run_event_sink: contextvars.ContextVar[ActorRef | None] = contextvars.ContextVar("_run_event_sink", default=None)

# ContextVar tracking the active task_id in the current async context.
# Set by AgentActor.on_receive() before calling execute(), propagated to
# child actor tasks via asyncio.create_task() context inheritance.
# Child agents read this as their parent_task_id for span linking.
_current_task_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("_current_task_id", default=None)


class RunStream:
    """Async-iterable stream of TaskEvents produced by an agent run.

    Consumed via ``async for``::

        async for event in system.run(MyAgent, "input"):
            print(event.type, event.agent_path)

    Closed automatically when the run completes or fails.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[TaskEvent | None] = asyncio.Queue()

    async def put(self, event: TaskEvent) -> None:
        """Enqueue an event. Called internally by the collector actor."""
        await self._queue.put(event)

    async def close(self) -> None:
        """Signal end-of-stream. Subsequent iteration raises StopAsyncIteration."""
        await self._queue.put(None)  # sentinel

    def __aiter__(self) -> RunStream:
        return self

    async def __anext__(self) -> TaskEvent:
        item = await self._queue.get()
        if item is None:
            raise StopAsyncIteration
        return item


class _EventCollectorActor(Actor[TaskEvent, None]):
    """Internal actor that funnels TaskEvents into a RunStream.

    One instance is spawned per AgentSystem.run() / ActorRef.ask_stream() call.
    Not part of the public API.

    Use ``make_collector_cls(stream)`` to get a concrete subclass bound to a
    specific stream — avoids post-spawn private attribute injection.
    """

    def __init__(self, stream: RunStream) -> None:
        super().__init__()
        self._stream = stream

    async def on_receive(self, message: TaskEvent) -> None:
        await self._stream.put(message)


def make_collector_cls(stream: RunStream) -> type[_EventCollectorActor]:
    """Return a concrete ``_EventCollectorActor`` subclass pre-bound to *stream*.

    The returned class satisfies the actor framework's no-arg constructor
    contract while capturing *stream* via closure — no post-spawn injection.
    """

    class _BoundCollector(_EventCollectorActor):
        def __init__(self) -> None:
            super().__init__(stream)

    return _BoundCollector
