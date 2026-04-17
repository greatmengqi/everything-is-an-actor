"""Actor interpreter — evaluate Flow ADT into actor topology.

Recursive interpretation: each Flow variant maps to an actor operation.
Uses existing AgentSystem / ComposableFuture infrastructure.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from typing import TypeVar

from everything_is_an_actor.agents.agent_actor import AgentActor
from everything_is_an_actor.core.composable_future import ComposableFuture
from everything_is_an_actor.agents.system import AgentSystem
from everything_is_an_actor.agents.task import StreamEvent, StreamResult, Task, TaskEvent

from everything_is_an_actor.flow.flow import (
    Continue,
    Done,
    Flow,
    FlowFilterError,
    _Agent,
    _AndThen,
    _Branch,
    _BranchOn,
    _DivertTo,
    _FallbackTo,
    _Filter,
    _FlatMap,
    _Loop,
    _LoopWithState,
    _Map,
    _Pure,
    _Race,
    _Recover,
    _RecoverWith,
    _Zip,
    _ZipAll,
)

_log = logging.getLogger(__name__)

I = TypeVar("I")
O = TypeVar("O")


async def _divert_side(interpret_fn: object, side: object, result: object) -> None:
    """Fire-and-forget wrapper for DivertTo — logs errors instead of propagating."""
    try:
        await interpret_fn(side, result)  # type: ignore[misc]
    except Exception as exc:
        _log.error("DivertTo side flow failed: %s", exc, exc_info=exc)


class Interpreter:
    """Evaluate a Flow[I, O] program into actor operations.

    The interpreter holds the actor runtime (AgentSystem) as context.
    Flow is data; the interpreter gives it meaning.

    Usage::

        interpreter = Interpreter(system)
        result: str = await interpreter.run(pipeline, "hello")
    """

    __slots__ = ("_system",)

    def __init__(self, system: AgentSystem) -> None:
        self._system = system

    @property
    def system(self) -> AgentSystem:
        return self._system

    async def run(self, flow: Flow[I, O], input: I) -> O:
        """Interpret a Flow, returning the final result."""
        return await self._interpret(flow, input)

    async def run_stream(self, flow: Flow[I, O], input: I) -> AsyncIterator[TaskEvent]:
        """Interpret a Flow, yielding TaskEvent streams from agent nodes.

        Non-agent nodes (Pure, Map, etc.) execute silently — only _Agent
        nodes produce events.
        """
        async for event in self._interpret_stream(flow, input):
            yield event

    # ── Internal recursive evaluator ────────────────────────

    async def _interpret(self, flow: Flow, input: object) -> object:
        """Recursively interpret a Flow ADT node."""
        match flow:
            case _Agent(cls=cls, timeout=timeout):
                return await self._interpret_agent(cls, input, timeout=timeout)

            case _Pure(f=f):
                return f(input)

            case _Map(source=source, f=f):
                result = await self._interpret(source, input)
                return f(result)

            case _FlatMap(first=first, next=next_flow):
                mid = await self._interpret(first, input)
                return await self._interpret(next_flow, mid)

            case _Zip(left=left, right=right):
                left_input, right_input = input
                return await ComposableFuture(self._interpret(left, left_input)).zip(
                    ComposableFuture(self._interpret(right, right_input))
                )

            case _ZipAll(flows=flows):
                inputs = input  # expects a list/tuple of inputs matching flows
                return await ComposableFuture.sequence(
                    [ComposableFuture(self._interpret(f, inp)) for f, inp in zip(flows, inputs)]
                )

            case _Branch(source=source, mapping=mapping):
                value = await self._interpret(source, input)
                for typ, branch_flow in mapping.items():
                    if isinstance(value, typ):
                        return await self._interpret(branch_flow, value)
                raise KeyError(
                    f"Branch: no handler for {type(value).__name__}. Available: {[t.__name__ for t in mapping]}"
                )

            case _BranchOn(source=source, predicate=predicate, then=then_flow, otherwise=otherwise_flow):
                value = await self._interpret(source, input)
                if predicate(value):
                    return await self._interpret(then_flow, value)
                return await self._interpret(otherwise_flow, value)

            case _Race(flows=flows):
                return await ComposableFuture.race(*[ComposableFuture(self._interpret(f, input)) for f in flows])

            case _Recover(source=source, handler=handler):
                try:
                    return await self._interpret(source, input)
                except Exception as e:
                    return handler(e)

            case _RecoverWith(source=source, handler=handler):
                try:
                    return await self._interpret(source, input)
                except Exception as e:
                    return await self._interpret(handler, e)

            case _FallbackTo(source=source, fallback=fallback):
                try:
                    return await self._interpret(source, input)
                except Exception:
                    return await self._interpret(fallback, input)

            case _DivertTo(source=source, side=side, when=when):
                result = await self._interpret(source, input)
                if when(result):
                    ComposableFuture.fire_and_forget(_divert_side(self._interpret, side, result))
                return result

            case _AndThen(source=source, callback=callback):
                result = await self._interpret(source, input)
                callback(result)
                return result

            case _Filter(source=source, predicate=predicate):
                result = await self._interpret(source, input)
                if not predicate(result):
                    raise FlowFilterError(result)
                return result

            case _Loop(body=body, max_iter=max_iter):
                current = input
                for _ in range(max_iter):
                    result = await self._interpret(body, current)
                    match result:
                        case Done(value=value):
                            return value
                        case Continue(value=value):
                            current = value
                        case _:
                            raise TypeError(f"Loop body must return Continue or Done, got {type(result).__name__}")
                raise RuntimeError(f"Loop exceeded max_iter ({max_iter}) without producing Done")

            case _LoopWithState(body=body, init_state=init_state, max_iter=max_iter):
                state = init_state() if callable(init_state) else init_state
                current = input
                for _ in range(max_iter):
                    result = await self._interpret(body, (current, state))
                    # body returns (Continue/Done, new_state) tuple
                    if not isinstance(result, tuple) or len(result) != 2:
                        raise TypeError(
                            f"LoopWithState body must return (Continue|Done, state) tuple, got {type(result).__name__}"
                        )
                    control, state = result
                    match control:
                        case Done(value=value):
                            return value
                        case Continue(value=value):
                            current = value
                        case _:
                            raise TypeError(
                                f"LoopWithState body must return (Continue|Done, state), "
                                f"got ({type(control).__name__}, ...)"
                            )
                raise RuntimeError(f"LoopWithState exceeded max_iter ({max_iter}) without producing Done")

            case _:
                raise NotImplementedError(f"Interpreter does not handle {type(flow).__name__}")

    async def _interpret_agent(
        self,
        cls: type[AgentActor],
        input: object,
        *,
        timeout: float = 30.0,
    ) -> object:
        """Spawn an ephemeral actor, ask, stop, return output."""
        name = f"_flow-{cls.__name__}-{uuid.uuid4().hex[:8]}"
        ref = await self._system.spawn(cls, name)
        try:
            result = await ref._ask(Task(input=input), timeout=timeout)
            return result.get_or_raise()
        finally:
            ref.stop()
            await ref.join()
            self._system._root_cells.pop(name, None)

    # ── Streaming evaluator ─────────────────────────────────

    async def _interpret_stream(self, flow: Flow, input: object) -> AsyncIterator[TaskEvent]:
        """Recursively interpret a Flow, yielding TaskEvent streams."""
        match flow:
            case _Agent(cls=cls, timeout=timeout):
                async for event in self._interpret_agent_stream(cls, input, timeout=timeout):
                    yield event

            case _Pure():
                return  # no events from pure functions

            case _Map(source=source):
                async for event in self._interpret_stream(source, input):
                    yield event

            case _FlatMap(first=first, next=next_flow):
                first_result = await self._interpret(first, input)
                async for event in self._interpret_stream(next_flow, first_result):
                    yield event

            case _Recover(source=source, handler=handler):
                try:
                    async for event in self._interpret_stream(source, input):
                        yield event
                except Exception:
                    return  # recovery produces no events

            case _RecoverWith(source=source, handler=handler):
                try:
                    async for event in self._interpret_stream(source, input):
                        yield event
                except Exception as e:
                    async for event in self._interpret_stream(handler, e):
                        yield event

            case _FallbackTo(source=source, fallback=fallback):
                try:
                    async for event in self._interpret_stream(source, input):
                        yield event
                except Exception:
                    async for event in self._interpret_stream(fallback, input):
                        yield event

            case _Loop(body=body, max_iter=max_iter):
                current = input
                for _ in range(max_iter):
                    body_result = None
                    async for event in self._interpret_stream(body, current):
                        yield event
                        if event.type == "task_completed":
                            body_result = event.data
                    if body_result is None:
                        body_result = await self._interpret(body, current)
                    match body_result:
                        case Done(value=value):
                            return
                        case Continue(value=value):
                            current = value

            case _:
                # For variants without streaming semantics (Zip, Race, Branch, etc.),
                # fall back to non-streaming interpret — no events yielded.
                return

    async def _interpret_agent_stream(
        self,
        cls: type[AgentActor],
        input: object,
        *,
        timeout: float = 30.0,
    ) -> AsyncIterator[TaskEvent]:
        """Spawn ephemeral actor, stream events via ask_stream, stop."""
        name = f"_flow-stream-{cls.__name__}-{uuid.uuid4().hex[:8]}"
        ref = await self._system.spawn(cls, name)
        try:
            async for item in ref._ask_stream(Task(input=input), timeout=timeout):
                match item:
                    case StreamEvent(event=event):
                        yield event
                    case StreamResult(result=result):
                        if result.is_failure():
                            raise RuntimeError(f"Agent {cls.__name__} failed: {result.error}")
        finally:
            ref.stop()
            await ref.join()
            self._system._root_cells.pop(name, None)
