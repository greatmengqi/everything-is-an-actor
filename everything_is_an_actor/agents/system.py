"""AgentSystem — event-streaming actor system for agent runs.

Wraps an ActorSystem with agent-specific capabilities:
validation, event streaming, and run management.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from everything_is_an_actor.flow.flow import Flow

from collections.abc import Callable

from everything_is_an_actor.core.actor import Actor, MsgT, RetT
from everything_is_an_actor.core.composable_future import ComposableFuture
from everything_is_an_actor.agents.agent_actor import AgentActor
from everything_is_an_actor.agents.card import AgentCard
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
        # Install the agent-layer stream adapter so ActorRef._ask_stream works
        # without core/ depending on agents.task / agents.run_stream.
        if system._stream_adapter is None:
            from everything_is_an_actor.agents.stream_adapter import AgentStreamAdapter

            system._stream_adapter = AgentStreamAdapter()

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
        actor_cls.__validate_spawn_class__(mode="agent")

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

    # ── Flow execution ────────────────────────────────

    async def run_flow(self, flow: Flow, input: object) -> object:
        """Execute a Flow program, returning the final result.

        Internally creates an Interpreter — callers need not import it.
        """
        from everything_is_an_actor.flow.interpreter import Interpreter

        return await Interpreter(self).run(flow, input)

    async def run_flow_stream(self, flow: Flow, input: object):  # type: ignore[return]
        """Execute a Flow program, yielding TaskEvent streams."""
        from everything_is_an_actor.flow.interpreter import Interpreter

        async for event in Interpreter(self).run_stream(flow, input):
            yield event

    # ── Discovery ─────────────────────────────────────────────

    def _catalog(self) -> list[tuple[ActorRef, AgentCard]]:
        """All spawned agents with their AgentCards — root + children."""
        results: list[tuple[ActorRef, AgentCard]] = []

        def _walk(cells: dict) -> None:
            for cell in cells.values():
                if (
                    hasattr(cell, "actor_cls")
                    and hasattr(cell.actor_cls, "__card__")
                    and isinstance(cell.actor_cls.__card__, AgentCard)
                ):
                    results.append((cell.ref, cell.actor_cls.__card__))
                if hasattr(cell, "children"):
                    _walk(cell.children)

        _walk(self._actor_system._root_cells)
        return results

    def discover_all(
        self,
        match: Callable[[list[tuple[ActorRef, AgentCard]]], list[tuple[ActorRef, AgentCard]]],
    ) -> list[tuple[ActorRef, AgentCard]]:
        """Select agents from the catalog via a match function.

        ``match`` receives all (ref, card) pairs and returns the selected subset.

        Example::

            system.discover_all(lambda agents: [
                (r, c) for r, c in agents if "translation" in c.skills
            ])
        """
        return match(self._catalog())

    def discover_one(
        self,
        match: Callable[[list[tuple[ActorRef, AgentCard]]], tuple[ActorRef, AgentCard] | None],
    ) -> tuple[ActorRef, AgentCard] | None:
        """Select a single agent from the catalog via a match function.

        ``match`` receives all (ref, card) pairs and returns the best one,
        or None if no match.

        Examples::

            # By skill
            system.discover_one(lambda agents:
                next(((r, c) for r, c in agents if "translation" in c.skills), None)
            )

            # By score
            system.discover_one(lambda agents:
                max(agents, key=lambda rc: my_score(rc[1])) if agents else None
            )
        """
        return match(self._catalog())

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

        ComposableFuture.fire_and_forget(_drive(), name=f"run:{run_id}")

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
