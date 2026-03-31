"""Supervision: parent restarts a failing child automatically."""

import asyncio

from actor_for_agents import Actor, ActorSystem, OneForOneStrategy


class WorkerActor(Actor):
    def __init__(self):
        self._count = 0

    async def on_receive(self, message):
        self._count += 1
        if self._count == 2:
            raise ValueError("simulated crash on message 2")
        return f"ok:{message}"


class ParentActor(Actor):
    def supervisor_strategy(self):
        return OneForOneStrategy(max_restarts=3, within_seconds=60)

    async def on_started(self):
        self.worker = await self.context.spawn(WorkerActor, "worker")

    async def on_receive(self, message):
        return await self.worker.ask(message, timeout=3.0)


async def main():
    system = ActorSystem("demo")
    parent = await system.spawn(ParentActor, "parent")

    print(await parent.ask("msg-1"))  # ok:msg-1
    try:
        await parent.ask("msg-2")    # triggers crash
    except Exception as e:
        print(f"caught: {e}")
    await asyncio.sleep(0.1)         # let supervisor restart worker
    print(await parent.ask("msg-3")) # ok:msg-3  (fresh instance)

    await system.shutdown()


asyncio.run(main())
