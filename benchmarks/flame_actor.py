"""Generate flame graph for actor framework."""

import asyncio
import sys
sys.path.insert(0, '.')

from actor_for_agents import Actor, ActorSystem


class NoopActor(Actor):
    async def on_receive(self, message):
        return message


async def bench():
    system = ActorSystem("bench")
    ref = await system.spawn(NoopActor, "echo", mailbox_size=100000)

    for _ in range(100000):
        await ref.tell("msg")

    await system.shutdown()


if __name__ == "__main__":
    asyncio.run(bench())