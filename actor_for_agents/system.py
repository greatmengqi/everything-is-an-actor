"""ActorSystem — top-level actor container and lifecycle manager."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from actor_for_agents.actor_f import ActorF
    from actor_for_agents.frees import Free

from actor_for_agents.actor import Actor, ActorContext, MsgT, RetT, StopMode, AfterMessage, AfterIdle
from actor_for_agents.mailbox import Empty, Mailbox, MemoryMailbox
from actor_for_agents.middleware import ActorMailboxContext, Middleware, NextFn, build_middleware_chain
from actor_for_agents.ref import (
    ActorRef,
    ActorStoppedError,
    MailboxFullError,
    ReplyChannel,
    _Envelope,
    _ReplyMessage,
    _ReplyRegistry,
    _Stop,
)
from actor_for_agents.supervision import Directive, SupervisorStrategy

logger = logging.getLogger(__name__)

# Timeout for middleware lifecycle hooks (on_started/on_stopped)
_MIDDLEWARE_HOOK_TIMEOUT = 10.0

# Maximum dead letters kept in memory
_MAX_DEAD_LETTERS = 10000

# Maximum consecutive failures before a root actor poison-quarantines a message
_MAX_CONSECUTIVE_FAILURES = 10


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
        executor_workers: int | None = 4,
        reply_channel: ReplyChannel | None = None,
        mailbox_cls: type[Mailbox] = MemoryMailbox,
        threaded: bool = False,
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
        self._threaded = threaded
        # Shared thread pool for actors to run blocking I/O
        from concurrent.futures import ThreadPoolExecutor

        self._executor = (
            ThreadPoolExecutor(max_workers=executor_workers, thread_name_prefix=f"actor-{name}")
            if executor_workers
            else None
        )

    async def spawn(
        self,
        actor_cls: type[Actor[MsgT, RetT]],
        name: str,
        *,
        mailbox_size: int = 256,
        mailbox: Mailbox | None = None,
        middlewares: list[Middleware] | None = None,
    ) -> ActorRef[MsgT, RetT]:
        """Spawn a root-level actor.

        Args:
            mailbox_cls: Mailbox class to use. Defaults to MemoryMailbox.
            mailbox_size: Size hint for the default mailbox.
            mailbox: Custom mailbox instance. If provided, overrides mailbox_cls.
        """
        if name in self._root_cells:
            raise ValueError(f"Root actor '{name}' already exists")
        cell = _ActorCell(
            actor_cls=actor_cls,
            name=name,
            parent=None,
            system=self,
            mailbox=mailbox or self._mailbox_cls(mailbox_size),
            middlewares=middlewares or [],
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

            from actor_for_agents.frees import Free
            from actor_for_agents.interpreter import run_free
            from actor_for_agents.actor_f import spawn, tell, ask

            async def workflow() -> Free[ActorF, str]:
                r = await spawn("greeter", GreeterActor)
                await tell(r, "hello")
                return await ask(r, "status")

            result = await system.run_free(workflow())
        """
        from actor_for_agents.interpreter import run_free as interpret

        return await interpret(self, free)

    async def shutdown(self, *, timeout: float = 10.0) -> None:
        """Gracefully stop all actors."""
        self._shutting_down = True
        tasks = []
        for cell in list(self._root_cells.values()):
            cell.request_stop()
            if cell.task is not None:
                tasks.append(cell.task)
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=timeout)
            # Cancel tasks that didn't finish within the timeout to prevent zombie tasks
            for t in pending:
                t.cancel()
            if pending:
                await asyncio.wait(pending, timeout=2.0)
        self._root_cells.clear()
        self._replies.reject_all(ActorStoppedError("ActorSystem shutting down"))
        await self._reply_channel.stop_listener()
        if self._executor is not None:
            self._executor.shutdown(wait=False)
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

    async def ask(self, path: str, message: Any, *, timeout: float = 30.0) -> Any:
        """Get actor by path and send ask message.

        Shorthand for::

            ref = await system.get_actor("/system/parent/child")
            return await ref.ask(message, timeout=timeout)

        Raises ValueError if actor not found.
        """
        ref = await self.get_actor(path)
        if ref is None:
            raise ValueError(f"Actor not found: {path}")
        return await ref.ask(message, timeout=timeout)


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
        self.task: asyncio.Task[None] | None = None
        self.stopped = False
        self._supervisor_strategy: SupervisorStrategy | None = None
        self._middlewares = middlewares or []
        self._receive_chain: NextFn | None = None
        self._last_message_time: float = time.time()  # For AfterIdle tracking
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

        # Check if actor implements sync receive() instead of async on_receive()
        import inspect
        # Check if receive is defined directly on this class (not inherited from Actor)
        has_sync_receive = (
            'receive' in self.actor_cls.__dict__ and
            callable(self.actor_cls.__dict__['receive']) and
            not inspect.iscoroutinefunction(self.actor_cls.__dict__['receive'])
        )

        if has_sync_receive:
            # Sync actor: use receive() method directly
            def _sync_handler(_ctx: ActorMailboxContext, message: Any) -> Any:
                return self.actor.receive(message)  # type: ignore[union-attr]

            async def _inner_handler(_ctx: ActorMailboxContext, message: Any) -> Any:
                # Sync handler runs in thread pool via _sync_wrapper
                loop = asyncio.get_running_loop()
                future = loop.run_in_executor(
                    self.system._executor,
                    _sync_handler,
                    _ctx,
                    message,
                )
                return await asyncio.wrap_future(future)

            self._receive_chain = _inner_handler
        else:
            # Async actor: use on_receive() method
            async def _inner_handler(_ctx: ActorMailboxContext, message: Any) -> Any:
                return await self.actor.on_receive(message)  # type: ignore[union-attr]

            if self._middlewares:
                self._receive_chain = build_middleware_chain(self._middlewares, _inner_handler)
            else:
                self._receive_chain = _inner_handler

        # Notify middleware of start (with timeout to prevent blocking)
        for mw in self._middlewares:
            try:
                await asyncio.wait_for(mw.on_started(self.ref), timeout=_MIDDLEWARE_HOOK_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning("Middleware %s.on_started timed out for %s", type(mw).__name__, self.path)
        await self.actor.on_started()
        self.task = asyncio.create_task(self._run(), name=f"actor:{self.path}")

    async def _process_message(self, msg: Any) -> None:
        """Process a single message. May be called in thread pool or event loop."""
        import time

        if isinstance(msg, _Stop):
            self.stopped = True
            return

        if not isinstance(msg, _Envelope):
            return

        msg_type = "ask" if msg.correlation_id else "tell"
        ctx = ActorMailboxContext(self.ref, msg.sender, msg_type)

        # In threaded mode, dispatch to thread pool; otherwise await directly
        if self.system._threaded and self.system._executor:
            # Run in thread pool - the executor runs a sync wrapper that awaits
            loop = asyncio.get_running_loop()
            # Use a sync wrapper since run_in_executor expects sync functions
            future = loop.run_in_executor(
                self.system._executor,
                self._sync_wrapper,
                ctx,
                msg.payload,
            )
            result = await asyncio.wrap_future(future)
        else:
            result = await self._receive_chain(ctx, msg.payload)

        if msg.correlation_id is not None:
            reply = _ReplyMessage(msg.correlation_id, result=result)
            await self.system._reply_channel.send_reply(
                msg.reply_to or self.system.system_id, reply, self.system._replies
            )

        self._last_message_time = time.time()

        # Check stop policy after message processed
        cached_policy = self.actor.stop_policy() if self.actor else None
        if cached_policy is not None:
            if isinstance(cached_policy, StopMode) and cached_policy == StopMode.ONE_TIME:
                self.stopped = True
            elif isinstance(cached_policy, AfterMessage) and msg.payload == cached_policy.message:
                self.stopped = True

    def _sync_wrapper(self, ctx: ActorMailboxContext, payload: Any) -> Any:
        """Sync wrapper for thread pool. Creates event loop to run async handler."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self._receive_chain(ctx, payload))
        finally:
            loop.close()

    async def enqueue(self, msg: _Envelope | _Stop) -> None:
        # Try non-blocking first (fast path for MemoryMailbox)
        if self.mailbox.put_nowait(msg):
            return
        # Fallback to async put (required for Redis and other async backends)
        if not await self.mailbox.put(msg):
            if isinstance(msg, _Envelope) and msg.correlation_id is not None:
                self.system._replies.reject(msg.correlation_id, MailboxFullError(f"Mailbox full: {self.path}"))
            elif isinstance(msg, _Envelope):
                self.system._dead_letter(self.ref, msg.payload, msg.sender)

    def request_stop(self) -> None:
        """Request graceful shutdown.

        Tries put_nowait first. If that fails (full or unsupported backend),
        cancels the task directly so _run exits via CancelledError → finally → _shutdown.
        """
        if not self.stopped:
            if not self.mailbox.put_nowait(_Stop()):
                # Redis/async backends can't put_nowait — cancel the task
                if self.task is not None and not self.task.done():
                    self.task.cancel()
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
        policy_is_static = True  # Track if policy changes at runtime

        try:
            while not self.stopped:
                # Calculate idle timeout from policy (only once per loop iteration)
                idle_timeout: float | None = None
                if cached_policy is None and self.actor is not None:
                    cached_policy = self.actor.stop_policy()
                    # Check if it's a static policy we can cache
                    policy_is_static = isinstance(cached_policy, StopMode) or isinstance(cached_policy, AfterMessage)

                if cached_policy is not None and isinstance(cached_policy, AfterIdle):
                    idle_timeout = cached_policy.seconds

                try:
                    if idle_timeout is not None:
                        msg = await asyncio.wait_for(self.mailbox.get(), timeout=idle_timeout)
                    else:
                        msg = await self.mailbox.get()
                except asyncio.TimeoutError:
                    break  # Idle timeout reached
                except asyncio.CancelledError:
                    break

                if isinstance(msg, _Stop):
                    break

                try:
                    if not isinstance(msg, _Envelope):
                        continue

                    if self.system._threaded and self.system._executor:
                        # Threaded mode: dispatch to thread pool
                        loop = asyncio.get_running_loop()
                        future = loop.run_in_executor(
                            self.system._executor,
                            self._sync_wrapper,
                            ActorMailboxContext(self.ref, msg.sender, "ask" if msg.correlation_id else "tell"),
                            msg.payload,
                        )
                        result = await asyncio.wrap_future(future)
                    else:
                        # Async mode: await directly
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
                        logger.error(
                            "Uncaught error in root actor %s (%d/%d): %s",
                            self.path,
                            consecutive_failures,
                            _MAX_CONSECUTIVE_FAILURES,
                            exc,
                        )
                        if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                            logger.error("Root actor %s hit consecutive failure limit — stopping", self.path)
                            break
        except asyncio.CancelledError:
            pass  # Fall through to _shutdown
        finally:
            await self._shutdown()

    async def _shutdown(self) -> None:
        self.stopped = True
        # Parallel child shutdown prevents cascading timeouts.
        child_tasks = []
        for child in list(self.children.values()):
            child.request_stop()
            if child.task is not None:
                child_tasks.append(child.task)
        if child_tasks:
            _, pending = await asyncio.wait(child_tasks, timeout=10.0)
            for t in pending:
                t.cancel()
                # Mark leaked children as stopped
                for child in self.children.values():
                    if child.task is t:
                        child.stopped = True
        # Drain mailbox → dead letters (use try/except to handle all backends)
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
        # Lifecycle hook
        for mw in self._middlewares:
            try:
                await asyncio.wait_for(mw.on_stopped(self.ref), timeout=_MIDDLEWARE_HOOK_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning("Middleware %s.on_stopped timed out for %s", type(mw).__name__, self.path)
            except Exception:
                logger.exception("Error in middleware on_stopped for %s", self.path)
        if self.actor is not None:
            try:
                await self.actor.on_stopped()
            except Exception:
                logger.exception("Error in on_stopped for %s", self.path)
        # Remove from parent
        if self.parent is not None:
            self.parent.children.pop(self.name, None)
        # Close mailbox to release backend resources (e.g. Redis connections)
        try:
            await self.mailbox.close()
        except Exception:
            logger.exception("Error closing mailbox for %s", self.path)
        # Break circular reference: _ActorCell → actor → context → _cell → _ActorCell
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
        # Stop the old actor (but keep the cell and mailbox)
        old_actor = child.actor
        if old_actor is not None:
            try:
                await old_actor.on_stopped()
            except Exception:
                logger.exception("Error in on_stopped during restart of %s", child.path)

        # Notify middleware of restart (reset per-instance state)
        for mw in child._middlewares:
            try:
                await asyncio.wait_for(mw.on_restart(child.ref, error), timeout=_MIDDLEWARE_HOOK_TIMEOUT)
            except asyncio.TimeoutError:
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
