import asyncio

import pytest

from everything_is_an_actor import Actor, ActorSystem
from everything_is_an_actor.plugins.retry import IdempotentActorMixin, RetryEnvelope, ask_with_retry


class AlwaysSlowActor(Actor):
    """Never returns within a short timeout — used to verify retry exhaustion."""

    async def on_receive(self, message):
        await asyncio.sleep(1.0)
        return "never"


class FlakyIdempotentActor(IdempotentActorMixin, Actor):
    async def on_started(self):
        self.calls = 0

    async def on_receive(self, message):
        return await self.handle_idempotent(message, self._handle)

    async def _handle(self, payload):
        self.calls += 1
        if payload == "flaky" and self.calls == 1:
            await asyncio.sleep(0.02)
            return "late"
        return f"ok:{payload}"


@pytest.mark.anyio
async def test_ask_with_retry_timeout_raises():
    # Actor always takes 1s; each attempt (timeout=5ms) times out → exhausts retries.
    # Must NOT use FlakyIdempotentActor here: its idempotency cache would return the
    # result of attempt-1 (completed after the caller timed out) on attempt-2, so the
    # retry would succeed instead of timing out.
    system = ActorSystem("retry")
    ref = await system.spawn(AlwaysSlowActor, "a")

    with pytest.raises(asyncio.TimeoutError):
        await ask_with_retry(
            ref,
            "x",
            timeout=0.005,
            max_attempts=3,
            base_backoff_s=0.001,
            max_backoff_s=0.005,
            jitter_ratio=0.0,
        )

    assert ref.is_alive
    await system.shutdown()


@pytest.mark.anyio
async def test_idempotent_envelope_returns_cached_result():
    system = ActorSystem("retry")
    ref = await system.spawn(FlakyIdempotentActor, "a")

    m1 = RetryEnvelope.wrap("x", idempotency_key="same-key")
    m2 = RetryEnvelope.wrap("x", idempotency_key="same-key", attempt=2, max_attempts=3)

    r1 = await ref.ask(m1, timeout=1.0)
    r2 = await ref.ask(m2, timeout=1.0)

    assert r1 == "ok:x"
    assert r2 == "ok:x"
    # handler should run once due to idempotency cache
    actor = ref._cell.actor
    assert actor.calls == 1

    await system.shutdown()
