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
        ...

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
