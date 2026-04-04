"""Basic usage: spawn an actor, ask and tell."""

import asyncio

from everything_is_an_actor import Actor, ActorSystem


class GreetActor(Actor):
    async def on_receive(self, message):
        return f"Hello, {message}!"


async def main():
    system = ActorSystem("demo")
    ref = await system.spawn(GreetActor, "greeter")

    reply = await system.ask(ref, "world")
    print(reply)  # Hello, world!

    await system.tell(ref, "fire-and-forget")

    await system.shutdown()


asyncio.run(main())
