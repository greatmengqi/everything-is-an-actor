"""Async Actor framework — lightweight, asyncio-native, supervision-ready.

Usage::

    from everything_is_an_actor import Actor, ActorSystem

    class Greeter(Actor):
        async def on_receive(self, message):
            return f"Hello, {message}!"

    async def main():
        system = ActorSystem("app")
        ref = await system.spawn(Greeter, "greeter")
        reply = await system.ask(ref, "World", timeout=5.0)
        print(reply)  # Hello, World!
        await system.shutdown()
"""

from everything_is_an_actor.core.actor import Actor, ActorContext, AfterIdle, AfterMessage, StopMode, StopPolicy
from everything_is_an_actor.agents.system import AgentSystem
from everything_is_an_actor.core.dispatcher import Dispatcher, PoolDispatcher
from everything_is_an_actor.core.frees import Free, FlatMap, Pure, Suspend, lift_free, run_free
from everything_is_an_actor.core.actor_f import (
    ActorF,
    AskF,
    SpawnF,
    StopF,
    TellF,
    ask,
    get_ref,
    spawn,
    stop,
    tell,
    tell_direct,
)
from everything_is_an_actor.core.interpreter import MockInterpreter, MockRef, MockSystem, run_free_mock
from everything_is_an_actor.core.mailbox import FastMailbox, Mailbox, MemoryMailbox, ThreadedMailbox
from everything_is_an_actor.core.middleware import Middleware
from everything_is_an_actor.core.ref import ActorRef, MailboxFullError, ReplyChannel
from everything_is_an_actor.core.supervision import (
    AllForOneStrategy,
    Directive,
    DirectiveResult,
    Either,
    Left,
    OneForOneStrategy,
    Right,
    SupervisorStrategy,
    map2,
    product,
    sequence,
    traverse,
)
from everything_is_an_actor.core.system import ActorSystem, DeadLetter
from everything_is_an_actor.core.virtual import RegistryStore, VirtualActorRegistry

__all__ = [
    "Actor",
    "ActorContext",
    "ActorF",
    "ActorRef",
    "ActorSystem",
    "AfterIdle",
    "AfterMessage",
    "AgentSystem",
    "AllForOneStrategy",
    "AskF",
    "DeadLetter",
    "Directive",
    "DirectiveResult",
    "Dispatcher",
    "Either",
    "FlatMap",
    "Free",
    "get_ref",
    "ask",
    "lift_free",
    "Left",
    "FastMailbox",
    "Mailbox",
    "MailboxFullError",
    "MemoryMailbox",
    "Middleware",
    "ThreadedMailbox",
    "MockInterpreter",
    "MockRef",
    "MockSystem",
    "OneForOneStrategy",
    "PoolDispatcher",
    "Pure",
    "ReplyChannel",
    "Right",
    "run_free",
    "run_free_mock",
    "spawn",
    "stop",
    "StopMode",
    "StopPolicy",
    "SupervisorStrategy",
    "Suspend",
    "TellF",
    "SpawnF",
    "StopF",
    "tell",
    "tell_direct",
    "RegistryStore",
    "VirtualActorRegistry",
    "map2",
    "product",
    "sequence",
    "traverse",
]
