"""Actor interpreter — evaluate Flow ADT into actor topology.

Recursive interpretation: each Flow variant maps to an actor operation.
Uses existing AgentSystem / ComposableFuture infrastructure.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

_log = logging.getLogger(__name__)

from collections.abc import AsyncIterator

from everything_is_an_actor.agents.agent_actor import AgentActor
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


def _log_divert_error(task: asyncio.Task) -> None:
    """Log exceptions from fire-and-forget DivertTo tasks."""
    if not task.cancelled() and task.exception() is not None:
        _log.error("DivertTo side flow failed: %s", task.exception(), exc_info=task.exception())


async def interpret(flow: Flow, input: Any, system: AgentSystem) -> Any:
    """Recursively interpret a Flow ADT node into actor operations."""
    match flow:
        case _Agent(cls=cls, timeout=timeout):
            return await _interpret_agent(cls, input, system, timeout=timeout)

        case _Pure(f=f):
            return f(input)

        case _Map(source=source, f=f):
            result = await interpret(source, input, system)
            return f(result)

        case _FlatMap(first=first, next=next_flow):
            mid = await interpret(first, input, system)
            return await interpret(next_flow, mid, system)

        case _Zip(left=left, right=right):
            left_input, right_input = input
            left_task = asyncio.create_task(interpret(left, left_input, system))
            right_task = asyncio.create_task(interpret(right, right_input, system))
            try:
                left_result, right_result = await asyncio.gather(left_task, right_task)
            except Exception:
                left_task.cancel()
                right_task.cancel()
                await asyncio.gather(left_task, right_task, return_exceptions=True)
                raise
            return (left_result, right_result)

        case _ZipAll(flows=flows):
            inputs = input  # expects a list/tuple of inputs matching flows
            tasks = [
                asyncio.create_task(interpret(f, inp, system))
                for f, inp in zip(flows, inputs)
            ]
            try:
                return list(await asyncio.gather(*tasks))
            except Exception:
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise

        case _Branch(source=source, mapping=mapping):
            value = await interpret(source, input, system)
            for typ, branch_flow in mapping.items():
                if isinstance(value, typ):
                    return await interpret(branch_flow, value, system)
            raise KeyError(
                f"Branch: no handler for {type(value).__name__}. "
                f"Available: {[t.__name__ for t in mapping]}"
            )

        case _BranchOn(source=source, predicate=predicate, then=then_flow, otherwise=otherwise_flow):
            value = await interpret(source, input, system)
            if predicate(value):
                return await interpret(then_flow, value, system)
            return await interpret(otherwise_flow, value, system)

        case _Race(flows=flows):
            tasks = [asyncio.create_task(interpret(f, input, system)) for f in flows]
            try:
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for t in pending:
                    t.cancel()
                # Await cancelled tasks so actor cleanup (stop + join) runs
                await asyncio.gather(*pending, return_exceptions=True)
                return done.pop().result()
            except Exception:
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise

        case _Recover(source=source, handler=handler):
            try:
                return await interpret(source, input, system)
            except Exception as e:
                return handler(e)

        case _RecoverWith(source=source, handler=handler):
            try:
                return await interpret(source, input, system)
            except Exception as e:
                return await interpret(handler, e, system)

        case _FallbackTo(source=source, fallback=fallback):
            try:
                return await interpret(source, input, system)
            except Exception:
                return await interpret(fallback, input, system)

        case _DivertTo(source=source, side=side, when=when):
            result = await interpret(source, input, system)
            if when(result):
                task = asyncio.create_task(interpret(side, result, system))
                task.add_done_callback(_log_divert_error)
            return result

        case _AndThen(source=source, callback=callback):
            result = await interpret(source, input, system)
            callback(result)
            return result

        case _Filter(source=source, predicate=predicate):
            result = await interpret(source, input, system)
            if not predicate(result):
                raise FlowFilterError(result)
            return result

        case _Loop(body=body, max_iter=max_iter):
            current = input
            for _ in range(max_iter):
                result = await interpret(body, current, system)
                match result:
                    case Done(value=value):
                        return value
                    case Continue(value=value):
                        current = value
                    case _:
                        raise TypeError(
                            f"Loop body must return Continue or Done, got {type(result).__name__}"
                        )
            raise RuntimeError(f"Loop exceeded max_iter ({max_iter}) without producing Done")

        case _LoopWithState(body=body, init_state=init_state, max_iter=max_iter):
            state = init_state() if callable(init_state) else init_state
            current = input
            for _ in range(max_iter):
                result = await interpret(body, (current, state), system)
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
    cls: type[AgentActor], input: Any, system: AgentSystem, *, timeout: float = 30.0,
) -> Any:
    """Spawn an ephemeral actor, ask, stop, return output."""
    name = f"_flow-{cls.__name__}-{uuid.uuid4().hex[:8]}"
    ref = await system.spawn(cls, name)
    try:
        result = await ref._ask(Task(input=input), timeout=timeout)
        return result.get_or_raise()
    finally:
        ref.stop()
        await ref.join()


# ── Streaming interpreter ────────────────────────────────


async def interpret_stream(
    flow: Flow, input: Any, system: AgentSystem,
) -> AsyncIterator[TaskEvent]:
    """Interpret a Flow, yielding TaskEvent streams from all agent nodes.

    Yields TaskEvent objects (task_started, task_progress, task_chunk, etc.)
    as they are emitted by agents in the flow. The final value is accessible
    via the last task_completed event's data field.

    Non-agent nodes (Pure, Map, etc.) execute silently — only _Agent nodes
    produce events.
    """
    match flow:
        case _Agent(cls=cls, timeout=timeout):
            async for event in _interpret_agent_stream(cls, input, system, timeout=timeout):
                yield event

        case _Pure():
            return  # no events from pure functions

        case _Map(source=source, f=f):
            async for event in interpret_stream(source, input, system):
                yield event

        case _FlatMap(first=first, next=next_flow):
            # Interpret first (non-streaming) to get result without re-execution risk,
            # then stream the next step. Events from first's agents are yielded via
            # the next step's streaming — this avoids the double-execution bug where
            # Pure/Map nodes produce no events causing a fallback re-interpret.
            first_result = await interpret(first, input, system)
            async for event in interpret_stream(next_flow, first_result, system):
                yield event

        case _Recover(source=source, handler=handler):
            try:
                async for event in interpret_stream(source, input, system):
                    yield event
            except Exception:
                return  # recovery produces no events

        case _RecoverWith(source=source, handler=handler):
            try:
                async for event in interpret_stream(source, input, system):
                    yield event
            except Exception as e:
                async for event in interpret_stream(handler, e, system):
                    yield event

        case _FallbackTo(source=source, fallback=fallback):
            try:
                async for event in interpret_stream(source, input, system):
                    yield event
            except Exception:
                async for event in interpret_stream(fallback, input, system):
                    yield event

        case _Loop(body=body, max_iter=max_iter):
            current = input
            for _ in range(max_iter):
                # Stream body and capture result
                body_result = None
                async for event in interpret_stream(body, current, system):
                    yield event
                    if event.type == "task_completed":
                        body_result = event.data
                if body_result is None:
                    body_result = await interpret(body, current, system)
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
    cls: type[AgentActor], input: Any, system: AgentSystem, *, timeout: float = 30.0,
) -> AsyncIterator[TaskEvent]:
    """Spawn ephemeral actor, stream events via ask_stream, stop."""
    name = f"_flow-stream-{cls.__name__}-{uuid.uuid4().hex[:8]}"
    ref = await system.spawn(cls, name)
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
