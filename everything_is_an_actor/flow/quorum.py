"""Quorum combinator — N-way parallel with minimum success threshold."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from everything_is_an_actor.flow.combinators import pure, zip_all
from everything_is_an_actor.flow.flow import Flow

I = TypeVar("I")
O = TypeVar("O")


@dataclass(frozen=True)
class QuorumResult(Generic[O]):
    """Quorum execution result, separating successes from failures."""

    succeeded: tuple[O, ...]
    failed: tuple[Exception, ...]


@dataclass(frozen=True)
class _Ok(Generic[O]):
    value: O


@dataclass(frozen=True)
class _Err:
    error: Exception


def _wrap_ok(value: object) -> _Ok:
    return _Ok(value)


def _wrap_err(e: Exception) -> _Err:
    if isinstance(e, MemoryError):
        raise
    return _Err(e)


def _split_and_validate(n: int):
    def validate(results: list) -> QuorumResult:
        succeeded = tuple(r.value for r in results if isinstance(r, _Ok))
        failed = tuple(r.error for r in results if isinstance(r, _Err))
        if len(succeeded) < n:
            raise RuntimeError(f"Quorum failed: {len(succeeded)} succeeded, {n} required")
        return QuorumResult(succeeded=succeeded, failed=failed)

    return validate


def at_least(n: int, *flows: Flow[I, O]) -> Flow[I, QuorumResult[O]]:
    """N-way parallel execution with quorum validation.

    Runs all *flows* concurrently with the same input.
    Succeeds if at least *n* flows succeed; raises ``RuntimeError`` otherwise.
    Domain exceptions are collected into ``QuorumResult.failed``;
    system exceptions (``MemoryError``) propagate immediately.
    """
    if n < 1:
        raise ValueError("n must be >= 1")
    if len(flows) < 1:
        raise ValueError("at_least requires at least 1 flow")
    if n > len(flows):
        raise ValueError(f"n ({n}) cannot exceed number of flows ({len(flows)})")

    wrapped = [f.map(_wrap_ok).recover(_wrap_err) for f in flows]

    if len(wrapped) == 1:
        return wrapped[0].map(lambda r: (r,)).map(_split_and_validate(n))

    k = len(wrapped)
    return pure(lambda x, _k=k: (x,) * _k).flat_map(zip_all(*wrapped)).map(_split_and_validate(n))
