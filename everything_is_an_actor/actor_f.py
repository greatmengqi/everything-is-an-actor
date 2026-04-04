"""Actor Free Monad — describe actor operations as pure data.

This module provides the base functor (ActorF) and smart constructors
for building actor workflows without executing them.

Example::

    from everything_is_an_actor.frees import Free
    from everything_is_an_actor.actor_f import ActorF, SpawnF, AskF, TellF

    def workflow(ref: ActorRef) -> Free[ActorF, str]:
        return (
            SpawnF("greeter", GreeterActor)
            .flatMap(lambda r: TellF(r, "hello").flatMap(lambda _: AskF(r, "status")))
        )

    # Execute with live interpreter
    result = await system.run_free(workflow(None))
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Generic, TypeVar

from everything_is_an_actor.frees import Free, Suspend

if TYPE_CHECKING:
    from everything_is_an_actor.ref import ActorRef

A = TypeVar("A")
B = TypeVar("B")
MsgT = TypeVar("MsgT")
RetT = TypeVar("RetT")


# ---------------------------------------------------------------------------
# Actor Base Functor
# ---------------------------------------------------------------------------


class ActorF(ABC):
    """Base functor for actor operations.

    All actor operations are encoded as instances of this class (or its
    subclasses). The Free monad suspends these operations until an
    interpreter decides how to execute them.

    Categorically: ActorF is a Functor (it implements fmap).
    """

    @staticmethod
    def fmap(f: Callable[[A], B]) -> "ActorF":
        """Functor map over the result type of this operation."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Primitive Actor Operations
# ---------------------------------------------------------------------------


@dataclass
class SpawnF(ActorF, Generic[MsgT, RetT]):
    """spawn : (name, actor_cls) → ActorRef"""

    name: str
    actor_cls: type  # type: ignore[type-arg]

    def fmap(self, f: Callable[[Any], B]) -> "SpawnF[MsgT, RetT]":
        # Spawn's result is ActorRef, we map over that
        return self  # type: ignore[return-value]


@dataclass
class TellF(ActorF):
    """tell : (ActorRef, msg) → ()"""

    ref: "ActorRef"  # type: ignore[type-arg]
    msg: Any

    def fmap(self, f: Callable[[Any], B]) -> "TellF":
        return self


@dataclass
class AskF(ActorF, Generic[MsgT, RetT]):
    """ask : (ActorRef, msg) → RetT"""

    ref: "ActorRef"  # type: ignore[type-arg]
    msg: MsgT

    def fmap(self, f: Callable[[RetT], B]) -> "AskF[MsgT, B]":
        return AskF(self.ref, self.msg)


@dataclass
class StopF(ActorF):
    """stop : ActorRef → ()"""

    ref: "ActorRef"  # type: ignore[type-arg]

    def fmap(self, f: Callable[[Any], B]) -> "StopF":
        return self


@dataclass
class GetRefF(ActorF):
    """get_ref : () → ActorRef (the caller's own ref)"""

    def fmap(self, f: Callable[[Any], B]) -> "GetRefF":
        return self


# ---------------------------------------------------------------------------
# Smart Constructors (lift into Free)
# ---------------------------------------------------------------------------


def spawn(name: str, actor_cls: type) -> Free[ActorF, "ActorRef"]:
    """Lift a Spawn operation into the Free monad."""
    return Suspend(SpawnF(name, actor_cls))


def tell(ref: "ActorRef", msg: Any) -> Free[ActorF, None]:
    """Lift a Tell operation into the Free monad."""
    return Suspend(TellF(ref, msg))


async def tell_direct(ref: "ActorRef", msg: Any) -> None:
    """Direct tell — bypasses Free monad for maximum performance (fire-and-forget).

    Use this for high-throughput fire-and-forget messaging where the extra
    Free monad allocation overhead is not acceptable.
    """
    await ref._tell(msg)


def ask(ref: "ActorRef", msg: MsgT) -> Free[ActorF, RetT]:
    """Lift an Ask operation into the Free monad."""
    return Suspend(AskF(ref, msg))


def stop(ref: "ActorRef") -> Free[ActorF, None]:
    """Lift a Stop operation into the Free monad."""
    return Suspend(StopF(ref))


def get_ref() -> Free[ActorF, "ActorRef"]:
    """Lift a GetRef operation into the Free monad."""
    return Suspend(GetRefF())


# ---------------------------------------------------------------------------
# Interpreter exports
# ---------------------------------------------------------------------------

__all__ = [
    "ActorF",
    "SpawnF",
    "TellF",
    "AskF",
    "StopF",
    "GetRefF",
    "spawn",
    "tell",
    "ask",
    "stop",
    "get_ref",
]
