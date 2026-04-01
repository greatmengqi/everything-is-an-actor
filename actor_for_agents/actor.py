"""Actor base class and per-actor context."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from actor_for_agents.supervision import OneForOneStrategy, SupervisorStrategy

if TYPE_CHECKING:
    from actor_for_agents.ref import ActorRef

# Message and return type variables — use Actor[MyMsg, MyReturn] for typed actors
MsgT = TypeVar("MsgT")
RetT = TypeVar("RetT")


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

    async def ask(
        self,
        target: type[Actor[MsgT, RetT]] | ActorRef[MsgT, RetT],
        message: MsgT,
        *,
        timeout: float = 300.0,
        name: str | None = None,
    ) -> RetT:
        """Spawn an ephemeral child actor (or reuse an existing ref), send one message, return result.

        ``target`` can be:

        - An ``Actor`` subclass — a new child actor is spawned, used once, then stopped.
        - An ``ActorRef`` — the message is sent to the already-running actor.

        Type parameters are inferred from ``target``::

            result: str = await ctx.ask(EchoActor, Task(input="hello"))

        Args:
            name: Optional deterministic name for the ephemeral child actor.
                  Defaults to ``{classname}-{uuid8}``. Provide a stable name
                  to improve log correlation across retries.
        """
        import uuid

        if isinstance(target, type):
            import asyncio

            actor_name = name or f"{target.__name__.lower()}-{uuid.uuid4().hex[:8]}"
            ref = await self.spawn(target, actor_name)
            try:
                return await ref.ask(message, timeout=timeout)
            finally:
                ref.stop()
                current = asyncio.current_task()
                if current is not None and getattr(current, "cancelling", lambda: 0)() > 0:
                    ref.interrupt()
                await ref.join()
        else:
            return await target.ask(message, timeout=timeout)

    async def sequence(
        self,
        tasks: list[tuple[type[Actor[Any, Any]] | ActorRef[Any, Any], Any]],
        *,
        timeout: float = 300.0,
    ) -> list[Any]:
        """Run tasks concurrently and collect results in original order.

        Each entry is a ``(target, message)`` pair — equivalent to Scala's
        ``parTraverse`` / ``parSequence``. Results preserve task order.

        On first failure, remaining siblings are cancelled immediately; their
        ``ask()`` finally blocks (``ref.stop`` + ``join``) still run on
        ``CancelledError``, so no ephemeral children are left running.
        """
        import asyncio

        if not tasks:
            return []
        spawned = [asyncio.create_task(self.ask(t, msg, timeout=timeout)) for t, msg in tasks]
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

    async def traverse(
        self,
        inputs: list[Any],
        target: type[Actor[Any, Any]] | ActorRef[Any, Any],
        *,
        timeout: float = 300.0,
    ) -> list[Any]:
        """Map a list of inputs through the same agent concurrently.

        Equivalent to Scala's ``List[A].parTraverse(f: A => F[B])``.
        Results preserve input order.

            results = await ctx.traverse(["a", "b", "c"], UpperAgent)
            # → ["A", "B", "C"]
        """
        from actor_for_agents.agents.task import Task

        return await self.sequence(
            [(target, Task(input=inp)) for inp in inputs],
            timeout=timeout,
        )

    async def race(
        self,
        tasks: list[tuple[type[Actor[Any, Any]] | ActorRef[Any, Any], Any]],
        *,
        timeout: float = 300.0,
    ) -> Any:
        """Run tasks concurrently and return the first to complete; cancel the rest.

        Equivalent to Scala's ``IO.race`` / Cats Effect ``IO.racePair``.
        If the first to complete raises, the exception propagates and all
        remaining tasks are cancelled.
        """
        import asyncio

        if not tasks:
            raise ValueError("race() requires at least one task")
        spawned = [asyncio.create_task(self.ask(t, msg, timeout=timeout)) for t, msg in tasks]
        try:
            _, pending = await asyncio.wait(spawned, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            # Return result (or raise exception) of the first completed task
            first = next(t for t in spawned if t not in pending)
            return first.result()
        except BaseException:
            for t in spawned:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*spawned, return_exceptions=True)
            raise

    async def zip(
        self,
        task_a: tuple[type[Actor[Any, Any]] | ActorRef[Any, Any], Any],
        task_b: tuple[type[Actor[Any, Any]] | ActorRef[Any, Any], Any],
        *,
        timeout: float = 300.0,
    ) -> tuple[Any, Any]:
        """Run two tasks concurrently and return both results as a typed pair.

        Equivalent to Scala's ``IO.both`` / ``(fa, fb).parTupled``.
        If either task fails, the other is cancelled.

            a, b = await ctx.zip(
                (SearchAgent, Task(input=query)),
                (FactCheckAgent, Task(input=query)),
            )
        """
        results = await self.sequence([task_a, task_b], timeout=timeout)
        return (results[0], results[1])

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
                async for item in ref.ask_stream(message, timeout=timeout):
                    yield item
            finally:
                ref.stop()
                current = asyncio.current_task()
                if current is not None and getattr(current, "cancelling", lambda: 0)() > 0:
                    ref.interrupt()
                await ref.join()
        else:
            async for item in target.ask_stream(message, timeout=timeout):
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
    """

    context: ActorContext

    async def on_receive(self, message: MsgT) -> RetT:
        """Handle an incoming message.

        Return value is sent back as reply for ``ask`` calls.
        For ``tell`` calls, the return value is discarded.
        """
        raise NotImplementedError(f"{type(self).__name__} must implement on_receive()")

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
