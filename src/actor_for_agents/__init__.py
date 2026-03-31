"""Async Actor framework — lightweight, asyncio-native, supervision-ready.

Usage::

    from actor_for_agents import Actor, ActorSystem

    class Greeter(Actor):
        async def on_receive(self, message):
            return f"Hello, {message}!"

    async def main():
        system = ActorSystem("app")
        ref = await system.spawn(Greeter, "greeter")
        reply = await ref.ask("World", timeout=5.0)
        print(reply)  # Hello, World!
        await system.shutdown()
"""

from actor_for_agents.actor import Actor, ActorContext
from actor_for_agents.mailbox import Mailbox, MemoryMailbox
from actor_for_agents.middleware import Middleware
from actor_for_agents.ref import ActorRef, MailboxFullError, ReplyChannel
from .retry import IdempotentActorMixin, IdempotencyStore, RetryEnvelope, ask_with_retry
from actor_for_agents.supervision import AllForOneStrategy, Directive, OneForOneStrategy, SupervisorStrategy
from .system import ActorSystem, DeadLetter

__all__ = [
    "Actor",
    "ActorContext",
    "ActorRef",
    "ActorSystem",
    "AllForOneStrategy",
    "DeadLetter",
    "Directive",
    "Mailbox",
    "MailboxFullError",
    "MemoryMailbox",
    "Middleware",
    "OneForOneStrategy",
    "ReplyChannel",
    "RetryEnvelope",
    "SupervisorStrategy",
    "IdempotentActorMixin",
    "IdempotencyStore",
    "ask_with_retry",
]
