"""Tests for ComposableFuture."""

import asyncio
import threading

import pytest

pytestmark = pytest.mark.anyio

from everything_is_an_actor.core.composable_future import ComposableFuture as Cf


# ------------------------------------------------------------------
# Constructors
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful():
    assert await Cf.successful(42) == 42


@pytest.mark.asyncio
async def test_failed():
    with pytest.raises(ValueError, match="boom"):
        await Cf.failed(ValueError("boom"))


# ------------------------------------------------------------------
# Transform
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_map():
    assert await Cf.successful(3).map(lambda x: x * 2) == 6


@pytest.mark.asyncio
async def test_map_skips_on_failure():
    called = False

    def should_not_run(x):
        nonlocal called
        called = True
        return x

    with pytest.raises(ValueError):
        await Cf.failed(ValueError()).map(should_not_run)
    assert not called


@pytest.mark.asyncio
async def test_flat_map():
    result = await (
        Cf.successful(3).flat_map(lambda x: Cf.successful(x + 10))
    )
    assert result == 13


@pytest.mark.asyncio
async def test_flat_map_skips_on_failure():
    with pytest.raises(ValueError):
        await Cf.failed(ValueError()).flat_map(lambda x: Cf.successful(x))


@pytest.mark.asyncio
async def test_filter_pass():
    assert await Cf.successful(5).filter(lambda x: x > 3) == 5


@pytest.mark.asyncio
async def test_filter_fail():
    with pytest.raises(ValueError, match="predicate failed"):
        await Cf.successful(1).filter(lambda x: x > 3)


@pytest.mark.asyncio
async def test_transform_success():
    result = await Cf.successful(3).transform(
        success=lambda x: x * 10,
        failure=lambda e: -1,
    )
    assert result == 30


@pytest.mark.asyncio
async def test_transform_failure():
    result = await Cf.failed(ValueError("oops")).transform(
        success=lambda x: x * 10,
        failure=lambda e: -1,
    )
    assert result == -1


# ------------------------------------------------------------------
# Error handling
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recover():
    result = await Cf.failed(ValueError("boom")).recover(lambda e: "recovered")
    assert result == "recovered"


@pytest.mark.asyncio
async def test_recover_not_triggered_on_success():
    result = await Cf.successful(42).recover(lambda e: -1)
    assert result == 42


@pytest.mark.asyncio
async def test_recover_with():
    result = await Cf.failed(ValueError()).recover_with(
        lambda e: Cf.successful("async_recovered")
    )
    assert result == "async_recovered"


@pytest.mark.asyncio
async def test_fallback_to_on_failure():
    result = await Cf.failed(ValueError()).fallback_to(lambda: Cf.successful("fallback"))
    assert result == "fallback"


@pytest.mark.asyncio
async def test_fallback_to_not_triggered_on_success():
    called = False

    def make_fallback():
        nonlocal called
        called = True
        return Cf.successful("fallback")

    result = await Cf.successful("primary").fallback_to(make_fallback)
    assert result == "primary"
    assert not called


# ------------------------------------------------------------------
# Combine
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zip():
    result = await Cf.successful("a").zip(Cf.successful(1))
    assert result == ("a", 1)


@pytest.mark.asyncio
async def test_zip_propagates_failure():
    with pytest.raises(ValueError):
        await Cf.successful("a").zip(Cf.failed(ValueError()))


@pytest.mark.asyncio
async def test_sequence():
    result = await Cf.sequence([Cf.successful(i) for i in range(5)])
    assert result == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_sequence_empty():
    result = await Cf.sequence([])
    assert result == []


@pytest.mark.asyncio
async def test_first_completed():
    async def slow():
        await asyncio.sleep(10)
        return "slow"

    result = await Cf.first_completed(Cf(slow()), Cf.successful("fast"))
    assert result == "fast"


@pytest.mark.asyncio
async def test_first_completed_no_cancel():
    """cancel_pending=False leaves losers running."""
    async def slow():
        await asyncio.sleep(10)
        return "slow"

    result = await Cf.first_completed(
        Cf(slow()), Cf.successful("fast"),
        cancel_pending=False,
    )
    assert result == "fast"


# ------------------------------------------------------------------
# Side effects
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_and_then():
    captured = []
    result = await Cf.successful(42).and_then(lambda x: captured.append(x))
    assert result == 42
    assert captured == [42]


@pytest.mark.asyncio
async def test_on_complete_success():
    ok_val = []
    result = await Cf.successful(7).on_complete(on_success=lambda x: ok_val.append(x))
    assert result == 7
    assert ok_val == [7]


@pytest.mark.asyncio
async def test_on_complete_failure():
    err_val = []
    with pytest.raises(ValueError):
        await Cf.failed(ValueError("x")).on_complete(on_failure=lambda e: err_val.append(str(e)))
    assert err_val == ["x"]


# ------------------------------------------------------------------
# Timeout
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_with_timeout_success():
    result = await Cf.successful(1).with_timeout(1.0)
    assert result == 1


@pytest.mark.asyncio
async def test_with_timeout_expires():
    async def slow():
        await asyncio.sleep(10)

    with pytest.raises(asyncio.TimeoutError):
        await Cf(slow()).with_timeout(0.01)


# ------------------------------------------------------------------
# Chaining
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_chain():
    result = await (
        Cf.successful(3)
            .map(lambda x: x * 2)
            .flat_map(lambda x: Cf.successful(x + 1))
            .filter(lambda x: x > 5)
            .and_then(lambda x: None)
            .with_timeout(1.0)
            .recover(lambda e: 0)
    )
    assert result == 7


@pytest.mark.asyncio
async def test_chain_with_failure_recovery():
    result = await (
        Cf.successful(3)
            .map(lambda x: x / 0)
            .recover(lambda e: -1)
            .map(lambda x: x * 10)
    )
    assert result == -10


# ------------------------------------------------------------------
# Cross-thread
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


@pytest.mark.asyncio
async def test_cross_loop_await():
    """await a Cf pinned to a background loop from the test's loop."""
    bg_loop, bg_thread = _make_background_loop()
    try:
        async def work():
            await asyncio.sleep(0)
            return "from_bg"

        result = await Cf(work(), loop=bg_loop).map(str.upper)
        assert result == "FROM_BG"
    finally:
        bg_loop.call_soon_threadsafe(bg_loop.stop)
        bg_thread.join(timeout=2.0)


@pytest.mark.asyncio
async def test_cross_loop_chain():
    """Full chain executes on the target loop."""
    bg_loop, bg_thread = _make_background_loop()
    try:
        loop_ids: list[int] = []

        async def capture_loop():
            loop_ids.append(id(asyncio.get_running_loop()))
            return 42

        result = await (
            Cf(capture_loop(), loop=bg_loop)
                .map(lambda x: x * 2)
                .recover(lambda e: -1)
        )
        assert result == 84
        assert loop_ids[0] == id(bg_loop)
    finally:
        bg_loop.call_soon_threadsafe(bg_loop.stop)
        bg_thread.join(timeout=2.0)


def test_blocking_result():
    """result() works from a plain (non-async) thread."""
    bg_loop, bg_thread = _make_background_loop()
    try:
        async def work():
            await asyncio.sleep(0)
            return 99

        result = Cf(work(), loop=bg_loop).map(lambda x: x + 1).result(timeout=5.0)
        assert result == 100
    finally:
        bg_loop.call_soon_threadsafe(bg_loop.stop)
        bg_thread.join(timeout=2.0)


def test_blocking_result_no_loop():
    """result() without a target loop creates a temporary one."""
    result = Cf.successful(7).map(lambda x: x * 3).result()
    assert result == 21


@pytest.mark.asyncio
async def test_on_sets_loop():
    """on(loop) pins a loop-less Cf to a specific loop."""
    bg_loop, bg_thread = _make_background_loop()
    try:
        result = await Cf.successful(10).map(lambda x: x + 5).on(bg_loop)
        assert result == 15
    finally:
        bg_loop.call_soon_threadsafe(bg_loop.stop)
        bg_thread.join(timeout=2.0)


@pytest.mark.asyncio
async def test_same_loop_no_overhead():
    """When target loop == current loop, no bridging happens."""
    current_loop = asyncio.get_running_loop()
    result = await Cf.successful(42).on(current_loop)
    assert result == 42


# ------------------------------------------------------------------
# Cross-thread: non-coroutine awaitables
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unpinned_cross_loop_future():
    """Cf wrapping a foreign-loop Future without .on() still works."""
    bg_loop, bg_thread = _make_background_loop()
    try:
        # Create a future resolved on bg_loop
        conc_fut = asyncio.run_coroutine_threadsafe(asyncio.sleep(0, result=55), bg_loop)
        wrapped = asyncio.wrap_future(conc_fut, loop=bg_loop)

        # No .on() — unpinned, but wrapping a bg_loop Future
        result = await Cf(wrapped).map(lambda x: x + 5)
        assert result == 60
    finally:
        bg_loop.call_soon_threadsafe(bg_loop.stop)
        bg_thread.join(timeout=2.0)


@pytest.mark.asyncio
async def test_cross_loop_with_asyncio_future():
    """Cross-loop works when wrapping an asyncio.Future (not a coroutine)."""
    bg_loop, bg_thread = _make_background_loop()
    try:
        # Create a future on bg_loop
        fut = asyncio.run_coroutine_threadsafe(asyncio.sleep(0, result=77), bg_loop)
        wrapped_fut = asyncio.wrap_future(fut)

        result = await Cf(wrapped_fut, loop=bg_loop).map(lambda x: x + 3)
        assert result == 80
    finally:
        bg_loop.call_soon_threadsafe(bg_loop.stop)
        bg_thread.join(timeout=2.0)


@pytest.mark.asyncio
async def test_cross_loop_with_task():
    """Cross-loop works when wrapping an asyncio.Task."""
    bg_loop, bg_thread = _make_background_loop()
    try:
        async def work():
            return "task_result"

        result = await Cf(work(), loop=bg_loop).map(str.upper)
        assert result == "TASK_RESULT"
    finally:
        bg_loop.call_soon_threadsafe(bg_loop.stop)
        bg_thread.join(timeout=2.0)


# ------------------------------------------------------------------
# result() safety
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_result_fails_in_async_context_without_loop():
    """result() raises RuntimeError when called from a running event loop."""
    with pytest.raises(RuntimeError, match="running event loop"):
        Cf.successful(1).result()


@pytest.mark.asyncio
async def test_result_fails_on_same_loop():
    """result() raises RuntimeError when pinned to the current loop."""
    current_loop = asyncio.get_running_loop()
    with pytest.raises(RuntimeError, match="same loop"):
        Cf.successful(1).on(current_loop).result()


# ------------------------------------------------------------------
# sequence / first_completed with mixed loops
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_completed_empty():
    """first_completed with zero futures raises ValueError."""
    with pytest.raises(ValueError, match="at least one"):
        await Cf.first_completed()


@pytest.mark.asyncio
async def test_first_completed_cancel_pending_settles():
    """cancel_pending=True waits for losers to settle."""
    settled = []

    async def slow():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            settled.append(True)
            raise

    result = await Cf.first_completed(
        Cf(slow()), Cf.successful("fast"),
        cancel_pending=True,
    )
    assert result == "fast"
    assert settled == [True]


@pytest.mark.asyncio
async def test_sequence_mixed_loops():
    """sequence() works with futures pinned to different loops."""
    bg_loop, bg_thread = _make_background_loop()
    try:
        local = Cf.successful(1)
        remote = Cf.successful(2).on(bg_loop)

        result = await Cf.sequence([local, remote])
        assert result == [1, 2]
    finally:
        bg_loop.call_soon_threadsafe(bg_loop.stop)
        bg_thread.join(timeout=2.0)
