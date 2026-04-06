"""Dispatcher — offloads handler execution via ComposableFuture.

Actor lifecycle (mailbox loop, on_started, on_stopped) always runs on the
caller's event loop.  A Dispatcher offloads the *handler execution*
to an executor, returning a ``ComposableFuture`` so the result
flows back through the standard composition pipeline.

Built-in:
- ``PoolDispatcher``: ``ThreadPoolExecutor``-backed dispatch.
"""

from __future__ import annotations

import abc
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TypeVar

from everything_is_an_actor.core.composable_future import ComposableFuture

T = TypeVar("T")


class Dispatcher(abc.ABC):
    """Offloads handler execution, returning ComposableFuture.

    Inject into ``ActorSystem(dispatchers={"io": PoolDispatcher(4)})``,
    then select at spawn time: ``system.spawn(MyActor, "a", dispatcher="io")``.

    The Cell calls ``dispatcher.dispatch(fn, *args)`` and awaits the result.
    How and where ``fn`` runs is the Dispatcher's concern.
    """

    @abc.abstractmethod
    def dispatch(self, fn: Callable[..., T], *args: Any) -> ComposableFuture[T]:
        """Dispatch a function, return ComposableFuture with the result."""

    async def start(self) -> None:
        """Start the dispatcher (create resources if needed)."""

    async def shutdown(self) -> None:
        """Shutdown the dispatcher (release resources)."""


class PoolDispatcher(Dispatcher):
    """Thread-pool dispatcher — offloads handler execution to a ``ThreadPoolExecutor``.

    Example::

        system = ActorSystem("app", dispatchers={"io": PoolDispatcher(4)})
        ref = await system.spawn(SlowActor, "worker", dispatcher="io")
    """

    def __init__(self, pool_size: int = 4) -> None:
        if pool_size < 1:
            raise ValueError(f"pool_size must be >= 1, got {pool_size}")
        self._pool_size = pool_size
        self._pool: ThreadPoolExecutor | None = None

    def dispatch(self, fn: Callable[..., T], *args: Any) -> ComposableFuture[T]:
        if self._pool is None:
            raise RuntimeError("PoolDispatcher not started — call await dispatcher.start() first")
        return ComposableFuture.from_executor(self._pool, fn, *args)

    async def start(self) -> None:
        if self._pool is None:
            self._pool = ThreadPoolExecutor(
                max_workers=self._pool_size,
                thread_name_prefix="dispatcher",
            )

    async def shutdown(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False)
            self._pool = None
