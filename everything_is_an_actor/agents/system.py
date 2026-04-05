"""AgentSystem — event-streaming actor system for agent runs.

Wraps an ActorSystem with agent-specific capabilities:
validation, event streaming, and run management.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

from everything_is_an_actor.core.actor import Actor, MsgT, RetT
from everything_is_an_actor.agents.agent_actor import AgentActor
from everything_is_an_actor.agents.run_stream import RunStream, _run_event_sink, make_collector_cls
from everything_is_an_actor.agents.task import Task, TaskEvent
from everything_is_an_actor.core.mailbox import Mailbox
from everything_is_an_actor.core.middleware import Middleware
from everything_is_an_actor.core.ref import ActorRef
from everything_is_an_actor.core.system import ActorSystem


class AgentSystem:
    """Agent-layer facade over ActorSystem.

    Adds spawn-time validation, event-streaming runs, and abort.
    The underlying ActorSystem is injected — AgentSystem does not own
    its lifecycle.

    Usage::

        actor_system = ActorSystem()
        system = AgentSystem(actor_system)
        async for event in system.run(ResearchOrchestrator, user_input):
            yield format_sse(event)
        await actor_system.shutdown()
    """

    __slots__ = ("_actor_system", "_active_runs")

    def __init__(self, system: ActorSystem) -> None:
        self._actor_system = system
        self._active_runs: dict[str, ActorRef] = {}

    @property
    def actor_system(self) -> ActorSystem:
        """Access the underlying ActorSystem for low-level control."""
        return self._actor_system

    @property
    def name(self) -> str:
        return self._actor_system.name

    @property
    def _root_cells(self) -> dict:
        """Delegate to ActorSystem internals (used by Interpreter cleanup)."""
        return self._actor_system._root_cells

    async def spawn(
        self,
        actor_cls: type[Actor[MsgT, RetT]],
        name: str,
        *,
        mailbox_size: int = 256,
        mailbox: Mailbox | None = None,
        middlewares: list[Middleware] | None = None,
        backend: str | None = None,  # Reserved for M5 distributed backends
    ) -> ActorRef[MsgT, RetT]:
        """Spawn a root-level actor with agent validation.

        ``backend`` is reserved for M5 (distributed actor execution).
        Currently ignored — all actors run in-process.
        """
        from everything_is_an_actor.core.validation import validate_agent_actor_compatibility

        validate_agent_actor_compatibility(actor_cls, mode="agent")

        return await self._actor_system.spawn(
            actor_cls,
            name,
            mailbox_size=mailbox_size,
            mailbox=mailbox,
            middlewares=middlewares,
        )

    async def shutdown(self, *, timeout: float = 10.0) -> None:
        """Shut down the underlying actor system."""
        await self._actor_system.shutdown(timeout=timeout)

    # ── Delegated ActorSystem methods ───────────────────────

    async def ask(self, target: ActorRef | str, message: Any, *, timeout: float = 30.0) -> Any:
        return await self._actor_system.ask(target, message, timeout=timeout)

    async def tell(self, target: ActorRef | str, message: Any) -> None:
        await self._actor_system.tell(target, message)

    async def ask_stream(self, target: ActorRef | str, message: Any, *, timeout: float = 30.0):  # type: ignore[return]
        async for item in self._actor_system.ask_stream(target, message, timeout=timeout):
            yield item

    async def get_actor(self, path: str) -> ActorRef | None:
        return await self._actor_system.get_actor(path)

    def on_dead_letter(self, callback: Any) -> None:
        self._actor_system.on_dead_letter(callback)

    @property
    def dead_letters(self) -> list:
        return self._actor_system.dead_letters

    async def run(
        self,
        agent_cls: type[AgentActor],
        input: Any,
        *,
        run_id: str | None = None,
        timeout: float = 600.0,
    ) -> AsyncIterator[TaskEvent]:
        """Start an agent run and stream all events from the actor tree.

        Yields ``TaskEvent`` objects as they are emitted by every agent in the tree.
        Stops when the root agent completes (or fails). Re-raises the root
        agent's exception (if any) after the stream is exhausted.

        Args:
            agent_cls: Root agent class to instantiate and drive.
            input: Input passed to the root agent as ``Task.input``.
            run_id: Unique run identifier. Auto-generated if not provided.
                    Use stable IDs to correlate logs across retries.
            timeout: Maximum seconds for the root agent to complete.

        Example::

            async for event in system.run(ResearchOrchestrator, query):
                if event.type == "task_progress":
                    print(event.data)
        """
        run_id = run_id or uuid.uuid4().hex

        if run_id in self._active_runs:
            raise ValueError(
                f"Run '{run_id}' is already active. Provide a unique run_id or wait for the existing run to finish."
            )

        stream = RunStream()
        collector_ref = await self.spawn(make_collector_cls(stream), f"_collector-{run_id}")

        # Set ContextVar so every AgentActor instantiated during this run
        # (including deeply nested children via dispatch) gets the sink automatically
        token = _run_event_sink.set(collector_ref)
        try:
            root_ref = await self.spawn(agent_cls, f"run-{run_id}")
        except Exception:
            _run_event_sink.reset(token)
            collector_ref.stop()
            await collector_ref.join()
            await stream.close()
            raise
        _run_event_sink.reset(token)

        self._active_runs[run_id] = root_ref

        # Drive the run in background; close stream when root agent finishes
        run_exc: list[BaseException] = []

        async def _drive() -> None:
            try:
                await root_ref._ask(Task(input=input), timeout=timeout)
            except Exception as exc:
                run_exc.append(exc)
            finally:
                root_ref.stop()
                await root_ref.join()
                collector_ref.stop()
                await collector_ref.join()
                # Remove from system registry (_ActorCell._shutdown only removes
                # from parent.children; root actors need explicit cleanup here)
                self._root_cells.pop(f"run-{run_id}", None)
                self._root_cells.pop(f"_collector-{run_id}", None)
                await stream.close()
                self._active_runs.pop(run_id, None)

        asyncio.create_task(_drive(), name=f"run:{run_id}")

        async for event in stream:
            yield event

        if run_exc:
            raise run_exc[0]

    async def abort(self, run_id: str) -> None:
        """Cancel a running agent tree identified by *run_id*.

        Stops the root actor. The background drive task detects the failure,
        closes the stream, and removes the run from the active registry.
        No-op if *run_id* is unknown or already finished.
        """
        ref = self._active_runs.get(run_id)
        if ref is not None:
            ref.stop()
            await ref.join()
