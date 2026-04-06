"""ActorRef — immutable, serializable reference to an actor."""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING, Any, Callable, Generic, TypeVar

from everything_is_an_actor.core.composable_future import ComposableFuture
from everything_is_an_actor.core.frees import Free, Suspend

if TYPE_CHECKING:
    from .system import _ActorCell
    from .actor_f import ActorF
else:
    ActorF = "ActorF"  # type: ignore[misc,assignment]

MsgT = TypeVar("MsgT")
RetT = TypeVar("RetT")


class ActorRef(Generic[MsgT, RetT]):
    """Immutable handle for sending messages to an actor.

    Type parameters ``MsgT`` and ``RetT`` match the actor's message and return types::

        ref: ActorRef[str, str] = await system.spawn(GreetActor, "greeter")
        result: str = await system.ask(ref, "world")

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

    async def _tell(self, message: MsgT, *, sender: ActorRef[Any, Any] | None = None) -> None:
        """Fire-and-forget message delivery (internal).

        Use ``system.tell(ref, msg)`` or ``actor.tell(target, msg)`` instead.
        """
        if self._cell.stopped:
            self._cell.system._dead_letter(self, message, sender)
            return
        await self._cell.enqueue(_Envelope(message, sender))

    def _ask(self, message: MsgT, *, timeout: float = 5.0) -> ComposableFuture[RetT]:
        """Request-response with timeout (internal). Returns ``ComposableFuture``.

        Use ``system.ask(ref, msg)`` or ``actor.ask(target, msg)`` instead.
        """

        async def _ask() -> RetT:
            if self._cell.stopped:
                raise ActorStoppedError(f"Actor {self.path} is stopped")
            corr_id = uuid.uuid4().hex
            cf = self._cell.system._replies.register(corr_id)
            try:
                envelope = _Envelope(message, sender=None, correlation_id=corr_id, reply_to=self._cell.system.system_id)
                await self._cell.enqueue(envelope)
                return await cf.with_timeout(timeout)
            finally:
                self._cell.system._replies.discard(corr_id)

        return ComposableFuture(_ask())

    async def _ask_stream(self, message: MsgT, *, timeout: float = 30.0):  # type: ignore[return]
        """Stream TaskEvents from a single ask to an AgentActor (internal).

        Use ``system.ask_stream(ref, msg)`` or ``actor.context.stream(target, msg)`` instead.
        """
        from everything_is_an_actor.agents.run_stream import RunStream, make_collector_cls
        from everything_is_an_actor.agents.task import StreamEvent, StreamResult, Task

        if self._cell.stopped:
            raise ActorStoppedError(f"Actor {self.path} is stopped")

        stream_id = uuid.uuid4().hex
        stream = RunStream()
        system = self._cell.system

        collector_ref = await system.spawn(make_collector_cls(stream), f"_ask-collector-{stream_id}")

        # Inject per-ask sink so on_receive picks it up without re-spawning the actor
        tagged = (
            Task(input=message.input, id=message.id, event_sink_ref=collector_ref)
            if isinstance(message, Task)
            else message
        )  # type: ignore[attr-defined]

        run_exc: list[BaseException] = []
        run_result: list[Any] = []

        async def _drive() -> None:
            try:
                run_result.append(await self._ask(tagged, timeout=timeout))
            except Exception as exc:
                run_exc.append(exc)
            finally:
                collector_ref.stop()
                await collector_ref.join()
                system._root_cells.pop(f"_ask-collector-{stream_id}", None)
                await stream.close()

        asyncio.create_task(_drive(), name=f"ask-stream:{stream_id}")

        async for event in stream:
            yield StreamEvent(event=event)

        if run_exc:
            raise run_exc[0]

        if run_result:
            yield StreamResult(result=run_result[0])

    def stop(self) -> None:
        """Request graceful shutdown."""
        self._cell.request_stop()

    def interrupt(self) -> None:
        """Cancel the actor's asyncio task immediately.

        No-op if the actor has already stopped.
        """
        task = self._cell.task
        if task is not None and not task.done():
            task.cancel()

    async def join(self) -> None:
        """Wait until the actor has fully stopped (on_stopped completed).

        No-op if the actor has already stopped or was never started.
        """
        task = self._cell.task
        if task is None or task.done():
            return
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            pass

    # -------------------------------------------------------------------------
    # Free Monad API — describe operations as pure data, execute later
    # -------------------------------------------------------------------------

    def free_ask(self, msg: MsgT) -> Free[ActorF, RetT]:
        """Lift an ask operation into the Free monad.

        Use with ``ActorSystem.run_free()`` for pure, composable workflows::

            async def workflow(ref: ActorRef) -> Free[ActorF, str]:
                return ref.free_ask("hello").flatMap(lambda r: ref.free_ask(r + " world"))

            result = await system.run_free(workflow)
        """
        from everything_is_an_actor.core.actor_f import AskF

        return Suspend(AskF(self, msg))  # type: ignore[return-value]

    def free_tell(self, msg: MsgT) -> Free[ActorF, None]:
        """Lift a tell operation into the Free monad.

        Use with ``ActorSystem.run_free()`` for pure, composable workflows::

            async def workflow(ref: ActorRef) -> Free[ActorF, None]:
                return ref.free_tell("hello")

            await system.run_free(workflow)
        """
        from everything_is_an_actor.core.actor_f import TellF

        return Suspend(TellF(self, msg))  # type: ignore[return-value]

    def free_stop(self) -> Free[ActorF, None]:
        """Lift a stop operation into the Free monad.

        Use with ``ActorSystem.run_free()`` for pure, composable workflows::

            async def workflow(ref: ActorRef) -> Free[ActorF, None]:
                return ref.free_stop()

            await system.run_free(workflow)
        """
        from everything_is_an_actor.core.actor_f import StopF

        return Suspend(StopF(self))  # type: ignore[return-value]

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
    """In-memory registry mapping correlation IDs to ComposableFuture promises.

    Uses ``ComposableFuture.promise()`` so that resolve/reject are
    **thread-safe** — works correctly even when the resolver (actor loop)
    and the waiter (ask caller) are on different event loops.
    """

    def __init__(self) -> None:
        # (future, resolve_fn, reject_fn) per correlation ID
        self._pending: dict[str, tuple[ComposableFuture[Any], Callable[[Any], None], Callable[[Exception], None]]] = {}

    def register(self, corr_id: str) -> ComposableFuture[Any]:
        """Create and register a promise for a correlation ID."""
        cf, resolve_fn, reject_fn = ComposableFuture.promise()
        self._pending[corr_id] = (cf, resolve_fn, reject_fn)
        return cf

    def resolve(self, corr_id: str, result: Any) -> None:
        """Complete a pending ask with a result.  Thread-safe."""
        entry = self._pending.pop(corr_id, None)
        if entry is not None:
            _, resolve_fn, _ = entry
            resolve_fn(result)

    def reject(self, corr_id: str, error: Exception) -> None:
        """Complete a pending ask with an error.  Thread-safe."""
        entry = self._pending.pop(corr_id, None)
        if entry is not None:
            _, _, reject_fn = entry
            reject_fn(error)

    def discard(self, corr_id: str) -> None:
        """Remove a pending entry (e.g. on timeout)."""
        self._pending.pop(corr_id, None)

    def reject_all(self, error: Exception) -> None:
        """Reject all pending asks (e.g. on system shutdown).  Thread-safe."""
        for _, _, reject_fn in self._pending.values():
            reject_fn(error)
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
