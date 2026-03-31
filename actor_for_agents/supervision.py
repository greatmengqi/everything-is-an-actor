"""Supervision strategies — Erlang/Akka-inspired fault tolerance."""

from __future__ import annotations

import enum
import time
from collections import deque
from collections.abc import Callable


class Directive(enum.Enum):
    """What a supervisor should do when a child fails."""

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
