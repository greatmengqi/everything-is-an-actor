"""OrchestratorActor — M2 of the progressive agent API."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Generic, TypeVar

from actor_for_agents.agents.agent_actor import AgentActor
from actor_for_agents.agents.task import Task, TaskResult

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


class OrchestratorActor(AgentActor[InputT, OutputT], Generic[InputT, OutputT]):
    """Coordinates multiple child agents with built-in dispatch primitives.

    Replaces the ThreadPoolExecutor + polling pattern common in multi-agent systems.
    Children are spawned via ``context.spawn()`` — compatible with M3 AgentSystem
    routing and M5 distributed backends.

    Usage::

        class ResearchOrchestrator(OrchestratorActor[list[str], str]):
            async def execute(self, queries: list[str]) -> str:
                results = await self.dispatch_parallel([
                    (SearchAgent, q) for q in queries
                ])
                return summarize(results)

    Override ``execute()`` to implement orchestration logic.
    Use ``dispatch()`` and ``dispatch_parallel()`` to fan out work to child agents.
    """

    async def dispatch(
        self,
        agent_cls: type[AgentActor],
        input: Any,
        *,
        timeout: float = 300.0,
    ) -> Any:
        """Spawn a child agent, send it a task, and await its result.

        The child actor is stopped after the task completes or fails,
        keeping the orchestrator's child set clean.

        Args:
            agent_cls: The AgentActor subclass to spawn.
            input: The input value passed to the child's ``execute()``.
            timeout: Seconds to wait for the child to respond.

        Returns:
            The ``output`` field of the child's ``TaskResult``.

        Raises:
            asyncio.TimeoutError: if the child doesn't respond within timeout.
            Exception: any exception raised by the child's ``execute()``.
        """
        name = f"{agent_cls.__name__.lower()}-{uuid.uuid4().hex[:8]}"
        ref = await self.context.spawn(agent_cls, name)
        try:
            result: TaskResult = await ref.ask(Task(input=input), timeout=timeout)
            return result.output
        finally:
            ref.stop()

    async def dispatch_parallel(
        self,
        tasks: list[tuple[type[AgentActor], Any]],
        *,
        timeout: float = 300.0,
    ) -> list[Any]:
        """Spawn multiple child agents concurrently and collect their results.

        Results are returned in the same order as ``tasks``.
        If any task raises, the exception propagates and remaining tasks are
        cancelled (standard ``asyncio.gather`` behaviour).

        Args:
            tasks: List of ``(agent_cls, input)`` pairs.
            timeout: Per-task timeout in seconds.

        Returns:
            Ordered list of output values from each child's ``execute()``.

        Raises:
            asyncio.TimeoutError: if any child doesn't respond within timeout.
            Exception: the first exception raised by any child's ``execute()``.
        """
        return list(await asyncio.gather(*[self.dispatch(agent_cls, inp, timeout=timeout) for agent_cls, inp in tasks]))
