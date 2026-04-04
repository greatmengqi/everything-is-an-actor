"""Pluggable mailbox abstraction — Akka-inspired enqueue/dequeue interface.

Built-in implementations:
- ``MemoryMailbox``: asyncio.Queue backed (default)
- ``FastMailbox``: deque backed, lower overhead for single-threaded asyncio
- ``ThreadedMailbox``: queue.Queue backed, multi-threaded consumption
- Extend ``Mailbox`` for Redis, RabbitMQ, Kafka, etc.
"""

from __future__ import annotations

import abc
import asyncio
import queue
import threading
from collections import deque
from typing import Any


BACKPRESSURE_BLOCK = "block"
BACKPRESSURE_DROP_NEW = "drop_new"
BACKPRESSURE_FAIL = "fail"
BACKPRESSURE_POLICIES = {BACKPRESSURE_BLOCK, BACKPRESSURE_DROP_NEW, BACKPRESSURE_FAIL}


class Mailbox(abc.ABC):
    """Abstract mailbox — the message queue for an actor.

    Implementations must be async-safe for single-consumer usage.
    Multiple producers may call ``put`` concurrently.
    """

    @abc.abstractmethod
    async def put(self, msg: Any) -> bool:
        """Enqueue a message. Returns True if accepted, False if dropped."""

    @abc.abstractmethod
    def put_nowait(self, msg: Any) -> bool:
        """Non-blocking enqueue. Returns True if accepted, False if dropped."""

    @abc.abstractmethod
    async def get(self) -> Any:
        """Dequeue the next message. Blocks until available."""

    @abc.abstractmethod
    def get_nowait(self) -> Any:
        """Non-blocking dequeue. Raises ``Empty`` if no message."""

    @abc.abstractmethod
    def empty(self) -> bool:
        """Return True if no messages are queued."""

    @property
    @abc.abstractmethod
    def full(self) -> bool:
        """Return True if mailbox is at capacity."""

    async def put_batch(self, msgs: list[Any]) -> int:
        """Enqueue multiple messages. Returns count accepted.

        Default implementation falls back to sequential ``put`` calls.
        Backends like Redis should override this for efficient bulk push.
        """
        count = 0
        for msg in msgs:
            if await self.put(msg):
                count += 1
        return count

    async def close(self) -> None:
        """Release resources. Default is no-op."""


class Empty(Exception):
    """Raised by ``get_nowait`` when mailbox is empty."""


class MailboxClosed(Exception):
    """Raised when ``put``/``put_nowait`` is called on a closed mailbox."""


class MemoryMailbox(Mailbox):
    """In-process mailbox backed by ``asyncio.Queue``."""

    def __init__(self, maxsize: int = 256, *, backpressure_policy: str = BACKPRESSURE_BLOCK) -> None:
        if backpressure_policy not in BACKPRESSURE_POLICIES:
            raise ValueError(
                f"Invalid backpressure_policy={backpressure_policy!r}, expected one of {sorted(BACKPRESSURE_POLICIES)}"
            )
        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=maxsize)
        self._maxsize = maxsize
        self._backpressure_policy = backpressure_policy

    async def put(self, msg: Any) -> bool:
        if self._backpressure_policy == BACKPRESSURE_BLOCK:
            await self._queue.put(msg)
            return True
        if self._backpressure_policy in (BACKPRESSURE_DROP_NEW, BACKPRESSURE_FAIL):
            if self._queue.full():
                return False
            self._queue.put_nowait(msg)
            return True
        return False

    def put_nowait(self, msg: Any) -> bool:
        if self._queue.full():
            return False
        self._queue.put_nowait(msg)
        return True

    async def get(self) -> Any:
        return await self._queue.get()

    def get_nowait(self) -> Any:
        try:
            return self._queue.get_nowait()
        except asyncio.QueueEmpty:
            raise Empty("mailbox empty")

    def empty(self) -> bool:
        return self._queue.empty()

    @property
    def full(self) -> bool:
        return self._queue.full()


class FastMailbox(Mailbox):
    """In-process mailbox backed by ``collections.deque``.

    Lower overhead than ``MemoryMailbox`` for single-threaded asyncio use cases.

    When ``target_loop`` is set, ``put_nowait()`` uses ``call_soon_threadsafe``
    to wake the consumer — making it safe for cross-loop (cross-thread) producers.
    """

    def __init__(
        self,
        maxsize: int = 0,
        *,
        backpressure_policy: str = BACKPRESSURE_BLOCK,
        target_loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        if backpressure_policy not in BACKPRESSURE_POLICIES:
            raise ValueError(
                f"Invalid backpressure_policy={backpressure_policy!r}, expected one of {sorted(BACKPRESSURE_POLICIES)}"
            )
        self._queue: deque[Any] = deque(maxlen=maxsize if maxsize > 0 else None)
        self._maxsize = maxsize
        self._backpressure_policy = backpressure_policy
        self._get_event: asyncio.Event | None = None
        self._target_loop = target_loop

    def _signal(self) -> None:
        """Wake the consumer's get() — cross-loop safe when target_loop is set."""
        if self._get_event is None:
            return
        if self._target_loop is not None:
            try:
                current = asyncio.get_running_loop()
            except RuntimeError:
                current = None
            if current is not self._target_loop:
                self._target_loop.call_soon_threadsafe(self._get_event.set)
                return
        self._get_event.set()

    async def put(self, msg: Any) -> bool:
        if self._backpressure_policy == BACKPRESSURE_BLOCK:
            self._queue.append(msg)
            self._signal()
            return True
        if self._backpressure_policy in (BACKPRESSURE_DROP_NEW, BACKPRESSURE_FAIL):
            if len(self._queue) >= self._maxsize > 0:
                return False
            self._queue.append(msg)
            self._signal()
            return True
        return False

    def put_nowait(self, msg: Any) -> bool:
        if self._maxsize > 0 and len(self._queue) >= self._maxsize:
            return False
        self._queue.append(msg)
        self._signal()
        return True

    async def get(self) -> Any:
        while not self._queue:
            if self._get_event is None:
                self._get_event = asyncio.Event()
            self._get_event.clear()
            await self._get_event.wait()
        return self._queue.popleft()

    def get_nowait(self) -> Any:
        if not self._queue:
            raise Empty("mailbox empty")
        return self._queue.popleft()

    def empty(self) -> bool:
        return len(self._queue) == 0

    @property
    def full(self) -> bool:
        return self._maxsize > 0 and len(self._queue) >= self._maxsize


class ThreadedMailbox(Mailbox):
    """In-process mailbox with multi-threaded consumption.

    Multiple worker threads consume messages from the queue and process them
    in parallel. Each message is delivered to exactly one thread.

    The worker_fn is called with the message and must be thread-safe.

    Use when actor needs to process CPU-bound messages in parallel.
    """

    def __init__(
        self,
        maxsize: int = 0,
        *,
        num_workers: int = 4,
        backpressure_policy: str = BACKPRESSURE_BLOCK,
    ) -> None:
        if backpressure_policy not in BACKPRESSURE_POLICIES:
            raise ValueError(
                f"Invalid backpressure_policy={backpressure_policy!r}, expected one of {sorted(BACKPRESSURE_POLICIES)}"
            )
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=maxsize if maxsize > 0 else 0)
        self._maxsize = maxsize
        self._backpressure_policy = backpressure_policy
        self._num_workers = num_workers
        self._started = False
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        # Worker function - set by _ActorCell
        self._worker_fn: Any = None

    def set_worker(self, fn: Any) -> None:
        """Set the synchronous worker function to call for each message."""
        self._worker_fn = fn

    def start_workers(self) -> None:
        """Start worker threads. Called by _ActorCell."""
        if self._started or self._worker_fn is None:
            return
        self._started = True
        for i in range(self._num_workers):
            t = threading.Thread(target=self._worker_loop, name=f"worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def _worker_loop(self) -> None:
        """Worker thread loop."""
        while not self._stop_event.is_set():
            try:
                msg = self._queue.get(timeout=0.1)
                if self._worker_fn is not None:
                    self._worker_fn(msg)
            except queue.Empty:
                continue

    def _blocking_put(self, msg: Any) -> None:
        """Blocking put with stop-event awareness. Runs in executor thread."""
        while not self._stop_event.is_set():
            try:
                self._queue.put(msg, timeout=0.1)
                return
            except queue.Full:
                continue
        raise MailboxClosed("mailbox closed")

    async def put(self, msg: Any) -> bool:
        if self._stop_event.is_set():
            raise MailboxClosed("mailbox closed")
        if self._backpressure_policy == BACKPRESSURE_BLOCK:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._blocking_put, msg)
            return True
        if self._backpressure_policy in (BACKPRESSURE_DROP_NEW, BACKPRESSURE_FAIL):
            if self._maxsize > 0 and self._queue.full():
                return False
            self._queue.put_nowait(msg)
            return True
        return False

    def put_nowait(self, msg: Any) -> bool:
        if self._stop_event.is_set():
            raise MailboxClosed("mailbox closed")
        if self._maxsize > 0 and self._queue.full():
            return False
        self._queue.put_nowait(msg)
        return True

    def _blocking_get(self) -> Any:
        """Blocking get with stop-event awareness. Runs in executor thread."""
        while True:
            try:
                return self._queue.get(timeout=0.1)
            except queue.Empty:
                if self._stop_event.is_set():
                    # Drain remaining messages before raising
                    try:
                        return self._queue.get_nowait()
                    except queue.Empty:
                        raise Empty("mailbox closed")

    async def get(self) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._blocking_get)

    def get_nowait(self) -> Any:
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            if self._stop_event.is_set():
                raise Empty("mailbox closed")
            raise Empty("mailbox empty")

    def empty(self) -> bool:
        return self._queue.empty()

    @property
    def full(self) -> bool:
        if self._maxsize <= 0:
            return False
        return self._queue.full()

    async def close(self) -> None:
        self._stop_event.set()
        for t in self._threads:
            t.join(timeout=1.0)


# Type alias for mailbox factory
MailboxFactory = type[Mailbox] | Any  # Callable[[], Mailbox]
