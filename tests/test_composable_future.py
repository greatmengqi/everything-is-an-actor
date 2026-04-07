"""Tests for ComposableFuture."""

import asyncio

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
# Blocking result()
# ------------------------------------------------------------------


def test_blocking_result():
    """result() works from a plain (non-async) thread."""
    result = Cf.successful(99).map(lambda x: x + 1).result(timeout=5.0)
    assert result == 100


def test_blocking_result_no_loop():
    """result() without a target loop creates a temporary one."""
    result = Cf.successful(7).map(lambda x: x * 3).result()
    assert result == 21


# ------------------------------------------------------------------
# result() safety
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_result_returns_cached_in_async_context():
    """result() returns cached value even from async context (no loop needed)."""
    assert Cf.successful(1).result() == 1


@pytest.mark.asyncio
async def test_result_fails_unresolved_in_async_context():
    """result() raises RuntimeError for unresolved CF in async context."""
    async def slow():
        await asyncio.sleep(10)
        return 42
    with pytest.raises(RuntimeError, match="running event loop"):
        Cf(slow()).result()


# ------------------------------------------------------------------
# sequence / first_completed
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


# ------------------------------------------------------------------
# Resolve-once caching
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_value_on_resolve():
    """First await caches result; second await returns cached value."""
    call_count = 0

    async def expensive():
        nonlocal call_count
        call_count += 1
        return 42

    cf = Cf(expensive())
    assert await cf == 42
    assert await cf == 42
    assert call_count == 1  # coroutine ran only once


@pytest.mark.asyncio
async def test_cache_error_on_resolve():
    """First await caches exception; second await re-raises from cache."""
    call_count = 0

    async def failing():
        nonlocal call_count
        call_count += 1
        raise ValueError("boom")

    cf = Cf(failing())
    with pytest.raises(ValueError, match="boom"):
        await cf
    with pytest.raises(ValueError, match="boom"):
        await cf
    assert call_count == 1


@pytest.mark.asyncio
async def test_fork_map_chains_from_same_cf():
    """Multiple map chains from the same CF share the cached value."""
    cf = Cf.successful(10)
    doubled = cf.map(lambda x: x * 2)
    tripled = cf.map(lambda x: x * 3)
    assert await doubled == 20
    assert await tripled == 30


@pytest.mark.asyncio
async def test_concurrent_awaiters_safe():
    """Two concurrent tasks awaiting the same CF — no crash, both get the value."""
    gate = asyncio.Event()

    async def slow():
        await gate.wait()
        return "done"

    cf = Cf(slow())

    async def awaiter():
        return await cf

    t1 = asyncio.create_task(awaiter())
    t2 = asyncio.create_task(awaiter())
    await asyncio.sleep(0)  # let both tasks start
    gate.set()

    r1, r2 = await asyncio.gather(t1, t2)
    assert r1 == "done"
    assert r2 == "done"
