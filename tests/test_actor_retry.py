import asyncio

import pytest

from actor_for_agents import Actor, ActorSystem, IdempotentActorMixin, RetryEnvelope, ask_with_retry


class FlakyIdempotentActor(IdempotentActorMixin, Actor):
    async def on_started(self):
        self.calls = 0

    async def on_receive(self, message):
        return await self.handle_idempotent(message, self._handle)

    async def _handle(self, payload):
        self.calls += 1
        if payload == 'flaky' and self.calls == 1:
            await asyncio.sleep(0.02)
            return 'late'
        return f"ok:{payload}"


@pytest.mark.anyio
async def test_ask_with_retry_timeout_raises():
    system = ActorSystem('retry')
    ref = await system.spawn(FlakyIdempotentActor, 'a')

    with pytest.raises(asyncio.TimeoutError):
        await ask_with_retry(
            ref,
            'flaky',
            timeout=0.005,
            max_attempts=3,
            base_backoff_s=0.001,
            max_backoff_s=0.005,
            jitter_ratio=0.0,
            idempotency_key='k1',
        )

    # This helper retries timeout, but if each attempt times out it should raise.
    assert ref.is_alive
    await system.shutdown()


@pytest.mark.anyio
async def test_idempotent_envelope_returns_cached_result():
    system = ActorSystem('retry')
    ref = await system.spawn(FlakyIdempotentActor, 'a')

    m1 = RetryEnvelope.wrap('x', idempotency_key='same-key')
    m2 = RetryEnvelope.wrap('x', idempotency_key='same-key', attempt=2, max_attempts=3)

    r1 = await ref.ask(m1, timeout=1.0)
    r2 = await ref.ask(m2, timeout=1.0)

    assert r1 == 'ok:x'
    assert r2 == 'ok:x'
    # handler should run once due to idempotency cache
    actor = ref._cell.actor
    assert actor.calls == 1

    await system.shutdown()
