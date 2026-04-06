"""Core algebraic data types — Either, Try, and Applicative operations.

These are the type-level building blocks for the actor runtime:

- ``Either[E, A]``: sum type for typed error channels (Left = error, Right = success)
- ``Try[T]``: sum type for exception capture (Success = value, Failure = exception)
- Applicative operations: ``sequence``, ``traverse``, ``product``, ``map2``
"""

from collections.abc import Callable
from typing import NoReturn, TypeVar, Generic

E = TypeVar("E")  # Error type
A = TypeVar("A")  # Success type
B = TypeVar("B")
C = TypeVar("C")
F = TypeVar("F")
T = TypeVar("T")


# =============================================================================
# Either[E, A] — categorical sum type (E + A)
# =============================================================================


class Either(Generic[E, A]):
    """Sum type for typed error channels, analogous to Scala's Either.

    Left represents failure, Right represents success.
    Subclass only via Left/Right — do not extend directly.
    """

    __slots__ = ()

    @staticmethod
    def pure(value: A) -> "Either[E, A]":
        """Lift a value into Either — always Right (success)."""
        return Right(value)

    def is_left(self) -> bool:
        return isinstance(self, Left)

    def is_right(self) -> bool:
        return isinstance(self, Right)

    def map(self, f: Callable[[A], B]) -> "Either[E, B]":
        raise NotImplementedError

    def flatMap(self, f: Callable[[A], "Either[E, B]"]) -> "Either[E, B]":
        raise NotImplementedError

    def ap(self, f: "Either[E, Callable[[A], B]]") -> "Either[E, B]":
        raise NotImplementedError

    def join(self) -> "Either[E, A]":
        return self.flatMap(lambda x: x)

    def mapL(self, f: Callable[[E], F]) -> "Either[F, A]":
        raise NotImplementedError

    @staticmethod
    def sequence(eithers: list["Either[E, A]"]) -> "Either[E, list[A]]":
        """Applicative.sequence: list[Either[E, A]] → Either[E, list[A]]

        Short-circuits on first Left.
        """
        result: list[A] = []
        for e in eithers:
            if isinstance(e, Left):
                return e  # type: ignore[return-value]
            result.append(e.value)  # type: ignore[attr-defined]
        return Right(result)  # type: ignore[return-value]

    @staticmethod
    def traverse(items: list[F], f: Callable[[F], "Either[E, A]"]) -> "Either[E, list[A]]":
        """Applicative.traverse: list[F] → (F → Either[E, A]) → Either[E, list[A]]

        Maps each item through f, short-circuits on first Left.
        """
        result: list[A] = []
        for item in items:
            e = f(item)
            if isinstance(e, Left):
                return e  # type: ignore[return-value]
            result.append(e.value)  # type: ignore[attr-defined]
        return Right(result)  # type: ignore[return-value]

    @staticmethod
    def product(left: "Either[E, A]", right: "Either[E, B]") -> "Either[E, tuple[A, B]]":
        """Applicative.product: (F[A], F[B]) → F[(A, B)]

        Short-circuits on first Left.
        """
        if isinstance(left, Left):
            return left  # type: ignore[return-value]
        if isinstance(right, Left):
            return right  # type: ignore[return-value]
        return Right((left.value, right.value))  # type: ignore[return-value]

    @staticmethod
    def map2(
        left: "Either[E, A]",
        right: "Either[E, B]",
        f: Callable[[A, B], C],
    ) -> "Either[E, C]":
        """Applicative.map2: (F[A], F[B]) → (A → B → C) → F[C]

        Short-circuits on first Left.
        """
        if isinstance(left, Left):
            return left  # type: ignore[return-value]
        if isinstance(right, Left):
            return right  # type: ignore[return-value]
        return Right(f(left.value, right.value))  # type: ignore[return-value]


class Left(Either[E, A]):
    """Left (failure) branch of Either.

    Short-circuits all subsequent computations.
    """

    __slots__ = ("value",)

    def __init__(self, value: E) -> None:
        self.value = value

    def __repr__(self) -> str:
        return f"Left({self.value!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Left):
            return False
        return self.value == other.value

    def map(self, f: Callable[[A], B]) -> Either[E, B]:
        return self  # type: ignore[return-value]

    def flatMap(self, f: Callable[[A], Either[E, B]]) -> Either[E, B]:
        return self  # type: ignore[return-value]

    def ap(self, f: Either[E, Callable[[A], B]]) -> Either[E, B]:
        return self  # type: ignore[return-value]

    def join(self) -> Either[E, A]:
        return self

    def mapL(self, f: Callable[[E], F]) -> Either[F, A]:
        return Left(f(self.value))  # type: ignore[return-value]


class Right(Either[E, A]):
    """Right (success) branch of Either.

    Identity of the Either monad.
    """

    __slots__ = ("value",)

    def __init__(self, value: A) -> None:
        self.value = value

    def __repr__(self) -> str:
        return f"Right({self.value!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Right):
            return False
        return self.value == other.value

    def map(self, f: Callable[[A], B]) -> Either[E, B]:
        return Right(f(self.value))  # type: ignore[return-value]

    def flatMap(self, f: Callable[[A], Either[E, B]]) -> Either[E, B]:
        return f(self.value)  # type: ignore[return-value]

    def ap(self, f: Either[E, Callable[[A], B]]) -> Either[E, B]:
        if isinstance(f, Left):
            return f  # type: ignore[return-value]
        return Right(f.value(self.value))  # type: ignore[return-value]

    def join(self) -> Either[E, A]:
        return self  # type: ignore[return-value]

    def mapL(self, f: Callable[[E], F]) -> Either[F, A]:
        return self  # type: ignore[return-value]


# =============================================================================
# Try[T] — exception capture (Success | Failure)
# =============================================================================


class Try(Generic[T]):
    """Sum type for exception capture, analogous to Scala's Try.

    Success holds a value, Failure holds an exception.
    Subclass only via Success/Failure — do not extend directly.
    """

    __slots__ = ()

    def is_success(self) -> bool:
        return isinstance(self, Success)

    def is_failure(self) -> bool:
        return isinstance(self, Failure)

    def get(self) -> T:
        """Extract the value, or re-raise the exception."""
        raise NotImplementedError

    def get_or(self, default: T) -> T:
        raise NotImplementedError

    def map(self, f: Callable[[T], B]) -> "Try[B]":
        raise NotImplementedError

    def flatMap(self, f: Callable[[T], "Try[B]"]) -> "Try[B]":
        raise NotImplementedError

    def recover(self, f: Callable[[Exception], T]) -> "Try[T]":
        raise NotImplementedError

    def to_either(self) -> Either[Exception, T]:
        raise NotImplementedError


class Success(Try[T]):
    """Success branch of Try — holds a computed value."""

    __slots__ = ("value",)

    def __init__(self, value: T) -> None:
        self.value = value

    def get(self) -> T:
        return self.value

    def get_or(self, default: T) -> T:
        return self.value

    def map(self, f: Callable[[T], B]) -> Try[B]:
        try:
            return Success(f(self.value))  # type: ignore[return-value]
        except Exception as e:
            return Failure(e)  # type: ignore[return-value]

    def flatMap(self, f: Callable[[T], Try[B]]) -> Try[B]:
        try:
            return f(self.value)
        except Exception as e:
            return Failure(e)  # type: ignore[return-value]

    def recover(self, f: Callable[[Exception], T]) -> Try[T]:
        return self

    def to_either(self) -> Either[Exception, T]:
        return Right(self.value)

    def __repr__(self) -> str:
        return f"Success({self.value!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Success):
            return False
        return self.value == other.value


class Failure(Try[T]):
    """Failure branch of Try — holds a caught exception.

    T is phantom — Failure carries no value of type T, but inherits
    the type parameter so ``Failure[int]`` is a valid ``Try[int]``.
    """

    __slots__ = ("error",)

    def __init__(self, error: Exception) -> None:
        self.error = error

    def get(self) -> NoReturn:
        raise self.error

    def get_or(self, default: T) -> T:
        return default

    def map(self, f: Callable[[T], B]) -> Try[B]:
        return self  # type: ignore[return-value]

    def flatMap(self, f: Callable[[T], Try[B]]) -> Try[B]:
        return self  # type: ignore[return-value]

    def recover(self, f: Callable[[Exception], T]) -> Try[T]:
        try:
            return Success(f(self.error))
        except Exception as e:
            return Failure(e)

    def to_either(self) -> Either[Exception, T]:
        return Left(self.error)  # type: ignore[return-value]

    def __repr__(self) -> str:
        return f"Failure({self.error!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Failure):
            return False
        return self.error == other.error


def try_apply(f: Callable[[], T]) -> Try[T]:
    """Construct a Try by calling f — captures exceptions as Failure.

    Analogous to Scala's ``Try { expr }``.
    """
    try:
        return Success(f())
    except Exception as e:
        return Failure(e)


# Backward-compatible module-level aliases for Applicative operations
sequence = Either.sequence
traverse = Either.traverse
product = Either.product
map2 = Either.map2
