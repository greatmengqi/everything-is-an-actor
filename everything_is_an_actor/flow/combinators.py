"""Flow constructors — entry points for building Flow ADT trees."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, TypeVar, Union

from everything_is_an_actor.flow.flow import (
    Continue,
    Done,
    Flow,
    _Agent,
    _Loop,
    _LoopWithState,
    _Pure,
    _Race,
    _ZipAll,
)

if TYPE_CHECKING:
    from everything_is_an_actor.agents.agent_actor import AgentActor

I = TypeVar("I")
O = TypeVar("O")


def agent(cls: type[AgentActor[I, O]], *, timeout: float = 30.0) -> Flow[I, O]:
    """Lift an AgentActor class into a Flow node."""
    return _Agent(cls=cls, timeout=timeout)


def pure(f: Callable[[I], O]) -> Flow[I, O]:
    """Lift a pure function into a Flow node."""
    return _Pure(f=f)


def zip_all(*flows: Flow) -> Flow:
    """N-way parallel — all flows run concurrently, results collected as list."""
    if len(flows) < 2:
        raise ValueError("zip_all() requires at least 2 flows")
    return _ZipAll(flows=tuple(flows))


def race(*flows: Flow[I, O]) -> Flow[I, O]:
    """Competitive parallelism — first to complete wins, others cancelled."""
    if len(flows) < 2:
        raise ValueError("race() requires at least 2 flows")
    return _Race(flows=tuple(flows))


def loop(body: Flow[I, Union[Continue[I], Done[O]]], *, max_iter: int = 10) -> Flow[I, O]:
    """tailRecM — iterate body until it returns Done, with safety bound."""
    return _Loop(body=body, max_iter=max_iter)


def loop_with_state(body: Flow, *, init_state: Any = None, max_iter: int = 10) -> Flow:
    """Trace — loop with explicit feedback state S."""
    return _LoopWithState(body=body, init_state=init_state, max_iter=max_iter)


from everything_is_an_actor.flow.quorum import at_least  # noqa: E402, F401
