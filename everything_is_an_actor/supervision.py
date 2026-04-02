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


class Either(Generic[E, A]):
    """Base class for Either — categorical sum type (E + A).

    This is an abstract base. Use Left or Right directly.

    Monad instance:
        pure: A → Either[E, A]  (Right)
        fmap: Either[E, A] → (A → B) → Either[E, B]
        join: Either[E, Either[E, A]] → Either[E, A]

    Applicative instance:
        pure: A → Either[E, A]  (Right)
        ap: Either[E, (A → B)] → Either[E, A] → Either[E, B]

    The E parameter is contravariant (error type), A is covariant (success type).
    """

    @staticmethod
    def pure(value: A) -> "Either[E, A]":
        """Lift a value into Either — always Right (success)."""
        return Right(value)

    @staticmethod
    def from_result(result: "Either[E, A]", default: A) -> A:
        """Extract value or return default if Left."""
        match result:
            case Left():
                return default
            case Right(value):
                return value

    def is_left(self) -> bool:
        """Check if this is a Left (failure)."""
        return isinstance(self, Left)

    def is_right(self) -> bool:
        """Check if this is a Right (success)."""
        return isinstance(self, Right)

    def get(self) -> A:
        """Extract value, raising if Left."""
        if isinstance(self, Left):
            raise ValueError("Cannot get() from Left")
        return self.value

    def get_or(self, default: A) -> A:
        """Extract value or return default."""
        if isinstance(self, Left):
            return default
        return self.value

    def map(self, f: Callable[[A], B]) -> "Either[E, B]":
        """Functor map — applies f only to Right branch.

        Left is a phantom type that propagates unchanged.
        """
        raise NotImplementedError

    def flatMap(self, f: Callable[[A], "Either[E, B]"]) -> "Either[E, B]":
        """Monad bind — chains Either computations.

        If Left, short-circuits and returns self.
        If Right, applies f to the value.
        """
        raise NotImplementedError

    def ap(self, f: "Either[E, Callable[[A], B]]") -> "Either[E, B]":
        """Applicative ap — applies Either function to Either value.

        Laws:
            ap(pure(f), x) ≡ fmap(x, f)
            ap(x, pure(y)) ≡ pure(f(y))  where f = identity
        """
        raise NotImplementedError

    def join(self) -> "Either[E, A]":
        """Monad join — flattens nested Either.

        join(Either[E, Either[E, A]]) → Either[E, A]
        """
        return self.flatMap(lambda x: x)

    def mapL(self, f: Callable[[E], F]) -> "Either[F, A]":
        """Map over the Left (error) branch — changes the error type.

        This is useful for error transformation:
            Left(str_error).mapL(JSONDecodeError) → Left(JSONDecodeError)
        """
        raise NotImplementedError


class Left(Generic[E, A]):
    """Left (failure) branch of Either — categorical notation: ⊥ or Failure.

    Left is the zero/monoid of the Either monad — it short-circuits
    all subsequent computations.

    Laws:
        Left(e).map(f) ≡ Left(e)                    — map is a constant functor
        Left(e).flatMap(f) ≡ Left(e)               — bind is also constant
        Left(e).ap(Func(f)) ≡ Left(e)               — ap is constant
    """

    __slots__ = ("value",)

    def __init__(self, value: E) -> None:
        self.value = value

    def is_left(self) -> bool:
        return True

    def is_right(self) -> bool:
        return False

    def __repr__(self) -> str:
        return f"Left({self.value!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Left):
            return False
        return self.value == other.value

    def map(self, f: Callable[[A], B]) -> "Either[E, B]":
        """Functor map — Left is a constant functor, f is never called."""
        return self  # type: ignore

    def flatMap(self, f: Callable[[A], "Either[E, B]"]) -> "Either[E, B]":
        """Monad bind — Left short-circuits, f is never called."""
        return self  # type: ignore

    def ap(self, f: "Either[E, Callable[[A], B]]") -> "Either[E, B]":
        """Applicative ap — Left is aApplicative, f is never applied."""
        return self  # type: ignore

    def join(self) -> "Either[E, A]":
        """Monad join — Left is already flattened."""
        return self

    def mapL(self, f: Callable[[E], F]) -> "Either[F, A]":
        """Map over the Left branch — transforms error type."""
        return Left(f(self.value))  # type: ignore


class Right(Generic[E, A]):
    """Right (success) branch of Either — categorical notation: ⊤ or Success.

    Right is the identity of the Either monad.

    Laws:
        Right(v).map(f) ≡ Right(f(v))               — f is applied
        Right(v).flatMap(f) ≡ f(v)                  — bind applies f
        Right(v).ap(Right(f)) ≡ Right(f(v))          — ap applies f
    """

    __slots__ = ("value",)

    def __init__(self, value: A) -> None:
        self.value = value

    def is_left(self) -> bool:
        return False

    def is_right(self) -> bool:
        return True

    def __repr__(self) -> str:
        return f"Right({self.value!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Right):
            return False
        return self.value == other.value

    def map(self, f: Callable[[A], B]) -> "Either[E, B]":
        """Functor map — applies f to the value."""
        return Right(f(self.value))  # type: ignore

    def flatMap(self, f: Callable[[A], "Either[E, B]"]) -> "Either[E, B]":
        """Monad bind — applies f to the value, flattens result."""
        return f(self.value)  # type: ignore

    def ap(self, f: "Either[E, Callable[[A], B]]") -> "Either[E, B]":
        """Applicative ap — if f is Right, applies it to value."""
        if isinstance(f, Left):
            return f  # type: ignore
        return Right(f.value(self.value))  # type: ignore

    def join(self) -> "Either[E, A]":
        """Monad join — unwraps nested Right."""
        return self  # type: ignore

    def mapL(self, f: Callable[[E], F]) -> "Either[F, A]":
        """Map over Left branch — Right is unchanged."""
        return self  # type: ignore


Either = Left[E, A] | Right[E, A]
"""Either[E, A] — the categorical sum type (E + A), analogous to Scala's Either.

Left represents failure (⊥), Right represents success (⊤).

Monad instance:
    pure: A → Either[E, A]  (Right)
    map: Either[E, A] → (A → B) → Either[E, B]
    flatMap: Either[E, A] → (A → Either[E, B]) → Either[E, B]

Applicative instance:
    pure: A → Either[E, A]  (Right)
    ap: Either[E, (A → B)] → Either[E, A] → Either[E, B]
    product: (Either[E, A], Either[E, B]) → Either[E, (A, B)]
"""


# =============================================================================
# Applicative operations — sequence and traverse
# =============================================================================

F = TypeVar("F")
B = TypeVar("B")
C = TypeVar("C")


def sequence(eithers: list[Either[E, A]]) -> Either[E, list[A]]:
    """Applicative.sequence: list[Either[E, A]] → Either[E, list[A]]

    Transforms a list of Eithers into an Either of list.
    Short-circuits on first Left.

    Laws:
        sequence(map(x, pure)) ≡ pure(x)         (if x is a list)
        sequence(map(x, pure)) ≡ pure(x)         (naturality)

    Example:
        sequence([Right(1), Right(2), Right(3)]) → Right([1, 2, 3])
        sequence([Right(1), Left("err"), Right(3)]) → Left("err")
    """
    result: list[A] = []
    for e in eithers:
        if isinstance(e, Left):
            return e  # short-circuit on first Left
        result.append(e.value)
    return Right(result)  # type: ignore


def traverse(items: list[F], f: Callable[[F], Either[E, A]]) -> Either[E, list[A]]:
    """Applicative.traverse: list[F] → (F → Either[E, A]) → Either[E, list[A]]

    Maps each item through f, collecting successes or short-circuiting on first Left.

    Laws:
        traverse(x, pure) ≡ pure(x)              (identity)
        traverse(x, λ y → pure(g(y))) ≡ pure(map(x, g))  (naturality)

    Example:
        traverse([1, 2, 3], lambda x: Right(x * 2)) → Right([2, 4, 6])
        traverse([1, 2, 3], lambda x: Left("err") if x == 2 else Right(x)) → Left("err")
    """
    result: list[A] = []
    for item in items:
        e = f(item)
        if isinstance(e, Left):
            return e
        result.append(e.value)
    return Right(result)  # type: ignore


def product(left: Either[E, A], right: Either[E, B]) -> Either[E, tuple[A, B]]:
    """Applicative.product: (F[A], F[B]) → F[(A, B)]

    Combines two Eithers into an Either of tuple.
    Short-circuits on first Left.

    Example:
        product(Right(1), Right(2)) → Right((1, 2))
        product(Left("err"), Right(2)) → Left("err")
    """
    if isinstance(left, Left):
        return left  # type: ignore
    if isinstance(right, Left):
        return right  # type: ignore
    return Right((left.value, right.value))  # type: ignore


def map2(
    left: Either[E, A],
    right: Either[E, B],
    f: Callable[[A, B], C],
) -> Either[E, C]:
    """Applicative.map2: (F[A], F[B]) → (A → B → C) → F[C]

    Combines two Eithers using a function.
    Short-circuits on first Left.

    Example:
        map2(Right(1), Right(2), lambda a, b: a + b) → Right(3)
        map2(Left("err"), Right(2), lambda a, b: a + b) → Left("err")
    """
    if isinstance(left, Left):
        return left  # type: ignore
    if isinstance(right, Left):
        return right  # type: ignore
    return Right(f(left.value, right.value))  # type: ignore


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
