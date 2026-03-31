"""Middleware pipeline — cross-cutting concerns for actors.

Inspired by Proto.Actor's sender/receiver middleware model.
Middleware intercepts messages before/after the actor processes them.

Usage::

    class LoggingMiddleware(Middleware):
        async def on_receive(self, ctx, message, next_fn):
            logger.info("Received: %s", message)
            result = await next_fn(ctx, message)
            logger.info("Replied: %s", result)
            return result

    system = ActorSystem("app")
    ref = await system.spawn(MyActor, "a", middlewares=[LoggingMiddleware()])
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


class ActorMailboxContext:
    """Context passed to middleware on each message."""

    __slots__ = ("actor_ref", "sender", "message_type")

    def __init__(self, actor_ref: Any, sender: Any, message_type: str) -> None:
        self.actor_ref = actor_ref
        self.sender = sender
        self.message_type = message_type  # "tell" or "ask"


# The inner handler signature: (ctx, message) -> result
NextFn = Callable[[ActorMailboxContext, Any], Awaitable[Any]]


class Middleware:
    """Base class for actor middleware.

    Override ``on_receive`` to intercept inbound messages.
    Must call ``await next_fn(ctx, message)`` to continue the chain.
    """

    async def on_receive(self, ctx: ActorMailboxContext, message: Any, next_fn: NextFn) -> Any:
        """Intercept a message. Call next_fn to continue the chain."""
        return await next_fn(ctx, message)

    async def on_started(self, actor_ref: Any) -> None:
        """Called when the actor starts."""

    async def on_stopped(self, actor_ref: Any) -> None:
        """Called when the actor stops."""

    async def on_restart(self, actor_ref: Any, error: Exception) -> None:
        """Called when the actor restarts after a crash.

        Override to reset per-actor-instance state (caches, counters, etc.)
        that should not bleed across restarts.
        """


def build_middleware_chain(middlewares: list[Middleware], handler: NextFn) -> NextFn:
    """Build a nested middleware chain ending with *handler*.

    Execution order: first middleware in list wraps outermost.
    ``[A, B, C]`` → ``A(B(C(handler)))``
    """
    chain = handler
    for mw in reversed(middlewares):
        outer = chain

        async def _wrap(ctx: ActorMailboxContext, msg: Any, _mw: Middleware = mw, _next: NextFn = outer) -> Any:
            return await _mw.on_receive(ctx, msg, _next)

        chain = _wrap
    return chain
