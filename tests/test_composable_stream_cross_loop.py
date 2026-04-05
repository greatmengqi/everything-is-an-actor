"""Cross-loop tests for ComposableStream.

Verifies that streams created on one event loop can be consumed, transformed,
and terminated from another event loop via ``.on(loop)`` and cross-loop
channel push operations (offer / put / complete / fail).
"""

import asyncio
import threading

import pytest

from everything_is_an_actor.core.composable_stream import ComposableStream
from everything_is_an_actor.core.composable_future import ComposableFuture

pytestmark = pytest.mark.anyio


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_background_loop() -> tuple[asyncio.AbstractEventLoop, threading.Thread]:
    """Start an event loop in a background thread."""
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def run():
        asyncio.set_event_loop(loop)
        ready.set()
        loop.run_forever()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    ready.wait()
    return loop, t


async def _create_stream_on_bg() -> ComposableStream[int]:
    """Create a simple 0..4 stream on the calling loop."""

    async def _gen():
        for i in range(5):
            await asyncio.sleep(0)
            yield i

    return ComposableStream(_gen())


async def _create_channel_on_bg() -> tuple[ComposableStream[int], object]:
    """Create a channel on the calling loop."""
    return ComposableStream.channel(buffer_size=8)


# ------------------------------------------------------------------
# Consumer-side cross-loop (SX01-SX03)
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sx01_on_loop_basic_iteration():
    """SX01: stream.on(loop) -- create stream on bg_loop, iterate from main loop."""
    bg_loop, bg_thread = _make_background_loop()
    try:
        # Create stream on bg_loop
        fut = asyncio.run_coroutine_threadsafe(_create_stream_on_bg(), bg_loop)
        stream = await asyncio.wrap_future(fut)

        # Consume from main loop via .on(bg_loop)
        result = await stream.on(bg_loop).run_to_list()
        assert result == [0, 1, 2, 3, 4]
    finally:
        bg_loop.call_soon_threadsafe(bg_loop.stop)
        bg_thread.join(timeout=2)


@pytest.mark.asyncio
async def test_sx02_on_loop_with_transform():
    """SX02: on(loop) + transform -- bg stream -> main loop map(x*10) + filter(>10)."""
    bg_loop, bg_thread = _make_background_loop()
    try:
        fut = asyncio.run_coroutine_threadsafe(_create_stream_on_bg(), bg_loop)
        stream = await asyncio.wrap_future(fut)

        result = await (
            stream
            .on(bg_loop)
            .map(lambda x: x * 10)
            .filter(lambda x: x > 10)
            .run_to_list()
        )
        assert result == [20, 30, 40]
    finally:
        bg_loop.call_soon_threadsafe(bg_loop.stop)
        bg_thread.join(timeout=2)


@pytest.mark.asyncio
async def test_sx03_on_loop_with_terminal():
    """SX03: on(loop) + terminal -- bg stream -> main loop run_fold(0, +) -> sum=10."""
    bg_loop, bg_thread = _make_background_loop()
    try:
        fut = asyncio.run_coroutine_threadsafe(_create_stream_on_bg(), bg_loop)
        stream = await asyncio.wrap_future(fut)

        result = await stream.on(bg_loop).run_fold(0, lambda a, b: a + b)
        assert result == 10  # 0+1+2+3+4
    finally:
        bg_loop.call_soon_threadsafe(bg_loop.stop)
        bg_thread.join(timeout=2)


# ------------------------------------------------------------------
# Push-side cross-loop (SX04-SX07)
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sx04_channel_cross_loop_offer():
    """SX04: channel cross-loop offer -- create channel on bg_loop, offer from main, consume on bg via on()."""
    bg_loop, bg_thread = _make_background_loop()
    try:
        # Create channel on bg_loop
        fut = asyncio.run_coroutine_threadsafe(_create_channel_on_bg(), bg_loop)
        stream, sender = await asyncio.wrap_future(fut)

        # Offer from main loop (cross-loop)
        for i in range(5):
            sender.offer(i)

        # Complete from main loop (cross-loop)
        complete_fut = asyncio.run_coroutine_threadsafe(sender.complete(), bg_loop)
        await asyncio.wrap_future(complete_fut)

        # Consume on main loop via .on(bg_loop)
        result = await stream.on(bg_loop).run_to_list()
        assert result == [0, 1, 2, 3, 4]
    finally:
        bg_loop.call_soon_threadsafe(bg_loop.stop)
        bg_thread.join(timeout=2)


@pytest.mark.asyncio
async def test_sx05_channel_cross_loop_put():
    """SX05: channel cross-loop put -- create channel on bg_loop, put from main (backpressure), consume concurrently."""
    bg_loop, bg_thread = _make_background_loop()
    try:
        # Create channel on bg_loop with small buffer to exercise backpressure
        fut = asyncio.run_coroutine_threadsafe(
            _create_channel_on_bg(), bg_loop,
        )
        stream, sender = await asyncio.wrap_future(fut)

        collected: list[int] = []

        async def _consume_on_bg():
            """Consume on bg_loop."""
            async for item in stream:
                collected.append(item)

        # Start consumer on bg_loop
        consumer_fut = asyncio.run_coroutine_threadsafe(_consume_on_bg(), bg_loop)

        # Put from main loop (cross-loop, with backpressure)
        for i in range(5):
            await sender.put(i)
        await sender.complete()

        # Wait for consumer to finish
        await asyncio.wrap_future(consumer_fut)
        assert collected == [0, 1, 2, 3, 4]
    finally:
        bg_loop.call_soon_threadsafe(bg_loop.stop)
        bg_thread.join(timeout=2)


@pytest.mark.asyncio
async def test_sx06_channel_cross_loop_complete_ordering():
    """SX06: channel cross-loop complete -- offers scheduled before complete should not be dropped."""
    bg_loop, bg_thread = _make_background_loop()
    try:
        fut = asyncio.run_coroutine_threadsafe(_create_channel_on_bg(), bg_loop)
        stream, sender = await asyncio.wrap_future(fut)

        # Offer several items from main loop (cross-loop, non-blocking)
        for i in range(5):
            sender.offer(i)

        # Complete from main loop -- the offers are scheduled via
        # call_soon_threadsafe before this complete, so they must not be
        # dropped even though complete() races with the offer callbacks.
        await sender.complete()

        # Consume on main loop via .on(bg_loop)
        result = await stream.on(bg_loop).run_to_list()
        assert result == [0, 1, 2, 3, 4]
    finally:
        bg_loop.call_soon_threadsafe(bg_loop.stop)
        bg_thread.join(timeout=2)


@pytest.mark.asyncio
async def test_sx07_channel_cross_loop_fail():
    """SX07: channel cross-loop fail -- create channel on bg_loop, fail from main, consumer sees exception."""
    bg_loop, bg_thread = _make_background_loop()
    try:
        fut = asyncio.run_coroutine_threadsafe(_create_channel_on_bg(), bg_loop)
        stream, sender = await asyncio.wrap_future(fut)

        # Offer one item, then fail from main loop
        sender.offer(42)
        await sender.fail(ValueError("cross-loop-boom"))

        # Consumer on main loop should see the error after the offered item
        with pytest.raises(ValueError, match="cross-loop-boom"):
            await stream.on(bg_loop).run_to_list()
    finally:
        bg_loop.call_soon_threadsafe(bg_loop.stop)
        bg_thread.join(timeout=2)
