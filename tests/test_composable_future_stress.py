"""Adversarial and stress tests for ComposableFuture.

Covers: cancellation propagation, first_completed races, result() edge cases,
memory leak detection, and stress throughput.
"""

import asyncio
import gc

import pytest

pytestmark = pytest.mark.anyio

from everything_is_an_actor.core.composable_future import ComposableFuture as Cf


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
async def test_cancel_propagates_through_flat_map():
    """Cancellation propagates through flat_map chains."""
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

    async def chain(x):
        return await Cf(slow())

    async def _run():
        return await Cf.successful(1).flat_map(chain)
    task = asyncio.create_task(_run())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled


@pytest.mark.asyncio
async def test_with_timeout_cancels_inner():
    """with_timeout cancels the inner chain on expiry."""
    cancelled = False

    async def slow():
        nonlocal cancelled
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled = True
            raise

    with pytest.raises(TimeoutError):
        await Cf(slow()).with_timeout(0.01)

    await asyncio.sleep(0.01)
    assert cancelled


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
    await asyncio.sleep(0.1)
    assert effects == []


@pytest.mark.asyncio
async def test_first_completed_no_cancel():
    """cancel_pending=False lets losers finish."""
    effects = []

    async def failing():
        await asyncio.sleep(0.01)
        effects.append("ran")
        raise ValueError("oops")

    result = await Cf.first_completed(
        Cf(failing()),
        Cf.successful("winner"),
        cancel_pending=False,
    )
    assert result == "winner"
    await asyncio.sleep(0.1)


# ==================================================================
# result() edge cases
# ==================================================================


def test_result_from_plain_thread():
    """result() works from a non-async thread."""
    assert Cf.successful(42).map(lambda x: x + 1).result() == 43


@pytest.mark.asyncio
async def test_result_rejects_unresolved_in_async():
    """result() from a running loop on unresolved CF raises RuntimeError."""
    async def slow():
        await asyncio.sleep(10)
        return 42
    with pytest.raises(RuntimeError, match="running event loop"):
        Cf(slow()).result()


# ==================================================================
# Memory / lifecycle
# ==================================================================


@pytest.mark.asyncio
async def test_no_memory_leak_repeated_resolve():
    """Repeated resolve operations don't leak objects."""
    gc.collect()
    before = len(gc.get_objects())

    for _ in range(1000):
        await Cf.successful(1).map(lambda x: x + 1)

    gc.collect()
    after = len(gc.get_objects())
    growth = after - before
    assert growth < 500, f"Suspicious object growth: {growth}"


@pytest.mark.asyncio
async def test_outcome_cached_after_resolve():
    """Outcome is cached after first resolve — subsequent awaits don't re-run."""
    run_count = 0

    async def work():
        nonlocal run_count
        run_count += 1
        return 42

    cf = Cf(work())
    assert cf._outcome is None  # not yet awaited — task scheduled but not observed
    assert await cf == 42
    assert cf._outcome is not None  # resolved and cached
    assert await cf == 42  # second await hits cache
    assert run_count == 1  # coroutine ran exactly once


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


# ==================================================================
# Cross-thread promise resolution (Dispatcher core path)
# ==================================================================


@pytest.mark.asyncio
async def test_promise_resolve_from_thread():
    """promise() resolved from a background thread delivers to the caller loop."""
    import threading

    cf, resolve, _ = Cf.promise()

    def _bg():
        resolve(42)

    t = threading.Thread(target=_bg)
    t.start()
    result = await cf
    t.join()
    assert result == 42


@pytest.mark.asyncio
async def test_promise_reject_from_thread():
    """promise() rejected from a background thread propagates the exception."""
    import threading

    cf, _, reject = Cf.promise()

    def _bg():
        reject(ValueError("thread error"))

    t = threading.Thread(target=_bg)
    t.start()
    with pytest.raises(ValueError, match="thread error"):
        await cf
    t.join()


@pytest.mark.asyncio
async def test_promise_resolve_same_loop():
    """promise() resolved from the same loop works without cross-thread overhead."""
    cf, resolve, _ = Cf.promise()
    resolve(99)
    result = await cf
    assert result == 99


@pytest.mark.asyncio
async def test_promise_double_resolve_ignored():
    """Second resolve is silently ignored (idempotent)."""
    cf, resolve, _ = Cf.promise()
    resolve(1)
    resolve(2)  # should be ignored
    result = await cf
    assert result == 1


@pytest.mark.asyncio
async def test_promise_resolve_after_reject_ignored():
    """resolve after reject is ignored — first writer wins."""
    cf, resolve, reject = Cf.promise()
    reject(ValueError("first"))
    resolve(42)  # ignored
    with pytest.raises(ValueError, match="first"):
        await cf


@pytest.mark.asyncio
async def test_promise_reject_after_resolve_ignored():
    """reject after resolve is ignored — first writer wins."""
    cf, resolve, reject = Cf.promise()
    resolve(42)
    reject(ValueError("late"))  # ignored
    result = await cf
    assert result == 42


@pytest.mark.asyncio
async def test_promise_timeout_before_resolve():
    """Awaiter times out, then thread resolves — no crash."""
    import threading

    cf, resolve, _ = Cf.promise()

    with pytest.raises(TimeoutError):
        await cf.with_timeout(0.01)

    # Late resolve from thread — should not crash
    def _bg():
        resolve(42)
    t = threading.Thread(target=_bg)
    t.start()
    t.join()


@pytest.mark.asyncio
async def test_promise_cancel_before_resolve():
    """Awaiter is cancelled, then thread resolves — no crash."""
    import threading

    cf, resolve, _ = Cf.promise()
    gate = threading.Event()

    async def _await_it():
        return await cf

    task = asyncio.create_task(_await_it())
    await asyncio.sleep(0)  # let task start
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Late resolve from thread — should not crash
    def _bg():
        resolve(99)
    t = threading.Thread(target=_bg)
    t.start()
    t.join()
