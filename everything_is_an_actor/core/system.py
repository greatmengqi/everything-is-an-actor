"""ActorSystem — top-level actor container and lifecycle manager."""

from __future__ import annotations

import asyncio
import logging
from collections import deque

import anyio
from dataclasses import dataclass
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from everything_is_an_actor.core.actor_f import ActorF
    from everything_is_an_actor.core.frees import Free

from everything_is_an_actor.core.actor import Actor, ActorContext, MsgT, RetT, StopMode, AfterMessage, AfterIdle
from everything_is_an_actor.core.composable_future import ComposableFuture
from everything_is_an_actor.core.dispatcher import Dispatcher
from everything_is_an_actor.core.mailbox import Empty, Mailbox, MemoryMailbox
from everything_is_an_actor.core.middleware import ActorMailboxContext, Middleware, NextFn, build_middleware_chain
from everything_is_an_actor.core.ref import (
    ActorRef,
    ActorStoppedError,
    MailboxFullError,
    ReplyChannel,
    StreamAdapter,
    _Envelope,
    _ReplyMessage,
    _ReplyRegistry,
    _Stop,
)
from everything_is_an_actor.core.supervision import Directive, SupervisorStrategy

logger = logging.getLogger(__name__)

_MIDDLEWARE_HOOK_TIMEOUT = 10.0
_MAX_DEAD_LETTERS = 10000
_DEFAULT_MAX_CONSECUTIVE_FAILURES = 10


def _timed(coro: Any) -> ComposableFuture:
    """Wrap a lifecycle hook coroutine with the standard middleware timeout."""
    return ComposableFuture(coro).with_timeout(_MIDDLEWARE_HOOK_TIMEOUT)


@dataclass
class DeadLetter:
    """A message that could not be delivered."""

    recipient: ActorRef
    message: Any
    sender: ActorRef | None


class ActorSystem:
    """Top-level actor container.

    Manages root actors and provides the dead letter sink.
    """

    def __init__(
        self,
        name: str = "system",
        *,
        max_dead_letters: int = _MAX_DEAD_LETTERS,
        max_consecutive_failures: int = _DEFAULT_MAX_CONSECUTIVE_FAILURES,
        reply_channel: ReplyChannel | None = None,
        mailbox_cls: type[Mailbox] = MemoryMailbox,
        dispatchers: dict[str, Dispatcher] | None = None,
        stream_adapter: StreamAdapter | None = None,
    ) -> None:
        import uuid as _uuid

        self.name = name
        self.system_id = f"{name}-{_uuid.uuid4().hex[:8]}"
        self._root_cells: dict[str, _ActorCell] = {}
        self._dead_letters: deque[DeadLetter] = deque(maxlen=max_dead_letters)
        self._on_dead_letter: list[Any] = []
        self._shutting_down = False
        self._replies = _ReplyRegistry()
        self._reply_channel = reply_channel or ReplyChannel()
        self._mailbox_cls = mailbox_cls
        self._dispatchers: dict[str, Dispatcher] = dispatchers or {}
        self._dispatchers_started: set[str] = set()
        # Streaming is a higher-layer concern. core/ does not know what a
        # Task or StreamEvent is — an adapter is installed by agents/.
        self._stream_adapter: StreamAdapter | None = stream_adapter
        # Root actor circuit breaker — if a root actor raises this many
        # consecutive times without being wrapped by a supervisor, stop it.
        # Non-root actors follow their parent's ``SupervisorStrategy``.
        self._max_consecutive_failures = max_consecutive_failures

    async def spawn(
        self,
        actor_cls: type[Actor[MsgT, RetT]],
        name: str,
        *,
        mailbox_size: int = 256,
        mailbox: Mailbox | None = None,
        middlewares: list[Middleware] | None = None,
        dispatcher: str | None = None,
    ) -> ActorRef[MsgT, RetT]:
        """Spawn a root-level actor.

        Args:
            mailbox_size: Size hint for the default mailbox.
            mailbox: Custom mailbox instance. If provided, overrides mailbox_cls.
            dispatcher: Named dispatcher key. When set, handler execution
                is offloaded via ``Dispatcher.dispatch()``.
        """
        actor_cls.__validate_spawn_class__(mode="single")

        if self._shutting_down:
            raise RuntimeError("Cannot spawn actor: system is shutting down")
        if name in self._root_cells:
            raise ValueError(f"Root actor '{name}' already exists")

        resolved: Dispatcher | None = None
        if dispatcher is not None:
            if dispatcher not in self._dispatchers:
                raise ValueError(f"Unknown dispatcher '{dispatcher}'. Available: {list(self._dispatchers.keys())}")
            resolved = self._dispatchers[dispatcher]
            if dispatcher not in self._dispatchers_started:
                await resolved.start()
                self._dispatchers_started.add(dispatcher)

        cell = _ActorCell(
            actor_cls=actor_cls,
            name=name,
            parent=None,
            system=self,
            mailbox=mailbox or self._mailbox_cls(mailbox_size),
            middlewares=middlewares or [],
            dispatcher=resolved,
        )
        self._root_cells[name] = cell
        try:
            await cell.start()
        except Exception:
            del self._root_cells[name]
            raise
        return cell.ref

    async def run_free(self, free: "Free[ActorF, Any]") -> "Any":
        """Execute a Free[A] workflow against this ActorSystem.

        The Free program is built using actor operations (spawn, tell, ask, stop)
        and is interpreted against this live system.

        Example::

            from everything_is_an_actor.core.frees import Free
            from everything_is_an_actor.core.interpreter import run_free
            from everything_is_an_actor.core.actor_f import spawn, tell, ask

            async def workflow() -> Free[ActorF, str]:
                r = await spawn("greeter", GreeterActor)
                await tell(r, "hello")
                return await ask(r, "status")

            result = await system.run_free(workflow())
        """
        from everything_is_an_actor.core.interpreter import run_free as interpret

        return await interpret(self, free)

    async def shutdown(self, *, timeout: float = 10.0) -> None:
        """Gracefully stop all actors."""
        self._shutting_down = True
        cells = list(self._root_cells.values())
        futures = []
        for cell in cells:
            cell.request_stop()
            if cell._run_future is not None:
                futures.append(cell._run_future)
        if futures:
            try:
                with anyio.fail_after(timeout):
                    await ComposableFuture.join_all(*futures)
            except TimeoutError:
                for cell in cells:
                    if not cell.stopped and cell._cancel_run is not None:
                        cell._cancel_run()
                remaining = [c._run_future for c in cells if c._run_future and not c.stopped]
                if remaining:
                    try:
                        with anyio.fail_after(2.0):
                            await ComposableFuture.join_all(*remaining)
                    except TimeoutError:
                        pass
        self._root_cells.clear()
        self._replies.reject_all(ActorStoppedError("ActorSystem shutting down"))
        await self._reply_channel.stop_listener()
        for disp_name in list(self._dispatchers_started):
            try:
                await self._dispatchers[disp_name].shutdown()
            except Exception:
                logger.exception("Error shutting down dispatcher '%s'", disp_name)
        self._dispatchers_started.clear()
        self._shutting_down = False
        logger.info("ActorSystem '%s' shut down (%d dead letters)", self.name, len(self._dead_letters))

    def _dead_letter(self, recipient: ActorRef, message: Any, sender: ActorRef | None) -> None:
        dl = DeadLetter(recipient=recipient, message=message, sender=sender)
        self._dead_letters.append(dl)
        for cb in self._on_dead_letter:
            try:
                cb(dl)
            except Exception:
                pass
        logger.debug("Dead letter: %s → %s", type(message).__name__, recipient.path)

    def on_dead_letter(self, callback: Any) -> None:
        """Register a dead letter listener."""
        self._on_dead_letter.append(callback)

    @property
    def dead_letters(self) -> list[DeadLetter]:
        return list(self._dead_letters)

    async def get_actor(self, path: str) -> ActorRef | None:
        """Get actor ref by path.

        Path format: /system-name/actor-name/.../actor-name
        Example: /system/parent/child/echoagent-a1b2c3d4

        Returns None if actor is not found.
        """
        # Normalize path
        if not path.startswith("/"):
            path = "/" + path
        parts = [p for p in path.split("/") if p]
        if not parts:
            return None

        # First part is system name
        if parts[0] != self.name:
            return None
        parts = parts[1:]

        # Traverse from root cells
        current: dict[str, _ActorCell] = self._root_cells
        for i, part in enumerate(parts):
            cell = current.get(part)
            if cell is None:
                return None
            if i == len(parts) - 1:
                return cell.ref
            current = cell.children
        return None

    async def ask(self, target: ActorRef | str, message: Any, *, timeout: float = 30.0) -> Any:
        """Send a message and wait for reply.

        ``target`` can be an ``ActorRef`` or a path string::

            result = await system.ask(ref, "hello")
            result = await system.ask("/system/worker", "hello")
        """
        ref = target if isinstance(target, ActorRef) else await self.get_actor(target)
        if ref is None:
            raise ValueError(f"Actor not found: {target}")
        return await ref._ask(message, timeout=timeout)

    async def tell(self, target: ActorRef | str, message: Any) -> None:
        """Fire-and-forget message delivery.

        ``target`` can be an ``ActorRef`` or a path string::

            await system.tell(ref, "hello")
            await system.tell("/system/worker", "hello")
        """
        ref = target if isinstance(target, ActorRef) else await self.get_actor(target)
        if ref is None:
            raise ValueError(f"Actor not found: {target}")
        import asyncio as _aio

        result = ref._tell(message)
        if _aio.iscoroutine(result):
            await result

    async def ask_stream(self, target: ActorRef | str, message: Any, *, timeout: float = 30.0):  # type: ignore[return]
        """Stream events from an AgentActor.

        ``target`` can be an ``ActorRef`` or a path string::

            async for item in system.ask_stream(ref, Task(input="x")):
                match item:
                    case StreamEvent(event=e): ...
                    case StreamResult(result=r): ...
        """
        ref = target if isinstance(target, ActorRef) else await self.get_actor(target)
        if ref is None:
            raise ValueError(f"Actor not found: {target}")
        async for item in ref._ask_stream(message, timeout=timeout):
            yield item


# ---------------------------------------------------------------------------
# _ActorCell — internal runtime wrapper
# ---------------------------------------------------------------------------


class _ActorCell:
    """Runtime container for a single actor instance.

    Manages the mailbox, processing loop, children, and supervision.
    Not part of the public API.
    """

    def __init__(
        self,
        actor_cls: type[Actor],
        name: str,
        parent: _ActorCell | None,
        system: ActorSystem,
        mailbox: Mailbox,
        middlewares: list[Middleware] | None = None,
        dispatcher: Dispatcher | None = None,
    ) -> None:
        import time

        self.actor_cls = actor_cls
        self.name = name
        self.parent = parent
        self.system = system
        self.children: dict[str, _ActorCell] = {}
        self.mailbox = mailbox
        self.ref = ActorRef(self)
        self.actor: Actor | None = None
        self._run_future: ComposableFuture | None = None
        self._cancel_run: Callable[[], None] | None = None
        self.stopped = False
        self._supervisor_strategy: SupervisorStrategy | None = None
        self._middlewares = middlewares or []
        self._receive_chain: NextFn | None = None
        self._last_message_time: float = time.time()
        self._dispatcher = dispatcher
        # Cache path (immutable after init — parent never changes)
        parts: list[str] = []
        cell: _ActorCell | None = self
        while cell is not None:
            parts.append(cell.name)
            cell = cell.parent
        parts.append(system.name)
        self.path = "/" + "/".join(reversed(parts))

    async def start(self) -> None:
        self.actor = self.actor_cls()
        self.actor.context = ActorContext(self)

        from everything_is_an_actor.core.composable_future import ComposableFuture
        from everything_is_an_actor.core.validation import find_sync_handler

        has_sync_receive = find_sync_handler(self.actor_cls, "receive") is not None

        # Reject async handler + dispatcher — conceptually incoherent.
        # Dispatchers exist to offload blocking work to a thread pool;
        # async handlers don't block. Bridging one back via ComposableFuture.result()
        # spawns a new event loop per message and breaks any cross-loop
        # object (ContextVars, registered Futures) that on_receive touches.
        # Use sync receive() if the handler is blocking, or omit dispatcher
        # for async handlers.
        if not has_sync_receive and self._dispatcher is not None:
            raise ValueError(
                f"Actor '{self.actor_cls.__name__}' has async on_receive() but was spawned with "
                f"dispatcher — dispatchers only offload blocking sync work. Either implement "
                f"sync receive() for blocking logic, or spawn without dispatcher for async logic."
            )

        if has_sync_receive and self._dispatcher:
            # Sync actor + dispatcher: offload receive() via dispatcher.dispatch()
            async def _inner_handler(_ctx: ActorMailboxContext, message: Any) -> Any:
                return await self._dispatcher.dispatch(  # type: ignore[union-attr]
                    self.actor.receive,
                    message,  # type: ignore[union-attr]
                )
        elif has_sync_receive:
            # Sync actor, no dispatcher: run receive() in default executor
            async def _inner_handler(_ctx: ActorMailboxContext, message: Any) -> Any:
                return await ComposableFuture.from_blocking(
                    self.actor.receive,
                    message,  # type: ignore[union-attr]
                )
        else:
            # Async actor, no dispatcher: direct execution on caller loop
            async def _inner_handler(_ctx: ActorMailboxContext, message: Any) -> Any:
                return await self.actor.on_receive(message)  # type: ignore[union-attr]

        if self._middlewares:
            self._receive_chain = build_middleware_chain(self._middlewares, _inner_handler)
        else:
            self._receive_chain = _inner_handler

        # LIFO unwind on partial failure: if middleware[k].on_started or
        # actor.on_started raises, any middleware[0..k-1] whose on_started
        # succeeded must see on_stopped to release what it acquired.
        # Timeout is now treated as initialization failure (was: silently
        # downgraded to a warning, which left middleware in a half-started
        # state while message delivery continued).
        started_mws: list[Middleware] = []
        try:
            for mw in self._middlewares:
                await _timed(mw.on_started(self.ref))
                started_mws.append(mw)
            await self.actor.on_started()
        except BaseException:
            for mw in reversed(started_mws):
                try:
                    await _timed(mw.on_stopped(self.ref))
                except Exception:
                    logger.exception(
                        "Middleware %s.on_stopped failed during start-unwind for %s",
                        type(mw).__name__,
                        self.path,
                    )
            raise

        self._run_future, self._cancel_run = ComposableFuture.eager(
            self._run(),
            name=f"actor:{self.path}",
        )

    async def enqueue(self, msg: _Envelope | _Stop) -> None:
        # Try non-blocking first (fast path for MemoryMailbox)
        if self.mailbox.put_nowait(msg):
            return
        # ask path: never block on enqueue — reject immediately to prevent
        # deadlocks when two actors ask each other with full mailboxes.
        if isinstance(msg, _Envelope) and msg.correlation_id is not None:
            self.system._replies.reject(msg.correlation_id, MailboxFullError(f"Mailbox full: {self.path}"))
            return
        # tell / _Stop path: fall back to async put (may block for BLOCK policy,
        # required for Redis and other async backends)
        if not await self.mailbox.put(msg):
            if isinstance(msg, _Envelope):
                self.system._dead_letter(self.ref, msg.payload, msg.sender)

    def request_stop(self) -> None:
        """Request graceful shutdown.

        Tries put_nowait first. If that fails (full or unsupported backend),
        cancels the task directly so _run exits via CancelledError → finally → _shutdown.
        """
        if not self.stopped:
            if not self.mailbox.put_nowait(_Stop()):
                if self._cancel_run is not None:
                    self._cancel_run()
                else:
                    self.stopped = True

    async def spawn_child(
        self,
        actor_cls: type[Actor[MsgT, RetT]],
        name: str,
        *,
        mailbox_size: int = 256,
        mailbox: Mailbox | None = None,
        middlewares: list[Middleware] | None = None,
    ) -> ActorRef[MsgT, RetT]:
        # Validate at spawn-time via the class-level hook (no core → agents coupling)
        actor_cls.__validate_spawn_class__(mode="single")

        if name in self.children:
            raise ValueError(f"Child '{name}' already exists under {self.path}")
        child = _ActorCell(
            actor_cls=actor_cls,
            name=name,
            parent=self,
            system=self.system,
            mailbox=mailbox or self.system._mailbox_cls(mailbox_size),
            middlewares=middlewares or [],
        )
        self.children[name] = child
        try:
            await child.start()
        except Exception:
            del self.children[name]
            raise
        return child.ref

    # -- Processing loop -------------------------------------------------------

    async def _run(self) -> None:
        import time

        consecutive_failures = 0
        # Cache stop_policy to avoid repeated method calls per message
        cached_policy = None

        try:
            while not self.stopped:
                # Calculate idle timeout from policy (only once per loop iteration)
                idle_timeout: float | None = None
                if cached_policy is None and self.actor is not None:
                    cached_policy = self.actor.stop_policy()

                if cached_policy is not None and isinstance(cached_policy, AfterIdle):
                    idle_timeout = cached_policy.seconds

                try:
                    if idle_timeout is not None:
                        with anyio.fail_after(idle_timeout):
                            msg = await self.mailbox.get()
                    else:
                        msg = await self.mailbox.get()
                except TimeoutError:
                    break  # Idle timeout reached
                except asyncio.CancelledError:
                    break

                if isinstance(msg, _Stop):
                    break

                try:
                    if not isinstance(msg, _Envelope):
                        # Unknown payload on the mailbox — protocol invariant violated.
                        # Let supervision handle it rather than silently dropping.
                        raise TypeError(
                            f"Mailbox protocol violation for {self.path}: "
                            f"expected _Envelope or _Stop, got {type(msg).__name__}"
                        )

                    ctx = ActorMailboxContext(self.ref, msg.sender, "ask" if msg.correlation_id else "tell")
                    result = await self._receive_chain(ctx, msg.payload)  # type: ignore[misc]

                    if msg.correlation_id is not None:
                        reply = _ReplyMessage(msg.correlation_id, result=result)
                        await self.system._reply_channel.send_reply(
                            msg.reply_to or self.system.system_id, reply, self.system._replies
                        )
                    consecutive_failures = 0
                    # Update last message time for AfterIdle tracking
                    self._last_message_time = time.time()
                    # Check stop policy
                    if cached_policy is not None:
                        if isinstance(cached_policy, StopMode):
                            if cached_policy == StopMode.ONE_TIME:
                                break
                        elif isinstance(cached_policy, AfterMessage):
                            if msg.payload == cached_policy.message:
                                break
                        elif isinstance(cached_policy, AfterIdle):
                            # Reset idle timeout after each message
                            idle_timeout = cached_policy.seconds
                except Exception as exc:
                    if isinstance(msg, _Envelope) and msg.correlation_id is not None:
                        reply = _ReplyMessage(msg.correlation_id, error=str(exc), exception=exc)
                        await self.system._reply_channel.send_reply(
                            msg.reply_to or self.system.system_id, reply, self.system._replies
                        )
                    if self.parent is not None:
                        await self.parent._handle_child_failure(self, exc)
                    else:
                        consecutive_failures += 1
                        limit = self.system._max_consecutive_failures
                        logger.error(
                            "Uncaught error in root actor %s (%d/%d): %s",
                            self.path,
                            consecutive_failures,
                            limit,
                            exc,
                        )
                        if consecutive_failures >= limit:
                            logger.error("Root actor %s hit consecutive failure limit — stopping", self.path)
                            break
        except asyncio.CancelledError:
            pass  # Fall through to _shutdown
        finally:
            await self._shutdown()

    async def _shutdown(self) -> None:
        # Shield the entire shutdown sequence from cancellation.
        # on_started/on_stopped are lifecycle guarantees — they must complete.
        try:
            await ComposableFuture(self._shutdown_inner()).shield()
        except asyncio.CancelledError:
            pass

    async def _shutdown_inner(self) -> None:
        self.stopped = True
        # Parallel child shutdown
        children = list(self.children.values())
        child_futures = []
        for child in children:
            child.request_stop()
            if child._run_future is not None:
                child_futures.append(child._run_future)
        if child_futures:
            try:
                with anyio.fail_after(10.0):
                    await ComposableFuture.join_all(*child_futures)
            except TimeoutError:
                for child in children:
                    if not child.stopped and child._cancel_run is not None:
                        child._cancel_run()
                        child.stopped = True
        # Drain mailbox → dead letters
        while True:
            try:
                msg = self.mailbox.get_nowait()
            except Empty:
                break
            if isinstance(msg, _Envelope):
                if msg.correlation_id is not None:
                    self.system._replies.reject(msg.correlation_id, ActorStoppedError(f"Actor {self.path} stopped"))
                else:
                    self.system._dead_letter(self.ref, msg.payload, msg.sender)
        # Lifecycle hooks
        for mw in self._middlewares:
            try:
                await _timed(mw.on_stopped(self.ref))
            except TimeoutError:
                logger.warning("Middleware %s.on_stopped timed out for %s", type(mw).__name__, self.path)
            except Exception:
                logger.exception("Error in middleware on_stopped for %s", self.path)
        if self.actor is not None:
            try:
                await _timed(self.actor.on_stopped())
            except TimeoutError:
                logger.error("on_stopped timed out for %s", self.path)
            except asyncio.CancelledError:
                logger.warning("on_stopped cancelled for %s, retrying once", self.path)
                try:
                    await _timed(self.actor.on_stopped())
                except Exception:
                    logger.exception("on_stopped retry failed for %s", self.path)
            except Exception:
                logger.exception("Error in on_stopped for %s", self.path)
        if self.parent is not None:
            self.parent.children.pop(self.name, None)
        try:
            await self.mailbox.close()
        except Exception:
            logger.exception("Error closing mailbox for %s", self.path)
        self.actor = None

    # -- Supervision -----------------------------------------------------------

    def _get_supervisor_strategy(self) -> SupervisorStrategy:
        if self._supervisor_strategy is None:
            self._supervisor_strategy = self.actor.supervisor_strategy()  # type: ignore[union-attr]
        return self._supervisor_strategy

    async def _handle_child_failure(self, child: _ActorCell, error: Exception) -> None:
        strategy = self._get_supervisor_strategy()
        directive = strategy.decide(error)

        affected = strategy.apply_to_children(child.name, list(self.children.keys()))

        if directive == Directive.resume:
            logger.info("Supervisor %s: resume %s after %s", self.path, child.path, type(error).__name__)
            return

        if directive == Directive.stop:
            for name in affected:
                c = self.children.get(name)
                if c is not None:
                    c.request_stop()
            logger.info(
                "Supervisor %s: stop %s after %s",
                self.path,
                [self.children[n].path for n in affected if n in self.children],
                type(error).__name__,
            )
            return

        if directive == Directive.escalate:
            # Stop the failing child, then propagate failure up the supervision chain.
            # We cannot use `raise error` here — that would crash the child's _run
            # loop instead of notifying the grandparent's supervisor.
            child.request_stop()
            if self.parent is not None:
                logger.info(
                    "Supervisor %s: escalate %s to grandparent %s", self.path, type(error).__name__, self.parent.path
                )
                await self.parent._handle_child_failure(self, error)
            else:
                logger.error("Uncaught escalation at root actor %s: %s", self.path, error)
            return

        if directive == Directive.restart:
            for name in affected:
                c = self.children.get(name)
                if c is None:
                    continue
                if not strategy.record_restart(name):
                    logger.warning("Supervisor %s: child %s exceeded restart limit — stopping", self.path, c.path)
                    c.request_stop()
                    continue
                await self._restart_child(c, error)

    async def _restart_child(self, child: _ActorCell, error: Exception) -> None:
        logger.info("Supervisor %s: restarting %s after %s", self.path, child.path, type(error).__name__)
        # Discard the old actor (but keep the cell and mailbox). Restart is
        # NOT graceful shutdown — it is crash recovery. We call
        # ``on_pre_restart`` (default no-op) on the old instance so business
        # code that genuinely needs to release something across restart
        # boundaries has an explicit hook, and we do NOT invoke
        # ``on_stopped`` (which is reserved for actual shutdown).
        old_actor = child.actor
        if old_actor is not None:
            try:
                await old_actor.on_pre_restart(error)
            except Exception:
                logger.exception("Error in on_pre_restart during restart of %s", child.path)

        # Notify middleware of restart (reset per-instance state)
        for mw in child._middlewares:
            try:
                await _timed(mw.on_restart(child.ref, error))
            except TimeoutError:
                logger.warning("Middleware %s.on_restart timed out for %s", type(mw).__name__, child.path)
            except Exception:
                logger.exception("Error in middleware on_restart for %s", child.path)
        # Create fresh instance
        new_actor = child.actor_cls()
        new_actor.context = ActorContext(child)
        child.actor = new_actor
        try:
            await new_actor.on_restart(error)
            await new_actor.on_started()
        except Exception:
            logger.exception("Error during restart initialization of %s", child.path)
            child.request_stop()
