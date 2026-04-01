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

from actor_for_agents.actor import Actor, ActorContext, AfterIdle, AfterMessage, StopMode, StopPolicy
from actor_for_agents.agents.system import AgentSystem
from actor_for_agents.frees import Free, FlatMap, Pure, Suspend, lift_free, run_free
from actor_for_agents.actor_f import (
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
from actor_for_agents.interpreter import MockInterpreter, MockRef, MockSystem, run_free_mock
from actor_for_agents.mailbox import Mailbox, MemoryMailbox
from actor_for_agents.middleware import Middleware
from actor_for_agents.ref import ActorRef, MailboxFullError, ReplyChannel
from actor_for_agents.supervision import (
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
from actor_for_agents.system import ActorSystem, DeadLetter

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
    "Either",
    "FlatMap",
    "Free",
    "get_ref",
    "ask",
    "lift_free",
    "Left",
    "Mailbox",
    "MailboxFullError",
    "MemoryMailbox",
    "Middleware",
    "MockInterpreter",
    "MockRef",
    "MockSystem",
    "OneForOneStrategy",
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
    "map2",
    "product",
    "sequence",
    "traverse",
]
