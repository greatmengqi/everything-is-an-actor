"""Categorical law tests for ComposableFuture.

Covers functor laws, monad laws, applicative laws,
and missing operator tests (zip concurrency, ap, recover_with).
"""

import asyncio
import time

import pytest

pytestmark = pytest.mark.anyio

from everything_is_an_actor.core.composable_future import ComposableFuture

Cf = ComposableFuture


# ------------------------------------------------------------------
# F07: zip concurrent execution verification
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_f07_zip_runs_concurrently():
    """Both sides of zip must run concurrently, not sequentially."""
    delay = 0.15

    async def slow_a():
        await asyncio.sleep(delay)
        return "a"

    async def slow_b():
        await asyncio.sleep(delay)
        return "b"

    start = time.monotonic()
    result = await Cf(slow_a()).zip(Cf(slow_b()))
    elapsed = time.monotonic() - start

    assert result == ("a", "b")
    # Sequential would take >= 2*delay; concurrent should be < 1.5*delay
    assert elapsed < delay * 1.5, (
        f"zip appears sequential: elapsed {elapsed:.3f}s >= {delay * 1.5:.3f}s"
    )


# ------------------------------------------------------------------
# F08: ap happy path
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_f08_ap_happy_path():
    """ap applies a lifted function to a lifted value."""
    result = await Cf.of(10).ap(Cf.of(lambda x: x + 5))
    assert result == 15


# ------------------------------------------------------------------
# F09: ap failure propagation
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_f09_ap_fn_future_fails():
    """If the function future fails, ap propagates the error."""
    with pytest.raises(ValueError, match="fn_boom"):
        await Cf.of(10).ap(Cf.failed(ValueError("fn_boom")))


# ------------------------------------------------------------------
# Functor laws
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fl01_functor_identity():
    """FL01: map(identity) == identity  --  map(x -> x) preserves the value."""
    value = 42
    cf = Cf.of(value)
    assert await cf.map(lambda x: x) == await Cf.of(value)


@pytest.mark.asyncio
async def test_fl02_functor_composition():
    """FL02: map(f).map(g) == map(g . f)  --  mapping composes."""
    f = lambda x: x + 3
    g = lambda x: x * 2
    cf_value = 7

    lhs = await Cf.of(cf_value).map(f).map(g)
    rhs = await Cf.of(cf_value).map(lambda x: g(f(x)))
    assert lhs == rhs


# ------------------------------------------------------------------
# Monad laws
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fl03_monad_left_identity():
    """FL03: of(a).flat_map(f) == f(a)  --  left identity / return-bind."""
    a = 5

    def f(x):
        return Cf.of(x * 3)

    lhs = await Cf.of(a).flat_map(f)
    rhs = await f(a)
    assert lhs == rhs


@pytest.mark.asyncio
async def test_fl04_monad_right_identity():
    """FL04: cf.flat_map(of) == cf  --  right identity / bind-return."""
    value = "hello"
    cf = Cf.of(value)

    lhs = await cf.flat_map(Cf.of)
    rhs = await Cf.of(value)
    assert lhs == rhs


@pytest.mark.asyncio
async def test_fl05_monad_associativity():
    """FL05: cf.flat_map(f).flat_map(g) == cf.flat_map(lambda x: f(x).flat_map(g))"""
    cf_value = 2

    def f(x):
        return Cf.of(x + 10)

    def g(x):
        return Cf.of(x * 3)

    lhs = await Cf.of(cf_value).flat_map(f).flat_map(g)
    rhs = await Cf.of(cf_value).flat_map(lambda x: f(x).flat_map(g))
    assert lhs == rhs


# ------------------------------------------------------------------
# Applicative laws
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fl06_applicative_identity():
    """FL06: cf.ap(of(identity)) == cf  --  applying identity preserves value."""
    value = 99
    cf = Cf.of(value)

    lhs = await cf.ap(Cf.of(lambda x: x))
    rhs = await Cf.of(value)
    assert lhs == rhs


@pytest.mark.asyncio
async def test_fl07_applicative_homomorphism():
    """FL07: of(x).ap(of(f)) == of(f(x))  --  pure function on pure value."""
    x = 4
    f = lambda v: v ** 2

    lhs = await Cf.of(x).ap(Cf.of(f))
    rhs = await Cf.of(f(x))
    assert lhs == rhs


# ------------------------------------------------------------------
# F13: recover_with not triggered on success
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_f13_recover_with_not_triggered_on_success():
    """recover_with handler must not be called when the future succeeds."""
    called = False

    async def handler(e):
        nonlocal called
        called = True
        return "fallback"

    result = await Cf.of(100).recover_with(handler)
    assert result == 100
    assert not called, "recover_with handler was called on a successful future"
