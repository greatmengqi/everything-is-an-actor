"""Agent-layer concrete StreamAdapter.

Implements the core ``StreamAdapter`` protocol so ``ActorRef._ask_stream``
can drive AgentActor streaming without core/ knowing about Task or
StreamEvent. Installed automatically by ``AgentSystem.__init__``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from everything_is_an_actor.core.composable_future import ComposableFuture
from everything_is_an_actor.core.ref import ActorStoppedError

if TYPE_CHECKING:
    from everything_is_an_actor.core.ref import ActorRef


class AgentStreamAdapter:
    """Spawn a per-ask collector actor, inject it as the event sink, and
    multiplex ``TaskEvent`` objects back to the caller as ``StreamEvent``,
    with a terminal ``StreamResult``.

    This is the streaming logic that used to live inside ``ActorRef._ask_stream``
    — it was pulled out so ``core/`` no longer depends on ``agents/``.
    """

    async def ask_stream(
        self,
        ref: "ActorRef[Any, Any]",
        message: Any,
        *,
        timeout: float = 30.0,
    ) -> AsyncIterator[Any]:
        from everything_is_an_actor.agents.run_stream import RunStream, make_collector_cls
        from everything_is_an_actor.agents.task import StreamEvent, StreamResult, Task

        if ref._cell.stopped:
            raise ActorStoppedError(f"Actor {ref.path} is stopped")

        stream_id = uuid.uuid4().hex
        stream = RunStream()
        system = ref._cell.system

        collector_ref = await system.spawn(
            make_collector_cls(stream),
            f"_ask-collector-{stream_id}",
        )

        # Inject per-ask sink so on_receive picks it up without re-spawning the actor
        tagged = (
            Task(input=message.input, id=message.id, event_sink_ref=collector_ref)
            if isinstance(message, Task)
            else message
        )

        run_exc: list[BaseException] = []
        run_result: list[Any] = []

        async def _drive() -> None:
            try:
                run_result.append(await ref._ask(tagged, timeout=timeout))
            except Exception as exc:
                run_exc.append(exc)
            finally:
                collector_ref.stop()
                await collector_ref.join()
                system._root_cells.pop(f"_ask-collector-{stream_id}", None)
                await stream.close()

        ComposableFuture.fire_and_forget(_drive(), name=f"ask-stream:{stream_id}")

        async for event in stream:
            yield StreamEvent(event=event)

        if run_exc:
            raise run_exc[0]

        if run_result:
            yield StreamResult(result=run_result[0])
