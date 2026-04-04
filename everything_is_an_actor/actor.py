"""Actor base class and per-actor context."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Generic, TypeVar, Union

from everything_is_an_actor.supervision import OneForOneStrategy, SupervisorStrategy

DEFAULT_MAILBOX_SIZE = 256

if TYPE_CHECKING:
    from everything_is_an_actor.ref import ActorRef

# Message and return type variables — use Actor[MyMsg, MyReturn] for typed actors
MsgT = TypeVar("MsgT")
RetT = TypeVar("RetT")


# ---------------------------------------------------------------------------
# Stop Policy ADT — defines when an actor stops itself automatically
# ---------------------------------------------------------------------------


class StopMode(Enum):
    """Built-in stop modes for actors."""

    NEVER = auto()  # Never auto-stop (default)
    ONE_TIME = auto()  # Stop after processing one message


@dataclass
class AfterMessage:
    """Stop after receiving a specific message."""

    message: Any  # The message that triggers stop


@dataclass
class AfterIdle:
    """Stop after being idle (no messages processed) for N seconds."""

    seconds: float  # Idle timeout in seconds


# Union type for all stop policies
StopPolicy = Union[StopMode, AfterMessage, AfterIdle]


class ActorContext:
    """Per-actor runtime context, injected before ``on_started``.

    Provides access to the actor's identity, parent, children,
    and the ability to spawn child actors.
    """

    __slots__ = ("_cell",)

    def __init__(self, cell: Any) -> None:
        self._cell = cell

    @property
    def self_ref(self) -> ActorRef:
        return self._cell.ref

    @property
    def parent(self) -> ActorRef | None:
        p = self._cell.parent
        return p.ref if p is not None else None

    @property
    def children(self) -> dict[str, ActorRef]:
        return {name: c.ref for name, c in self._cell.children.items()}

    @property
    def system(self) -> Any:
        return self._cell.system

    async def spawn(
        self,
        actor_cls: type[Actor[MsgT, RetT]],
        name: str,
        *,
        mailbox_size: int = 256,
        middlewares: list | None = None,
    ) -> ActorRef[MsgT, RetT]:
        """Spawn a child actor supervised by this actor."""
        return await self._cell.spawn_child(actor_cls, name, mailbox_size=mailbox_size, middlewares=middlewares)

    async def stop_self(self) -> None:
        """Stop this actor (self) and remove from parent's children.

        Equivalent to Akka's context.stop(self).

        Use when an actor decides its own work is done and should shut down::

            async def on_receive(self, message):
                if message == "done":
                    await self.context.stop_self()
        """
        self._cell.request_stop()

    def ask(
        self,
        target: type[Actor[MsgT, RetT]] | ActorRef[MsgT, RetT],
        message: MsgT,
        *,
        timeout: float = 300.0,
        name: str | None = None,
    ) -> ComposableFuture[RetT]:
        """Spawn an ephemeral child actor (or reuse an existing ref), send one message, return result.

        Returns a ``ComposableFuture`` for chaining::

            result = await ctx.ask(EchoActor, Task(input="hello")).map(f).recover(g)

        ``target`` can be:

        - An ``Actor`` subclass — a new child actor is spawned, used once, then stopped.
        - An ``ActorRef`` — the message is sent to the already-running actor.

        Args:
            name: Optional deterministic name for the ephemeral child actor.
                  Defaults to ``{classname}-{uuid8}``. Provide a stable name
                  to improve log correlation across retries.
        """
        from everything_is_an_actor.composable_future import ComposableFuture
        import uuid

        if isinstance(target, type):
            async def _ephemeral_ask() -> RetT:
                import asyncio
                actor_name = name or f"{target.__name__.lower()}-{uuid.uuid4().hex[:8]}"
                ref = await self.spawn(target, actor_name)
                try:
                    return await ref._ask(message, timeout=timeout)
                finally:
                    ref.stop()
                    current = asyncio.current_task()
                    if current is not None and getattr(current, "cancelling", lambda: 0)() > 0:
                        ref.interrupt()
                    await ref.join()
            return ComposableFuture(_ephemeral_ask())
        else:
            return target._ask(message, timeout=timeout)

    def sequence(
        self,
        tasks: list[tuple[type[Actor[Any, Any]] | ActorRef[Any, Any], Any]],
        *,
        timeout: float = 300.0,
    ) -> ComposableFuture[list[Any]]:
        """Run tasks concurrently, collect results in order. Returns ComposableFuture.

        On first failure, remaining siblings are cancelled; ephemeral children
        are cleaned up via ``ask()``'s finally block.
        """
        from everything_is_an_actor.composable_future import ComposableFuture

        async def _seq() -> list[Any]:
            import asyncio
            if not tasks:
                return []
            async def _run(t: Any, msg: Any) -> Any:
                return await self.ask(t, msg, timeout=timeout)
            spawned = [asyncio.create_task(_run(t, msg)) for t, msg in tasks]
            _, pending = await asyncio.wait(spawned, return_when=asyncio.FIRST_EXCEPTION)
            if pending:
                for t in pending:
                    t.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
            for t in spawned:
                if not t.cancelled():
                    exc = t.exception()
                    if exc is not None:
                        raise exc
            return [t.result() for t in spawned]
        return ComposableFuture(_seq())

    def traverse(
        self,
        inputs: list[Any],
        target: type[Actor[Any, Any]] | ActorRef[Any, Any],
        *,
        timeout: float = 300.0,
    ) -> ComposableFuture[list[Any]]:
        """Map inputs through the same agent concurrently. Returns ComposableFuture."""
        from everything_is_an_actor.agents.task import Task

        return self.sequence(
            [(target, Task(input=inp)) for inp in inputs],
            timeout=timeout,
        )

    def race(
        self,
        tasks: list[tuple[type[Actor[Any, Any]] | ActorRef[Any, Any], Any]],
        *,
        timeout: float = 300.0,
    ) -> ComposableFuture[Any]:
        """Return first completed result; cancel the rest. Returns ComposableFuture."""
        from everything_is_an_actor.composable_future import ComposableFuture

        async def _race() -> Any:
            import asyncio
            if not tasks:
                raise ValueError("race() requires at least one task")
            async def _run(t: Any, msg: Any) -> Any:
                return await self.ask(t, msg, timeout=timeout)
            spawned = [asyncio.create_task(_run(t, msg)) for t, msg in tasks]
            try:
                _, pending = await asyncio.wait(spawned, return_when=asyncio.FIRST_COMPLETED)
                for t in pending:
                    t.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                first = next(t for t in spawned if t not in pending)
                return first.result()
            except BaseException:
                for t in spawned:
                    if not t.done():
                        t.cancel()
                await asyncio.gather(*spawned, return_exceptions=True)
                raise
        return ComposableFuture(_race())

    def zip(
        self,
        task_a: tuple[type[Actor[Any, Any]] | ActorRef[Any, Any], Any],
        task_b: tuple[type[Actor[Any, Any]] | ActorRef[Any, Any], Any],
        *,
        timeout: float = 300.0,
    ) -> ComposableFuture[tuple[Any, Any]]:
        """Run two tasks concurrently, return pair. Returns ComposableFuture."""
        from everything_is_an_actor.composable_future import Fn
        return self.sequence([task_a, task_b], timeout=timeout).map(
            Fn[list[Any], tuple[Any, Any]](lambda r: (r[0], r[1]))
        )

    async def stream(
        self,
        target: type[Actor[MsgT, RetT]] | ActorRef[MsgT, RetT],
        message: MsgT,
        *,
        timeout: float = 300.0,
        name: str | None = None,
    ):  # type: ignore[return]
        """Spawn an ephemeral child actor (or reuse a ref) and stream its events.

        Yields ``StreamItem`` objects — ``StreamEvent`` for each event,
        ``StreamResult`` as the final item. Ephemeral actors are stopped after
        the stream is exhausted or the caller breaks early.

        Use inside a streaming ``execute()`` to propagate child chunks::

            async def execute(self, input: str):
                async for item in self.context.stream(LLMAgent, Task(input=input)):
                    match item:
                        case StreamEvent(event=e) if e.type == "task_chunk":
                            yield e.data
                        case StreamResult(result=r):
                            pass
        """
        import uuid

        if isinstance(target, type):
            import asyncio

            actor_name = name or f"{target.__name__.lower()}-{uuid.uuid4().hex[:8]}"
            ref = await self.spawn(target, actor_name)
            try:
                async for item in ref._ask_stream(message, timeout=timeout):
                    yield item
            finally:
                ref.stop()
                current = asyncio.current_task()
                if current is not None and getattr(current, "cancelling", lambda: 0)() > 0:
                    ref.interrupt()
                await ref.join()
        else:
            async for item in target._ask_stream(message, timeout=timeout):
                yield item

    # ------------------------------------------------------------------
    # Backward-compatible aliases (deprecated — use ask/sequence/stream)
    # ------------------------------------------------------------------

    async def dispatch(
        self,
        target: type[Actor[MsgT, RetT]] | ActorRef[MsgT, RetT],
        message: MsgT,
        *,
        timeout: float = 300.0,
        name: str | None = None,
    ) -> RetT:
        """Deprecated: use ``ask()`` instead."""
        return await self.ask(target, message, timeout=timeout, name=name)

    async def dispatch_parallel(
        self,
        tasks: list[tuple[type[Actor[Any, Any]] | ActorRef[Any, Any], Any]],
        *,
        timeout: float = 300.0,
    ) -> list[Any]:
        """Deprecated: use ``sequence()`` instead."""
        return await self.sequence(tasks, timeout=timeout)

    async def dispatch_stream(  # type: ignore[return]
        self,
        target: type[Actor[MsgT, RetT]] | ActorRef[MsgT, RetT],
        message: MsgT,
        *,
        timeout: float = 300.0,
        name: str | None = None,
    ):
        """Deprecated: use ``stream()`` instead."""
        async for item in self.stream(target, message, timeout=timeout, name=name):
            yield item

    async def run_in_executor(self, fn: Callable[..., Any], *args: Any) -> Any:
        """Run a blocking function in the system's thread pool.

        Usage::

            result = await self.context.run_in_executor(requests.get, url)
        """
        import asyncio

        executor = self._cell.system._executor
        return await asyncio.get_running_loop().run_in_executor(executor, fn, *args)


class Actor(Generic[MsgT, RetT]):
    """Base class for all actors.

    Type parameters ``MsgT`` and ``RetT`` constrain message and return types::

        class Greeter(Actor[str, str]):
            async def on_receive(self, message: str) -> str:
                return f"Hello, {message}!"

        class Calculator(Actor[int, int]):
            async def on_receive(self, message: int) -> int:
                ...

    Unparameterized ``Actor`` accepts and returns ``Any`` (backward-compatible).

    For synchronous (blocking) actors, implement ``receive()`` instead::

        class BlockingActor(Actor[str, str]):
            def receive(self, message: str) -> str:
                # Runs in thread pool, won't block other actors
                result = blocking_io()
                return result
    """

    context: ActorContext

    async def on_receive(self, message: MsgT) -> RetT:
        """Handle an incoming message (async version).

        Return value is sent back as reply for ``ask`` calls.
        For ``tell`` calls, the return value is discarded.
        """
        raise NotImplementedError(f"{type(self).__name__} must implement on_receive()")

    def receive(self, message: MsgT) -> RetT:
        """Handle an incoming message (sync version).

        Runs in thread pool automatically when used with ThreadedMailbox
        or threaded=True mode. Won't block other actors.

        Override this instead of on_receive() for blocking operations.
        """
        return NotImplemented  # Must be overridden if used

    async def on_started(self) -> None:
        """Called after creation, before receiving messages."""

    async def on_stopped(self) -> None:
        """Called on graceful shutdown. Release resources here."""

    async def on_restart(self, error: Exception) -> None:
        """Called on the *new* instance before resuming after a crash."""
        ...

    def supervisor_strategy(self) -> SupervisorStrategy:
        """Override to customize how this actor supervises its children.

        Default: OneForOne, up to 3 restarts per 60 seconds, always restart.
        """
        return OneForOneStrategy()

    def stop_policy(self) -> StopPolicy:
        """Override to define when this actor stops itself automatically.

        Default: NEVER (never auto-stop).

        Examples::

            # One-time actor: stops after processing first message
            def stop_policy(self) -> StopPolicy:
                return StopMode.ONE_TIME

            # Stop after receiving "shutdown" message
            def stop_policy(self) -> StopPolicy:
                return AfterMessage(message="shutdown")

            # Stop after 60 seconds of idle time
            def stop_policy(self) -> StopPolicy:
                return AfterIdle(seconds=60.0)
        """
        return StopMode.NEVER

    # -------------------------------------------------------------------------
    # Syntax sugar — delegate to context
    # -------------------------------------------------------------------------

    async def ask(
        self,
        target: type[Actor[Any, Any]] | ActorRef[Any, Any] | str,
        message: Any,
        *,
        timeout: float = 300.0,
        name: str | None = None,
    ) -> Any:
        """Ask an actor (or spawn a temporary one) and wait for reply.

        target can be:
        - Actor class: spawn temporary actor, use once, auto-stop
        - ActorRef: send to existing actor
        - str (path): find actor by path and ask it
        """
        if isinstance(target, str):
            system = self.context._cell.system
            ref = await system.get_actor(target)
            if ref is None:
                raise ValueError(f"Actor not found: {target}")
            return await ref._ask(message, timeout=timeout)
        return await self.context.ask(target, message, timeout=timeout, name=name)

    async def tell(self, target: type[Actor[Any, Any]] | ActorRef[Any, Any] | str, message: Any) -> None:
        """Send a fire-and-forget message to an actor.

        target can be:
        - Actor class: spawn temporary actor, tell message, auto-stop
        - ActorRef: send to existing actor
        - str (path): find actor by path and send

        Raises:
            TypeError: if target is an Actor class with stop_policy == NEVER
        """
        if isinstance(target, type):
            # Check stop_policy to prevent actor leaks
            policy = target().stop_policy()
            if isinstance(policy, StopMode) and policy == StopMode.NEVER:
                raise TypeError(
                    "tell() requires an actor with a non-NEVER stop_policy. "
                    "Use StopMode.ONE_TIME, AfterMessage, or AfterIdle instead."
                )
            # Spawn actor (its stop_policy determines auto-stop behavior)
            actor_name = f"temp-{uuid.uuid4().hex[:8]}"
            ref = await self.context._cell.spawn_child(
                target, actor_name, mailbox_size=DEFAULT_MAILBOX_SIZE, middlewares=None
            )
            await ref._tell(message)
        elif isinstance(target, str):
            system = self.context._cell.system
            ref = await system.get_actor(target)
            if ref is None:
                raise ValueError(f"Actor not found: {target}")
            await ref._tell(message)
        else:
            await target._tell(message)

    async def spawn(
        self,
        actor_cls: type[Actor[Any, Any]],
        name: str,
        *,
        mailbox_size: int | None = None,
    ) -> ActorRef[Any, Any]:
        """Spawn a child actor."""
        return await self.context.spawn(actor_cls, name, mailbox_size=mailbox_size)

    async def stop_self(self) -> None:
        """Stop this actor (self) and remove from parent's children."""
        await self.context.stop_self()
