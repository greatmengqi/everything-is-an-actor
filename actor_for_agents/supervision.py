"""Supervision strategies — Erlang/Akka-inspired fault tolerance.

The supervision layer implements categorical structures for fault recovery:

- Directive: A sum type (GADT-like) for recovery decisions
- SupervisorStrategy: A natural transformation between actor states
- The decider maps exceptions to directives — a pure function

Example (categorical view):
    Exception → [Directive] → Strategy.apply_to_children → AffectedActors

This is a natural transformation η: Exn → Directive, preserving the functor structure.
"""

from __future__ import annotations

import enum
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar, Generic

E = TypeVar("E")  # Error type
A = TypeVar("A")  # Success type


class Left(Generic[E, A]):
    """Left (error) branch of Either — categorical notation: ⊥ or Failure"""

    __slots__ = ("value",)

    def __init__(self, value: E) -> None:
        self.value = value

    def is_left(self) -> bool:
        return True

    def is_right(self) -> bool:
        return False

    def __repr__(self) -> str:
        return f"Left({self.value!r})"


class Right(Generic[E, A]):
    """Right (success) branch of Either — categorical notation: ⊤ or Success"""

    __slots__ = ("value",)

    def __init__(self, value: A) -> None:
        self.value = value

    def is_left(self) -> bool:
        return False

    def is_right(self) -> bool:
        return True

    def __repr__(self) -> str:
        return f"Right({self.value!r})"


Either = Left[E, A] | Right[E, A]
"""Either[E, A] — the categorical sum type (E + A), analogous to Scala's Either.

Left represents failure (⊥), Right represents success (⊤).

Monad instance:
    f: A → Either[E, B]
    map: Either[E, A] → (A → B) → Either[E, B]  (applies only to Right)
    flatMap: Either[E, A] → (A → Either[E, B]) → Either[E, B]  (Sequencing)
"""


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


class SupervisorStrategy:
    """Base class for supervision strategies.

    Args:
        max_restarts: Maximum restarts allowed within *within_seconds*.
            Exceeding this limit stops the child permanently.
        within_seconds: Time window for restart counting.
        decider: Maps exception → Directive. Default: always restart.
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
        self.decider = decider or (lambda _: Directive.restart)
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
