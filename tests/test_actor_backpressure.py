import asyncio

import pytest

from everything_is_an_actor import Actor, ActorSystem, MailboxFullError
from everything_is_an_actor.core.mailbox import BACKPRESSURE_BLOCK, BACKPRESSURE_DROP_NEW, BACKPRESSURE_FAIL, MemoryMailbox


class SlowActor(Actor):
    async def on_started(self):
        self.count = 0

    async def on_receive(self, message):
        if message == "inc":
            await asyncio.sleep(0.01)
            self.count += 1
            return None
        if message == "get":
            return self.count
        return None


@pytest.mark.anyio
async def test_memory_mailbox_drop_new_policy_drops_tell_to_dead_letters():
    system = ActorSystem("bp")
    ref = await system.spawn(
        SlowActor,
        "slow",
        mailbox=MemoryMailbox(1, backpressure_policy=BACKPRESSURE_DROP_NEW),
    )

    # Overfill quickly
    for _ in range(20):
        await system.tell(ref, "inc")

    await asyncio.sleep(0.4)
    count = await system.ask(ref, "get", timeout=2.0)
    await system.shutdown()

    # Some messages should be dropped under drop_new
    assert count < 20
    assert len(system.dead_letters) > 0


@pytest.mark.anyio
async def test_memory_mailbox_fail_policy_rejects_ask_when_full():
    system = ActorSystem("bp")
    ref = await system.spawn(
        SlowActor,
        "slow",
        mailbox=MemoryMailbox(1, backpressure_policy=BACKPRESSURE_FAIL),
    )

    # Fire many concurrent asks while the mailbox is size-1 and the actor is
    # busy with each message — at least one should hit a full queue and be
    # rejected with MailboxFullError. Concurrent firing avoids a race where
    # the actor drains the queue between sequential attempts.
    results = await asyncio.gather(
        *(system.ask(ref, "inc", timeout=0.5) for _ in range(20)),
        return_exceptions=True,
    )
    await system.shutdown()

    rejected = [r for r in results if isinstance(r, MailboxFullError)]
    assert rejected, f"Expected at least one MailboxFullError, got: {[type(r).__name__ for r in results]}"


@pytest.mark.anyio
async def test_memory_mailbox_block_policy_eventually_accepts():
    system = ActorSystem("bp")
    ref = await system.spawn(
        SlowActor,
        "slow",
        mailbox=MemoryMailbox(1, backpressure_policy=BACKPRESSURE_BLOCK),
    )

    for _ in range(10):
        await system.tell(ref, "inc")

    await asyncio.sleep(0.25)
    count = await system.ask(ref, "get", timeout=2.0)
    await system.shutdown()

    # Block policy should avoid dropping on tell path
    assert count == 10
