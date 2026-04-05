"""MoASystem — high-level entry point for MOA pipelines."""

from __future__ import annotations

from collections.abc import AsyncIterator

from everything_is_an_actor.agents import AgentSystem
from everything_is_an_actor.agents.task import TaskEvent
from everything_is_an_actor.core.system import ActorSystem
from everything_is_an_actor.flow.flow import Flow


class MoASystem:
    """One-stop MOA entry point for users who don't need low-level control.

    Owns the full ``ActorSystem → AgentSystem`` lifecycle.
    Delegates flow execution to ``AgentSystem.run_flow``.
    """

    __slots__ = ("_agent_system",)

    def __init__(self) -> None:
        self._agent_system = AgentSystem(ActorSystem())

    async def run(self, flow: Flow, input: object) -> object:
        """Execute a MOA pipeline, returning the final result."""
        return await self._agent_system.run_flow(flow, input)

    async def run_stream(self, flow: Flow, input: object) -> AsyncIterator[TaskEvent]:
        """Execute a MOA pipeline, yielding TaskEvent streams."""
        async for event in self._agent_system.run_flow_stream(flow, input):
            yield event

    async def shutdown(self, *, timeout: float = 10.0) -> None:
        """Shut down the underlying actor system."""
        await self._agent_system.shutdown(timeout=timeout)
