"""FlowSystem — high-level entry point for Flow-based agent orchestration.

Wraps an AgentSystem with a Flow interpreter.
Users work with Flow programs; the underlying actor runtime is injected.

Usage::

    actor_system = ActorSystem()
    agent_system = AgentSystem(actor_system)
    system = FlowSystem(agent_system)
    result: str = await system.run(pipeline, "hello")
    await agent_system.shutdown()
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TypeVar

from everything_is_an_actor.agents.system import AgentSystem
from everything_is_an_actor.agents.task import TaskEvent
from everything_is_an_actor.flow.flow import Flow
from everything_is_an_actor.flow.interpreter import Interpreter

I = TypeVar("I")
O = TypeVar("O")


class FlowSystem:
    """Facade that composes AgentSystem + Interpreter.

    Flow is data; FlowSystem gives it meaning by interpreting it
    against an actor runtime.

    The AgentSystem is injected — FlowSystem does not own its lifecycle.
    """

    __slots__ = ("_agent_system", "_interpreter")

    def __init__(self, system: AgentSystem) -> None:
        self._agent_system = system
        self._interpreter = Interpreter(system)

    @property
    def agent_system(self) -> AgentSystem:
        """Access the underlying AgentSystem for low-level actor control."""
        return self._agent_system

    @property
    def interpreter(self) -> Interpreter:
        return self._interpreter

    async def run(self, flow: Flow[I, O], input: I) -> O:
        """Interpret a Flow program, returning the final result."""
        return await self._interpreter.run(flow, input)

    async def run_stream(self, flow: Flow[I, O], input: I) -> AsyncIterator[TaskEvent]:
        """Interpret a Flow program, yielding TaskEvent streams."""
        async for event in self._interpreter.run_stream(flow, input):
            yield event
