"""Supervision strategies — Erlang/Akka-inspired fault tolerance.

- Directive: A sum type for recovery decisions
- SupervisorStrategy: decides what to do when a child fails
- The decider maps exceptions to directives — a pure function
"""

from __future__ import annotations

import enum
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar, Generic

# Re-export Either types for backward compatibility
from everything_is_an_actor.core.types import (  # noqa: F401
    Either,
    Left,
    Right,
    sequence,
    traverse,
    product,
    map2,
)

E = TypeVar("E")


@dataclass(frozen=True)
class DirectiveResult(Generic[E]):
    """The result of a supervision decision — wraps the directive and any error context.

    Categorically: This is a pair (Directive × ErrorContext) forming a product type.
    """

    directive: "Directive"
    error_context: E | None = None


class Directive(enum.Enum):
    """What a supervisor should do when a child fails.

    Categorically: A finite sum type with 4 constructors.

    Each directive corresponds to a categorical transformation:
    - Resume: id (identity morphism — continue as-is)
    - Restart: Σ (sum — restart with fresh state)
    - Stop: 0 (zero/terminal — no further computation)
    - Escalate: ∇ (codiagonal — propagate to parent category)
    """

    resume = "resume"  # ignore error, keep processing
    restart = "restart"  # discard state, create fresh instance
    stop = "stop"  # terminate the child permanently
    escalate = "escalate"  # propagate to grandparent


# System-level exceptions that must never be restarted — restarting through
# a MemoryError / SystemError / KeyboardInterrupt loop is how processes turn
# into unrecoverable zombies. Domain exceptions from ``execute()`` remain
# restart-by-default.
_SYSTEM_LEVEL_EXCEPTIONS: tuple[type[BaseException], ...] = (
    MemoryError,
    SystemError,
    KeyboardInterrupt,
    SystemExit,
)


def _default_decider(error: Exception) -> "Directive":
    """Default exception → directive mapping.

    - ``MemoryError``/``SystemError``/``KeyboardInterrupt``/``SystemExit``:
      escalate — these indicate the runtime is in a degraded state no
      child restart can recover from.
    - Everything else: restart (domain bugs are assumed transient enough
      to warrant one fresh instance; the rate limiter stops the loop).
    """
    if isinstance(error, _SYSTEM_LEVEL_EXCEPTIONS):
        return Directive.escalate
    return Directive.restart


class SupervisorStrategy:
    """Base class for supervision strategies.

    Args:
        max_restarts: Maximum restarts allowed within *within_seconds*.
            Exceeding this limit stops the child permanently.
        within_seconds: Time window for restart counting.
        decider: Maps exception → Directive. Default: restart for domain
            exceptions, escalate for system-level exceptions
            (``MemoryError``, ``SystemError``, ``KeyboardInterrupt``,
            ``SystemExit``).
    """

    def __init__(
        self,
        *,
        max_restarts: int = 3,
        within_seconds: float = 60.0,
        decider: Callable[[Exception], Directive] | None = None,
    ) -> None:
        self.max_restarts = max_restarts
        self.within_seconds = within_seconds
        self.decider = decider or _default_decider
        self._restart_timestamps: dict[str, deque[float]] = {}

    def decide(self, error: Exception) -> Directive:
        return self.decider(error)

    def record_restart(self, child_name: str) -> bool:
        """Record a restart and return True if within limits."""
        now = time.monotonic()
        if child_name not in self._restart_timestamps:
            self._restart_timestamps[child_name] = deque()
        ts = self._restart_timestamps[child_name]
        # Purge old entries outside the window
        cutoff = now - self.within_seconds
        while ts and ts[0] < cutoff:
            ts.popleft()
        ts.append(now)
        return len(ts) <= self.max_restarts

    def apply_to_children(self, failed_child: str, all_children: list[str]) -> list[str]:
        """Return which children should be affected by the directive."""
        raise NotImplementedError


class OneForOneStrategy(SupervisorStrategy):
    """Only the failed child is affected."""

    def apply_to_children(self, failed_child: str, all_children: list[str]) -> list[str]:
        return [failed_child]


class AllForOneStrategy(SupervisorStrategy):
    """All children are affected when any one fails."""

    def apply_to_children(self, failed_child: str, all_children: list[str]) -> list[str]:
        return list(all_children)
