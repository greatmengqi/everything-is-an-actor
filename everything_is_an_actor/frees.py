"""Free Monad base types.

Provides the foundation for describing actor operations as pure data,
deferring execution to an interpreter.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar

A = TypeVar("A")
B = TypeVar("B")
F = TypeVar("F")


# ---------------------------------------------------------------------------
# Free ADT
# ---------------------------------------------------------------------------


class Free(ABC, Generic[F, A]):
    """Base for Free Monad — represents a suspended computation.

    Subclassed by the user to embed their own operations (e.g. ActorF).
    The Free structure forms aMonad when the base functor is itself a Functor.

    Laws (via the base functor):
        - pure(x).flatMap(f) = f(x)           (left identity)
        - x.flatMap(pure) = x                  (right identity)
        - x.flatMap(f).flatMap(g) = x.flatMap(lambda v: f(v).flatMap(g))  (associativity)
    """

    @staticmethod
    def pure(value: A) -> "Free[F, A]":
        """Lift a pure value into the Free monad (Pure variant)."""
        return Pure(value)

    def flatMap(self, f: Callable[[A], "Free[F, B]"]) -> "Free[F, B]":
        """Bind — continues the computation with f."""
        return FlatMap(self, f)

    def map(self, f: Callable[[A], B]) -> "Free[F, B]":
        """Functor map — implemented via flatMap for Free."""
        return self.flatMap(lambda x: Pure(f(x)))

    def ap(self, ff: "Free[F, Callable[[A], B]]") -> "Free[F, B]":
        """Applicative ap — apply a wrapped function to this wrapped value."""
        return ff.flatMap(lambda f: self.map(f))

    @abstractmethod
    def _run(self, interpreter: Callable[[F], Free[F, A]]) -> Free[F, A]:
        """Internal: run one step of the Free computation using an interpreter."""
        ...


@dataclass
class Pure(Free[F, A], Generic[F, A]):
    """Free variant — a pure value, no effects.

    Categorically: η(a) — the unit of the Free Monad.
    """

    value: A

    def _run(self, interpreter: Callable[[F], Free[F, A]]) -> Free[F, A]:
        return self


@dataclass
class FlatMap(Free[F, B], Generic[F, A, B]):
    """Free variant — a suspended computation awaiting flatMap continuation.

    Categorically: Free[F, A] >>= (A → Free[F, B]) = FlatMap(Free[F, A], A → Free[F, B])
    """

    thunk: Free[F, A]
    f: Callable[[A], Free[F, B]]

    def _run(self, interpreter: Callable[[F], Free[F, A]]) -> Free[F, B]:
        # Interpret the inner thunk, then apply f to its result
        return self.thunk._run(interpreter).flatMap(self.f)


@dataclass
class Suspend(Free[F, A], Generic[F, A]):
    """Free variant — a primitive functorial action awaiting interpretation.

    Categorically: this is the "suspension" of the base functor F.
    The interpreter decides what to do with the suspended action.
    """

    thunk: F

    def _run(self, interpreter: Callable[[F], Free[F, A]]) -> Free[F, A]:
        return interpreter(self.thunk)


# ---------------------------------------------------------------------------
# Free Monad utilities
# ---------------------------------------------------------------------------


def lift_free(fa: F) -> Free[F, A]:
    """Lift a base functor value into the Free monad (Suspend variant)."""
    return Suspend(fa)


def run_free(free: Free[F, A], interpreter: Callable[[F], Free[F, A]]) -> A:
    """Run a Free computation using an interpreter.

    The interpreter is a natural transformation η: F[A] → Free[F, A]
    that decides how each primitive action is executed.

    Uses trampolining to avoid stack overflow on deep Free chains.
    """
    current: Free[F, Any] = free
    while isinstance(current, FlatMap):
        # Execute one step: interpret the suspended action, then continue
        if isinstance(current.thunk, Suspend):
            current = current.thunk._run(interpreter).flatMap(current.f)
        elif isinstance(current.thunk, FlatMap):
            # Nested FlatMap: re-associate to avoid stack growth
            inner = current.thunk
            current = inner.thunk.flatMap(lambda x: inner.f(x).flatMap(current.f))
        else:
            # Pure or already-resolved: just continue
            current = current.thunk.flatMap(current.f)

    if isinstance(current, Pure):
        return current.value
    elif isinstance(current, Suspend):
        return run_free(current._run(interpreter), interpreter)
    else:
        raise RuntimeError(f"Unreachable Free state: {type(current)}")


def merge_flatmaps(free: Free[F, A]) -> Free[F, A]:
    """Flatten nested FlatMaps to a single FlatMap chain (for optimization)."""
    if isinstance(free, FlatMap):
        left = merge_flatmaps(free.thunk)
        if isinstance(left, FlatMap):
            # Associativity: (m >>= f) >>= g ≡ m >>= (x >>= f >>= g)
            return left.thunk.flatMap(lambda x: left.f(x).flatMap(free.f))
        return left.flatMap(free.f)
    elif isinstance(free, Suspend):
        return Suspend(free.thunk)
    elif isinstance(free, Pure):
        return free
    return free
