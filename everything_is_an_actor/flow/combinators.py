"""Flow constructors — entry points for building Flow ADT trees."""

from __future__ import annotations

from typing import Any

from everything_is_an_actor.flow.flow import (
    Flow,
    _Agent,
    _Loop,
    _LoopWithState,
    _Pure,
    _Race,
)


def agent(cls: type) -> Flow:
    """Lift an AgentActor class into a Flow node."""
    return _Agent(cls=cls)


def pure(f: Any) -> Flow:
    """Lift a pure function into a Flow node."""
    return _Pure(f=f)


def race(*flows: Flow) -> Flow:
    """Competitive parallelism — first to complete wins, others cancelled."""
    if len(flows) < 2:
        raise ValueError("race() requires at least 2 flows")
    return _Race(flows=list(flows))


def loop(body: Flow, *, max_iter: int = 10) -> Flow:
    """tailRecM — iterate body until it returns Done, with safety bound."""
    return _Loop(body=body, max_iter=max_iter)


def loop_with_state(body: Flow, *, init_state: Any = None, max_iter: int = 10) -> Flow:
    """Trace — loop with explicit feedback state S."""
    return _LoopWithState(body=body, init_state=init_state, max_iter=max_iter)
