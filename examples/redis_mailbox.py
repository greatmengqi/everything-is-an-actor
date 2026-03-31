"""Redis mailbox: durable inbox that survives process restarts.

Requires: pip install actor-for-agents[redis]
Requires: Redis running on localhost:6379
"""

import asyncio

import redis.asyncio as redis

from actor_for_agents import Actor, ActorSystem
from actor_for_agents.plugins.redis import RedisMailbox


class EchoActor(Actor):
    async def on_receive(self, message):
        return message


async def main():
    pool = redis.ConnectionPool.from_url("redis://localhost:6379")

    system = ActorSystem("demo")
    ref = await system.spawn(
        EchoActor,
        "echo",
        mailbox=RedisMailbox(pool, "actor:inbox:echo", maxlen=1000),
    )

    result = await ref.ask({"event": "ping"}, timeout=5.0)
    print(result)  # {'event': 'ping'}

    await system.shutdown()


asyncio.run(main())
