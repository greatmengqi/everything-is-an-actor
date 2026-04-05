"""Dispatcher — assigns actors to event loops.

Decouples actor execution context from ActorSystem lifecycle.
Inspired by Akka's Dispatcher model.

Built-in dispatchers:
- ``DefaultDispatcher``: all actors on caller's loop (zero overhead)
- ``PoolDispatcher``: round-robin across N worker loops (I/O isolation)
"""

from __future__ import annotations

import abc
import asyncio
import threading
from dataclasses import dataclass


@dataclass
class LoopContext:
    """Worker loop context — an event loop running in a dedicated thread."""

    loop: asyncio.AbstractEventLoop
    thread: threading.Thread


def _create_worker_loop(name: str) -> LoopContext:
    """Create a worker event loop in a daemon thread.

    Pattern reused from ``backends/multi_loop.py``.
    """
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def _run() -> None:
        asyncio.set_event_loop(loop)
        ready.set()
        loop.run_forever()

    thread = threading.Thread(target=_run, name=name, daemon=True)
    thread.start()
    ready.wait(timeout=5.0)
    return LoopContext(loop=loop, thread=thread)


class Dispatcher(abc.ABC):
    """Assigns actors to event loops.

    Subclass and override ``assign()`` for custom scheduling policies.
    """

    @abc.abstractmethod
    def assign(self, actor_cls: type, name: str) -> asyncio.AbstractEventLoop:
        """Return the event loop this actor should run on."""

    async def start(self) -> None:
        """Start the dispatcher (create threads/loops if needed)."""

    async def shutdown(self) -> None:
        """Shutdown the dispatcher (stop threads/loops)."""


class DefaultDispatcher(Dispatcher):
    """All actors run on the caller's event loop. Zero overhead."""

    def assign(self, actor_cls: type, name: str) -> asyncio.AbstractEventLoop:
        return asyncio.get_running_loop()


class PoolDispatcher(Dispatcher):
    """Round-robin actor assignment across N worker threads.

    Each worker thread runs its own asyncio event loop.
    Actors on different loops are isolated — one slow/blocking actor
    doesn't starve actors on other loops.

    Example::

        dispatcher = PoolDispatcher(pool_size=4)
        await dispatcher.start()
        system = ActorSystem("app", dispatcher=dispatcher)
        # actors are distributed across 4 worker loops
    """

    def __init__(self, pool_size: int = 4) -> None:
        if pool_size < 1:
            raise ValueError(f"pool_size must be >= 1, got {pool_size}")
        self._pool_size = pool_size
        self._workers: list[LoopContext] = []
        self._counter = 0
        self._started = False

    def assign(self, actor_cls: type, name: str) -> asyncio.AbstractEventLoop:
        if not self._started:
            raise RuntimeError("PoolDispatcher not started — call await dispatcher.start() first")
        loop = self._workers[self._counter % self._pool_size].loop
        self._counter += 1
        return loop

    async def start(self) -> None:
        if self._started:
            return
        for i in range(self._pool_size):
            self._workers.append(_create_worker_loop(f"dispatcher-worker-{i}"))
        self._started = True

    async def shutdown(self) -> None:
        if not self._started:
            return
        for w in self._workers:
            w.loop.call_soon_threadsafe(w.loop.stop)
        for w in self._workers:
            w.thread.join(timeout=5.0)
        self._workers.clear()
        self._counter = 0
        self._started = False
