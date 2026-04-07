"""Tests for ComposableStream.channel() and StreamSender."""

import asyncio
import pytest

pytestmark = pytest.mark.anyio

from everything_is_an_actor.core.composable_stream import (
    BufferOverflowError,
    ComposableStream,
    OfferResult,
    OverflowStrategy,
    StreamClosedError,
    StreamSender,
)


@pytest.mark.asyncio
async def test_basic_channel():
    stream, sender = ComposableStream.channel(buffer_size=4)
    sender.offer(1)
    sender.offer(2)
    sender.offer(3)
    await sender.complete()
    result = await stream.map(lambda x: x * 10).run_to_list()
    assert result == [10, 20, 30]


@pytest.mark.asyncio
async def test_drop_new():
    s, tx = ComposableStream.channel(buffer_size=2, overflow=OverflowStrategy.DROP_NEW)
    assert tx.offer("a") == OfferResult.ENQUEUED
    assert tx.offer("b") == OfferResult.ENQUEUED
    assert tx.offer("c") == OfferResult.DROPPED
    await tx.complete()
    assert await s.run_to_list() == ["a", "b"]


@pytest.mark.asyncio
async def test_drop_head():
    s, tx = ComposableStream.channel(buffer_size=2, overflow=OverflowStrategy.DROP_HEAD)
    tx.offer(1)
    tx.offer(2)
    tx.offer(3)
    await tx.complete()
    assert await s.run_to_list() == [2, 3]


@pytest.mark.asyncio
async def test_fail_strategy():
    s, tx = ComposableStream.channel(buffer_size=1, overflow=OverflowStrategy.FAIL)
    tx.offer("x")
    with pytest.raises(BufferOverflowError):
        tx.offer("y")
    await tx.complete()
    assert await s.run_to_list() == ["x"]


@pytest.mark.asyncio
async def test_put_backpressure():
    s, tx = ComposableStream.channel(buffer_size=2)
    await tx.put(10)
    await tx.put(20)
    await tx.complete()
    assert await s.run_to_list() == [10, 20]


@pytest.mark.asyncio
async def test_fail_error_propagation():
    s, tx = ComposableStream.channel()
    tx.offer("ok")
    await tx.fail(ValueError("boom"))
    with pytest.raises(ValueError, match="boom"):
        await s.run_to_list()


@pytest.mark.asyncio
async def test_closed_sender():
    s, tx = ComposableStream.channel()
    await tx.complete()
    assert tx.offer("late") == OfferResult.CLOSED
    assert tx.is_closed


@pytest.mark.asyncio
async def test_put_on_closed_raises():
    _, tx = ComposableStream.channel()
    await tx.complete()
    with pytest.raises(StreamClosedError):
        await tx.put("nope")


@pytest.mark.asyncio
async def test_channel_with_filter_and_fold():
    s, tx = ComposableStream.channel(buffer_size=8)
    for i in range(6):
        tx.offer(i)
    await tx.complete()
    result = await s.filter(lambda x: x % 2 == 0).run_fold(0, lambda a, b: a + b)
    assert result == 6  # 0 + 2 + 4


@pytest.mark.asyncio
async def test_concurrent_put_and_consume():
    s, tx = ComposableStream.channel(buffer_size=2)

    async def producer():
        for i in range(5):
            await tx.put(i)
        await tx.complete()

    async def consumer():
        return await s.run_to_list()

    _, result = await asyncio.gather(producer(), consumer())
    assert result == [0, 1, 2, 3, 4]
