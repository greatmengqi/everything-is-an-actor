import pytest

redis = pytest.importorskip("redis.asyncio")

from actor_for_agents.plugins.redis import RedisMailbox
from actor_for_agents.ref import _Envelope, _Stop


pytestmark = pytest.mark.anyio


async def _make_mailbox(queue_name: str, *, maxlen: int = 0) -> RedisMailbox:
    client = redis.Redis(host="127.0.0.1", port=6379, decode_responses=False)
    await client.ping()
    await client.delete(queue_name)
    mailbox = RedisMailbox(client.connection_pool, queue_name, maxlen=maxlen, brpop_timeout=0.2)
    return mailbox


async def test_roundtrip_envelope_and_stop():
    queue = "deerflow:test:redis-mailbox:roundtrip"
    mailbox = await _make_mailbox(queue)
    try:
        msg = _Envelope(payload={"k": "v"}, correlation_id="c1", reply_to="sysA")
        ok = await mailbox.put(msg)
        assert ok is True

        got = await mailbox.get()
        assert isinstance(got, _Envelope)
        assert got.payload == {"k": "v"}
        assert got.correlation_id == "c1"
        assert got.reply_to == "sysA"

        ok = await mailbox.put(_Stop())
        assert ok is True
        stop = await mailbox.get()
        assert isinstance(stop, _Stop)
    finally:
        await mailbox.close()


async def test_bounded_queue_rejects_when_full():
    queue = "deerflow:test:redis-mailbox:bounded"
    mailbox = await _make_mailbox(queue, maxlen=1)
    try:
        assert await mailbox.put(_Envelope("m1")) is True
        assert await mailbox.put(_Envelope("m2")) is False
    finally:
        await mailbox.close()


async def test_put_nowait_and_get_nowait_contract():
    queue = "deerflow:test:redis-mailbox:nowait"
    mailbox = await _make_mailbox(queue)
    try:
        assert mailbox.put_nowait(_Envelope("x")) is False
        with pytest.raises(Exception, match="does not support synchronous get_nowait"):
            mailbox.get_nowait()
    finally:
        await mailbox.close()


async def test_system_enqueue_fallback_with_async_mailbox():
    from actor_for_agents import Actor, ActorSystem

    class EchoActor(Actor):
        async def on_receive(self, message):
            return message

    queue = "deerflow:test:redis-mailbox:system-fallback"
    mailbox = await _make_mailbox(queue)

    system = ActorSystem("redis-test")
    ref = await system.spawn(EchoActor, "echo", mailbox=mailbox)
    try:
        # This exercises _ActorCell.enqueue fallback path:
        # put_nowait() -> False, then await put() -> True
        result = await ref.ask("hello", timeout=3.0)
        assert result == "hello"
    finally:
        await system.shutdown()
