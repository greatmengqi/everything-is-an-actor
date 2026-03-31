"""Basic usage: spawn an actor, ask and tell."""

import asyncio

from actor_for_agents import Actor, ActorSystem


class GreetActor(Actor):
    async def on_receive(self, message):
        return f"Hello, {message}!"


async def main():
    system = ActorSystem("demo")
    ref = await system.spawn(GreetActor, "greeter")

    reply = await ref.ask("world")
    print(reply)  # Hello, world!

    await ref.tell("fire-and-forget")

    await system.shutdown()


asyncio.run(main())
