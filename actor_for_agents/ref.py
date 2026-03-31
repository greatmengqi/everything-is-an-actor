"""ActorRef — immutable, serializable reference to an actor."""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Any, Generic, TypeVar

if TYPE_CHECKING:
    from .system import _ActorCell

MsgT = TypeVar("MsgT")
RetT = TypeVar("RetT")


class ActorRef(Generic[MsgT, RetT]):
    """Immutable handle for sending messages to an actor.

    Type parameters ``MsgT`` and ``RetT`` match the actor's message and return types::

        ref: ActorRef[str, str] = await system.spawn(GreetActor, "greeter")
        result: str = await ref.ask("world")

    Users never construct this directly — it is returned by
    ``ActorSystem.spawn`` or ``ActorContext.spawn``.
    """

    __slots__ = ("_cell",)

    def __init__(self, cell: _ActorCell) -> None:
        self._cell = cell

    @property
    def name(self) -> str:
        return self._cell.name

    @property
    def path(self) -> str:
        return self._cell.path

    @property
    def is_alive(self) -> bool:
        return not self._cell.stopped

    async def tell(self, message: MsgT, *, sender: ActorRef[Any, Any] | None = None) -> None:
        """Fire-and-forget message delivery."""
        if self._cell.stopped:
            self._cell.system._dead_letter(self, message, sender)
            return
        await self._cell.enqueue(_Envelope(message, sender))

    async def ask(self, message: MsgT, *, timeout: float = 5.0) -> RetT:
        """Request-response with timeout.

        Uses correlation ID + ReplyRegistry instead of passing a Future
        through the mailbox. This makes ask work with any Mailbox backend
        (memory, Redis, RabbitMQ, etc.).

        Raises ``asyncio.TimeoutError`` if the actor doesn't reply in time.
        Raises the actor's exception if ``on_receive`` fails.
        """
        if self._cell.stopped:
            raise ActorStoppedError(f"Actor {self.path} is stopped")
        corr_id = uuid.uuid4().hex
        future = self._cell.system._replies.register(corr_id)
        try:
            envelope = _Envelope(message, sender=None, correlation_id=corr_id, reply_to=self._cell.system.system_id)
            await self._cell.enqueue(envelope)
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._cell.system._replies.discard(corr_id)

    def stop(self) -> None:
        """Request graceful shutdown."""
        self._cell.request_stop()

    async def join(self) -> None:
        """Wait until the actor has fully stopped (on_stopped completed).

        Pairs with ``stop()`` when callers need a hard lifecycle guarantee::

            ref.stop()
            await ref.join()  # on_stopped() has returned, children removed

        No-op if the actor has already stopped or was never started.
        On cancellation, the wait is abandoned but the actor continues its
        own shutdown — use ``system.shutdown()`` to force-stop stuck actors.
        """
        task = self._cell.task
        if task is None or task.done():
            return
        try:
            await asyncio.shield(task)
        except BaseException:
            pass  # cancelled or failed — actor will stop on its own

    def __repr__(self) -> str:
        alive = "alive" if self.is_alive else "dead"
        return f"ActorRef({self.path}, {alive})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ActorRef):
            return self._cell is other._cell
        return NotImplemented

    def __hash__(self) -> int:
        return id(self._cell)


class ActorStoppedError(Exception):
    """Raised when sending to a stopped actor via ask."""


class MailboxFullError(RuntimeError):
    """Raised when a message is rejected because the mailbox is at capacity."""


# ---------------------------------------------------------------------------
# Internal message wrappers (serializable — no Future objects)
# ---------------------------------------------------------------------------


class _Envelope:
    """Message envelope flowing through mailboxes.

    All fields are serializable (no asyncio.Future). This is what
    enables ask() to work across MQ-backed mailboxes.
    """

    __slots__ = ("payload", "sender", "correlation_id", "reply_to")

    def __init__(
        self,
        payload: Any,
        sender: ActorRef | None = None,
        correlation_id: str | None = None,
        reply_to: str | None = None,
    ) -> None:
        self.payload = payload
        self.sender = sender
        self.correlation_id = correlation_id
        self.reply_to = reply_to  # System ID of the caller (for cross-process reply routing)


class _Stop:
    """Sentinel placed on the mailbox to trigger graceful shutdown."""


# ---------------------------------------------------------------------------
# ReplyRegistry — maps correlation_id → Future (lives on ActorSystem)
# ---------------------------------------------------------------------------


class _ReplyRegistry:
    """In-memory registry mapping correlation IDs to Futures.

    Used by ask() to receive replies without putting Futures in the mailbox.
    """

    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Future[Any]] = {}

    def register(self, corr_id: str) -> asyncio.Future[Any]:
        """Create and register a Future for a correlation ID."""
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[corr_id] = future
        return future

    def resolve(self, corr_id: str, result: Any) -> None:
        """Complete a pending ask with a result."""
        future = self._pending.pop(corr_id, None)
        if future is not None and not future.done():
            future.set_result(result)

    def reject(self, corr_id: str, error: Exception) -> None:
        """Complete a pending ask with an error."""
        future = self._pending.pop(corr_id, None)
        if future is not None and not future.done():
            future.set_exception(error)

    def discard(self, corr_id: str) -> None:
        """Remove a pending entry (e.g. on timeout)."""
        self._pending.pop(corr_id, None)

    def reject_all(self, error: Exception) -> None:
        """Reject all pending asks (e.g. on system shutdown)."""
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()


# ---------------------------------------------------------------------------
# ReplyChannel — abstraction for routing replies (local or cross-process)
# ---------------------------------------------------------------------------


class _ReplyMessage:
    """Reply payload sent through ReplyChannel.

    Carries the original exception object for local delivery (preserves type).
    For cross-process serialization, use ``to_dict``/``from_dict``.
    """

    __slots__ = ("correlation_id", "result", "error", "exception")

    def __init__(
        self, correlation_id: str, result: Any = None, error: str | None = None, exception: Exception | None = None
    ) -> None:
        self.correlation_id = correlation_id
        self.result = result
        self.error = error
        self.exception = exception  # Original exception (local only, not serializable)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for cross-process transport (exception becomes string)."""
        return {"correlation_id": self.correlation_id, "result": self.result, "error": self.error}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> _ReplyMessage:
        return cls(d["correlation_id"], d.get("result"), d.get("error"))


class ReplyChannel:
    """Routes replies from actor back to the caller's ReplyRegistry.

    Default implementation: resolve locally (same process).
    Override ``send_reply`` for cross-process routing (e.g. via Redis pub/sub).
    """

    async def send_reply(self, reply_to: str, reply: _ReplyMessage, local_registry: _ReplyRegistry) -> None:
        """Deliver a reply to the system identified by *reply_to*.

        Default: assumes reply_to is the local system → resolve directly.
        Override for MQ-backed cross-process delivery.
        """
        if reply.exception is not None:
            # Local: preserve original exception type
            local_registry.reject(reply.correlation_id, reply.exception)
        elif reply.error is not None:
            # Cross-process: exception was serialized to string
            local_registry.reject(reply.correlation_id, RuntimeError(reply.error))
        else:
            local_registry.resolve(reply.correlation_id, reply.result)

    async def start_listener(self, system_id: str, registry: _ReplyRegistry) -> None:
        """Start listening for inbound replies (no-op for local)."""

    async def stop_listener(self) -> None:
        """Stop the reply listener (no-op for local)."""
