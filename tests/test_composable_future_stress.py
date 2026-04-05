"""Adversarial and stress tests for ComposableFuture.

Covers: cancellation propagation, cross-loop bridge lifecycle,
first_completed races, zip/sequence mixed loops, result() edge cases,
and memory leak detection.
"""

import asyncio
import gc
import threading

import pytest

pytestmark = pytest.mark.anyio

from everything_is_an_actor.core.composable_future import ComposableFuture as Cf


def _make_background_loop() -> tuple[asyncio.AbstractEventLoop, threading.Thread]:
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


def _stop_loop(loop, thread):
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=2.0)


# ==================================================================
# Cancellation propagation
# ==================================================================


@pytest.mark.asyncio
async def test_cancel_propagates_upstream():
    """Cancelling an outer task cancels the inner ComposableFuture chain."""
    started = asyncio.Event()
    cancelled = False

    async def slow():
        nonlocal cancelled
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled = True
            raise

    async def _run():
        return await Cf(slow()).map(lambda x: x)
    task = asyncio.create_task(_run())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled


@pytest.mark.asyncio
async def test_cancel_cross_loop_bridge_forward():
    """Source future cancelled → proxy on caller loop gets cancelled."""
    bg_loop, bg_thread = _make_background_loop()
    try:
        source_fut = bg_loop.create_future()

        async def get_result():
            return await Cf(source_fut, loop=bg_loop)

        task = asyncio.create_task(get_result())
        await asyncio.sleep(0.01)

        # Cancel source on bg_loop
        bg_loop.call_soon_threadsafe(source_fut.cancel)
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        _stop_loop(bg_loop, bg_thread)


@pytest.mark.asyncio
async def test_cancel_cross_loop_bridge_backward():
    """Cancelling proxy on caller loop propagates cancel to source future."""
    bg_loop, bg_thread = _make_background_loop()
    try:
        source_fut = bg_loop.create_future()

        async def get_result():
            return await Cf(source_fut, loop=bg_loop)

        task = asyncio.create_task(get_result())
        await asyncio.sleep(0.05)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Give bg_loop time to process the cancel
        await asyncio.sleep(0.05)

        # Source should be cancelled too
        assert source_fut.cancelled()
    finally:
        _stop_loop(bg_loop, bg_thread)


@pytest.mark.asyncio
async def test_with_timeout_cancels_inner():
    """with_timeout cancels the inner work on expiry."""
    cancelled = False

    async def slow():
        nonlocal cancelled
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled = True
            raise

    with pytest.raises(asyncio.TimeoutError):
        await Cf(slow()).with_timeout(0.01)

    await asyncio.sleep(0.01)
    assert cancelled


@pytest.mark.asyncio
async def test_with_timeout_cross_loop():
    """with_timeout works correctly with pinned loop."""
    bg_loop, bg_thread = _make_background_loop()
    try:
        async def slow():
            await asyncio.sleep(10)

        with pytest.raises(asyncio.TimeoutError):
            await Cf(slow(), loop=bg_loop).with_timeout(0.05)
    finally:
        _stop_loop(bg_loop, bg_thread)


# ==================================================================
# first_completed races
# ==================================================================


@pytest.mark.asyncio
async def test_first_completed_prefers_success_over_failure():
    """When done set has both success and failure, success wins."""
    result = await Cf.first_completed(
        Cf.failed(ValueError("boom")),
        Cf.successful("ok"),
    )
    assert result == "ok"


@pytest.mark.asyncio
async def test_first_completed_all_fail():
    """When all futures fail, surfaces an exception (order is nondeterministic)."""
    with pytest.raises(ValueError, match="first|second"):
        await Cf.first_completed(
            Cf.failed(ValueError("first")),
            Cf.failed(ValueError("second")),
        )


@pytest.mark.asyncio
async def test_first_completed_cancel_pending_stops_losers():
    """cancel_pending=True actually stops loser side effects."""
    effects = []

    async def side_effecting():
        await asyncio.sleep(0.05)
        effects.append("should_not_happen")
        return "slow"

    await Cf.first_completed(
        Cf(side_effecting()),
        Cf.successful("fast"),
        cancel_pending=True,
    )

    # Wait for potential side effect to land
    await asyncio.sleep(0.1)
    assert effects == []


@pytest.mark.asyncio
async def test_first_completed_no_cancel_allows_losers():
    """cancel_pending=False lets losers finish."""
    effects = []

    async def side_effecting():
        await asyncio.sleep(0.05)
        effects.append("executed")
        return "slow"

    await Cf.first_completed(
        Cf(side_effecting()),
        Cf.successful("fast"),
        cancel_pending=False,
    )

    await asyncio.sleep(0.15)
    assert effects == ["executed"]


@pytest.mark.asyncio
async def test_first_completed_loser_exception_no_warning():
    """Loser exception with cancel_pending=False doesn't cause 'never retrieved' warning."""
    async def failing():
        await asyncio.sleep(0.05)
        raise ValueError("loser failed")

    # Should not emit any warning
    result = await Cf.first_completed(
        Cf(failing()),
        Cf.successful("winner"),
        cancel_pending=False,
    )
    assert result == "winner"
    await asyncio.sleep(0.1)


# ==================================================================
# zip / sequence mixed loops
# ==================================================================


@pytest.mark.asyncio
async def test_zip_cross_loop():
    """zip works with futures on different loops."""
    bg_loop, bg_thread = _make_background_loop()
    try:
        async def on_bg():
            await asyncio.sleep(0)
            return "bg"

        local = Cf.successful("local")
        remote = Cf(on_bg(), loop=bg_loop)

        result = await local.zip(remote)
        assert result == ("local", "bg")
    finally:
        _stop_loop(bg_loop, bg_thread)


@pytest.mark.asyncio
async def test_sequence_cross_loop():
    """sequence with mixed-loop futures."""
    bg_loop, bg_thread = _make_background_loop()
    try:
        async def on_bg(v):
            return v * 2

        futures = [
            Cf.successful(1),
            Cf(on_bg(2), loop=bg_loop),
            Cf.successful(3),
        ]
        result = await Cf.sequence(futures)
        assert result == [1, 4, 3]
    finally:
        _stop_loop(bg_loop, bg_thread)


# ==================================================================
# result() edge cases
# ==================================================================


def test_result_from_plain_thread():
    """result() works from a non-async thread."""
    assert Cf.successful(42).map(lambda x: x + 1).result() == 43


def test_result_cross_loop_from_thread():
    """result() with pinned loop from a non-async thread."""
    bg_loop, bg_thread = _make_background_loop()
    try:
        async def work():
            return 77

        result = Cf(work(), loop=bg_loop).map(lambda x: x * 2).result(timeout=5.0)
        assert result == 154
    finally:
        _stop_loop(bg_loop, bg_thread)


@pytest.mark.asyncio
async def test_result_rejects_same_loop():
    """result() from the pinned loop itself raises RuntimeError."""
    loop = asyncio.get_running_loop()
    with pytest.raises(RuntimeError, match="same loop"):
        Cf.successful(1).on(loop).result()


@pytest.mark.asyncio
async def test_result_rejects_running_loop_no_pin():
    """result() from any running loop without pin raises RuntimeError."""
    with pytest.raises(RuntimeError, match="running event loop"):
        Cf.successful(1).result()


# ==================================================================
# Memory / lifecycle
# ==================================================================


@pytest.mark.asyncio
async def test_bridge_callback_cleanup():
    """Cross-loop bridge completes without retaining references."""
    bg_loop, bg_thread = _make_background_loop()
    try:
        async def work():
            return "done"

        # Run many cross-loop operations — if callbacks leak, object count explodes
        gc.collect()
        before = len(gc.get_objects())

        for _ in range(100):
            result = await Cf(work(), loop=bg_loop)
            assert result == "done"

        gc.collect()
        after = len(gc.get_objects())
        growth = after - before
        assert growth < 200, f"Callback leak: {growth} objects grew"
    finally:
        _stop_loop(bg_loop, bg_thread)


@pytest.mark.asyncio
async def test_no_memory_leak_repeated_bridge():
    """Repeated cross-loop operations don't leak Futures."""
    bg_loop, bg_thread = _make_background_loop()
    try:
        gc.collect()
        # Warm up
        for _ in range(10):
            await Cf.successful(1).on(bg_loop).map(lambda x: x)

        gc.collect()
        before = len(gc.get_objects())

        for _ in range(1000):
            await Cf.successful(1).on(bg_loop).map(lambda x: x + 1)

        gc.collect()
        after = len(gc.get_objects())

        # Allow some growth but not proportional to iteration count
        growth = after - before
        assert growth < 500, f"Suspicious object growth: {growth}"
    finally:
        _stop_loop(bg_loop, bg_thread)


# ==================================================================
# Stress / throughput
# ==================================================================


@pytest.mark.asyncio
async def test_stress_map_chain():
    """Long map chain doesn't stack overflow or degrade.

    Each link adds ~2 coroutine frames. Python's default recursion limit
    is 1000, so 200 links (~400 frames) is safe with headroom.
    """
    cf = Cf.successful(0)
    for _ in range(200):
        cf = cf.map(lambda x: x + 1)
    result = await cf
    assert result == 200


@pytest.mark.asyncio
async def test_stress_concurrent_sequence():
    """sequence with many concurrent futures."""
    futures = [Cf.successful(i) for i in range(500)]
    result = await Cf.sequence(futures)
    assert result == list(range(500))


@pytest.mark.asyncio
async def test_stress_first_completed_many():
    """first_completed with many contenders."""
    async def delayed(val, delay):
        await asyncio.sleep(delay)
        return val

    futures = [Cf(delayed(i, (i + 1) * 0.01)) for i in range(20)]
    futures.append(Cf.successful("instant"))

    result = await Cf.first_completed(*futures)
    assert result == "instant"


@pytest.mark.asyncio
async def test_stress_cross_loop_throughput():
    """Many cross-loop operations complete without hanging."""
    bg_loop, bg_thread = _make_background_loop()
    try:
        results = []
        for i in range(100):
            r = await Cf.successful(i).on(bg_loop).map(lambda x: x * 2)
            results.append(r)
        assert results == [i * 2 for i in range(100)]
    finally:
        _stop_loop(bg_loop, bg_thread)
