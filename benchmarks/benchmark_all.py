#!/usr/bin/env python3
import asyncio
import time
from actor_for_agents import Actor, ActorSystem
from actor_for_agents.mailbox import MemoryMailbox, FastMailbox, ThreadedMailbox

class SyncActor(Actor):
    def receive(self, message):
        return message

class AsyncActor(Actor):
    async def on_receive(self, message):
        return message

async def bench_tell(name, system, actor_cls, n=500000):
    ref = await system.spawn(actor_cls, 'echo', mailbox_size=n+10)
    start = time.perf_counter()
    for _ in range(n):
        await ref.tell('msg')
    elapsed = time.perf_counter() - start
    await system.shutdown()
    return n/elapsed

async def bench_ask(name, system, actor_cls, n=50000):
    ref = await system.spawn(actor_cls, 'echo')
    start = time.perf_counter()
    for _ in range(n):
        await ref.ask('msg', timeout=5.0)
    elapsed = time.perf_counter() - start
    await system.shutdown()
    return n/elapsed

async def main():
    n_tell = 500000
    n_ask = 50000

    print('=== tell benchmark ===')
    for mailbox_cls, name in [(MemoryMailbox, 'MemoryMailbox'), (FastMailbox, 'FastMailbox'), (ThreadedMailbox, 'ThreadedMailbox')]:
        r1 = await bench_tell(name+'+Sync', ActorSystem('s', mailbox_cls=mailbox_cls), SyncActor, n_tell)
        r2 = await bench_tell(name+'+Async', ActorSystem('a', mailbox_cls=mailbox_cls), AsyncActor, n_tell)
        print(f'{name}: receive()={r1/1000:.1f}K/s, on_receive()={r2/1000:.1f}K/s')

    print()
    print('=== ask benchmark ===')
    for mailbox_cls, name in [(MemoryMailbox, 'MemoryMailbox'), (FastMailbox, 'FastMailbox'), (ThreadedMailbox, 'ThreadedMailbox')]:
        r1 = await bench_ask(name+'+Sync', ActorSystem('s', mailbox_cls=mailbox_cls), SyncActor, n_ask)
        r2 = await bench_ask(name+'+Async', ActorSystem('a', mailbox_cls=mailbox_cls), AsyncActor, n_ask)
        print(f'{name}: receive()={r1/1000:.1f}K/s, on_receive()={r2/1000:.1f}K/s')

asyncio.run(main())