import asyncio

import pytest

from actor_for_agents import Actor, ActorSystem, MailboxFullError
from actor_for_agents.mailbox import BACKPRESSURE_BLOCK, BACKPRESSURE_DROP_NEW, BACKPRESSURE_FAIL, MemoryMailbox


class SlowActor(Actor):
    async def on_started(self):
        self.count = 0

    async def on_receive(self, message):
        if message == 'inc':
            await asyncio.sleep(0.01)
            self.count += 1
            return None
        if message == 'get':
            return self.count
        return None


@pytest.mark.anyio
async def test_memory_mailbox_drop_new_policy_drops_tell_to_dead_letters():
    system = ActorSystem('bp')
    ref = await system.spawn(
        SlowActor,
        'slow',
        mailbox=MemoryMailbox(1, backpressure_policy=BACKPRESSURE_DROP_NEW),
    )

    # Overfill quickly
    for _ in range(20):
        await ref.tell('inc')

    await asyncio.sleep(0.4)
    count = await ref.ask('get', timeout=2.0)
    await system.shutdown()

    # Some messages should be dropped under drop_new
    assert count < 20
    assert len(system.dead_letters) > 0


@pytest.mark.anyio
async def test_memory_mailbox_fail_policy_rejects_ask_when_full():
    system = ActorSystem('bp')
    ref = await system.spawn(
        SlowActor,
        'slow',
        mailbox=MemoryMailbox(1, backpressure_policy=BACKPRESSURE_FAIL),
    )

    # Fill queue with tell first
    await ref.tell('inc')

    # Then ask may be rejected when queue still full
    got_reject = False
    for _ in range(30):
        try:
            await ref.ask('inc', timeout=0.02)
        except MailboxFullError:
            got_reject = True
            break
        except asyncio.TimeoutError:
            pass

    await system.shutdown()
    assert got_reject


@pytest.mark.anyio
async def test_memory_mailbox_block_policy_eventually_accepts():
    system = ActorSystem('bp')
    ref = await system.spawn(
        SlowActor,
        'slow',
        mailbox=MemoryMailbox(1, backpressure_policy=BACKPRESSURE_BLOCK),
    )

    for _ in range(10):
        await ref.tell('inc')

    await asyncio.sleep(0.25)
    count = await ref.ask('get', timeout=2.0)
    await system.shutdown()

    # Block policy should avoid dropping on tell path
    assert count == 10
