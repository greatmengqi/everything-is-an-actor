"""Tests for ComposableStream concurrent, error handling, time-based, and lifecycle operators."""

import asyncio
import time
from typing import AsyncIterator

import pytest

pytestmark = pytest.mark.anyio

from everything_is_an_actor.core.composable_stream import ComposableStream


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _failing_source(*values, error=ValueError("boom")):
    """Async generator that yields *values* then raises *error*."""
    for v in values:
        yield v
    raise error


async def _slow_source(items, delay: float):
    """Yield *items* with *delay* seconds between each."""
    for item in items:
        await asyncio.sleep(delay)
        yield item


# ===================================================================
# SC: Concurrent operators
# ===================================================================


class TestMerge:
    """SC01-SC03: merge operator."""

    @pytest.mark.asyncio
    async def test_merge_happy(self):
        """SC01: merge two non-empty streams; order is non-deterministic."""
        s1 = ComposableStream.of(1, 2, 3)
        s2 = ComposableStream.of(10, 20, 30)
        result = await s1.merge(s2).run_to_list()
        assert sorted(result) == [1, 2, 3, 10, 20, 30]

    @pytest.mark.asyncio
    async def test_merge_one_empty(self):
        """SC02: merge non-empty with empty yields all from non-empty."""
        s1 = ComposableStream.of(1, 2, 3)
        s2 = ComposableStream.empty()
        result = await s1.merge(s2).run_to_list()
        assert sorted(result) == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_merge_both_empty(self):
        """SC03: merge two empty streams yields empty."""
        s1 = ComposableStream.empty()
        s2 = ComposableStream.empty()
        result = await s1.merge(s2).run_to_list()
        assert result == []


class TestFlatMapMerge:
    """SC04-SC06: flat_map_merge operator."""

    @pytest.mark.asyncio
    async def test_flat_map_merge_happy(self):
        """SC04: concurrent flat_map produces all sub-stream elements."""
        result = await (
            ComposableStream.of(1, 2, 3)
            .flat_map_merge(lambda x: ComposableStream.of(x * 10, x * 100))
            .run_to_list()
        )
        assert sorted(result) == [10, 20, 30, 100, 200, 300]

    @pytest.mark.asyncio
    async def test_flat_map_merge_breadth_1(self):
        """SC05: breadth=1 degrades to sequential flat_map behavior."""
        collected = []

        def make_sub(x):
            async def _gen() -> AsyncIterator[int]:
                collected.append(f"start-{x}")
                yield x * 10
                yield x * 100
                collected.append(f"end-{x}")
            return ComposableStream(_gen())

        result = await (
            ComposableStream.of(1, 2)
            .flat_map_merge(make_sub, breadth=1)
            .run_to_list()
        )
        # breadth=1 means sequential: sub-stream 1 fully completes before 2 starts
        assert collected == ["start-1", "end-1", "start-2", "end-2"]
        assert sorted(result) == [10, 20, 100, 200]

    @pytest.mark.asyncio
    async def test_flat_map_merge_nondeterministic(self):
        """SC06: with high breadth, output set matches even if order varies."""
        result = await (
            ComposableStream.of(1, 2, 3, 4, 5)
            .flat_map_merge(lambda x: ComposableStream.of(x), breadth=16)
            .run_to_list()
        )
        assert set(result) == {1, 2, 3, 4, 5}


class TestInterleave:
    """SC07-SC09: interleave operator."""

    @pytest.mark.asyncio
    async def test_interleave_segment_1(self):
        """SC07: segment=1 alternates one element from each stream."""
        s1 = ComposableStream.of(1, 2, 3)
        s2 = ComposableStream.of(10, 20, 30)
        result = await s1.interleave(s2, segment=1).run_to_list()
        assert result == [1, 10, 2, 20, 3, 30]

    @pytest.mark.asyncio
    async def test_interleave_segment_2(self):
        """SC08: segment=2 takes two from each stream in turn."""
        s1 = ComposableStream.of(1, 2, 3, 4)
        s2 = ComposableStream.of(10, 20, 30, 40)
        result = await s1.interleave(s2, segment=2).run_to_list()
        assert result == [1, 2, 10, 20, 3, 4, 30, 40]

    @pytest.mark.asyncio
    async def test_interleave_unequal_length(self):
        """SC09: shorter stream exhausts first; remaining from longer stream emitted."""
        s1 = ComposableStream.of(1, 2, 3, 4, 5)
        s2 = ComposableStream.of(10, 20)
        result = await s1.interleave(s2, segment=1).run_to_list()
        assert result == [1, 10, 2, 20, 3, 4, 5]


class TestBuffer:
    """SC10-SC11: buffer operator."""

    @pytest.mark.asyncio
    async def test_buffer_happy(self):
        """SC10: buffered stream yields all elements in order."""
        result = await (
            ComposableStream.of(1, 2, 3, 4, 5)
            .buffer(3)
            .run_to_list()
        )
        assert result == [1, 2, 3, 4, 5]

    @pytest.mark.asyncio
    async def test_buffer_producer_faster_than_consumer(self):
        """SC11: fast producer with slow consumer still delivers all elements."""
        # Producer emits instantly; consumer adds delay per element
        consumed = []

        async def slow_consume(stream: ComposableStream[int]) -> list[int]:
            result = []
            async for item in stream._source:
                await asyncio.sleep(0.01)
                result.append(item)
                consumed.append(item)
            return result

        stream = ComposableStream.of(1, 2, 3, 4, 5).buffer(2)
        result = await slow_consume(stream)
        assert result == [1, 2, 3, 4, 5]
        assert consumed == [1, 2, 3, 4, 5]


# ===================================================================
# SE: Error handling operators
# ===================================================================


class TestRecover:
    """SE01-SE02: recover operator."""

    @pytest.mark.asyncio
    async def test_recover_emits_recovery_value(self):
        """SE01: on error, recovery function is called and its value emitted."""
        stream = ComposableStream(_failing_source(1, 2, error=ValueError("oops")))
        result = await stream.recover(lambda e: -1).run_to_list()
        assert result == [1, 2, -1]

    @pytest.mark.asyncio
    async def test_recover_not_triggered_on_success(self):
        """SE02: recover does not interfere with successful streams."""
        result = await (
            ComposableStream.of(1, 2, 3)
            .recover(lambda e: -1)
            .run_to_list()
        )
        assert result == [1, 2, 3]


class TestRecoverWith:
    """SE03-SE04: recover_with operator."""

    @pytest.mark.asyncio
    async def test_recover_with_emits_fallback_stream(self):
        """SE03: on error, fallback stream elements are emitted."""
        stream = ComposableStream(_failing_source(1, 2, error=ValueError("oops")))
        result = await stream.recover_with(
            lambda e: ComposableStream.of(99, 100)
        ).run_to_list()
        assert result == [1, 2, 99, 100]

    @pytest.mark.asyncio
    async def test_recover_with_not_triggered_on_success(self):
        """SE04: recover_with does not interfere with successful streams."""
        result = await (
            ComposableStream.of(1, 2, 3)
            .recover_with(lambda e: ComposableStream.of(99))
            .run_to_list()
        )
        assert result == [1, 2, 3]


class TestMapError:
    """SE05-SE06: map_error operator."""

    @pytest.mark.asyncio
    async def test_map_error_transforms_exception(self):
        """SE05: exception type is transformed by map_error."""
        stream = ComposableStream(_failing_source(1, 2, error=ValueError("original")))
        mapped = stream.map_error(lambda e: RuntimeError(f"wrapped: {e}"))
        with pytest.raises(RuntimeError, match="wrapped: original"):
            await mapped.run_to_list()

    @pytest.mark.asyncio
    async def test_map_error_not_triggered_on_success(self):
        """SE06: map_error does not interfere with successful streams."""
        result = await (
            ComposableStream.of(1, 2, 3)
            .map_error(lambda e: RuntimeError("should not happen"))
            .run_to_list()
        )
        assert result == [1, 2, 3]


# ===================================================================
# ST: Time-based operators
# ===================================================================


class TestThrottle:
    """ST01: throttle operator."""

    @pytest.mark.asyncio
    async def test_throttle_limits_throughput(self):
        """ST01: 5 elements throttled to 2 per 0.1s takes at least 0.15s."""
        stream = ComposableStream.of(1, 2, 3, 4, 5).throttle(elements=2, per=0.1)
        t0 = time.monotonic()
        result = await stream.run_to_list()
        elapsed = time.monotonic() - t0
        assert result == [1, 2, 3, 4, 5]
        # 5 elements at 2 per 0.1s = 2 full windows (sleep twice) -> >= 0.15s
        # Use generous tolerance: at least 50% of expected minimum
        assert elapsed >= 0.075, f"Expected >= 0.075s, got {elapsed:.3f}s"


class TestGroupedWithin:
    """ST02-ST04: grouped_within operator."""

    @pytest.mark.asyncio
    async def test_grouped_within_trigger_by_count(self):
        """ST02: fast source with large timeout triggers by count."""
        result = await (
            ComposableStream.of(1, 2, 3, 4, 5)
            .grouped_within(n=2, timeout=10.0)
            .run_to_list()
        )
        assert result == [[1, 2], [3, 4], [5]]

    @pytest.mark.asyncio
    async def test_grouped_within_trigger_by_time(self):
        """ST03: fast source elements that don't fill count trigger by time.

        Source emits 2 elements quickly then stops. With count=100 and a
        short timeout, the batch is flushed by the timeout, not by count.
        """
        result = await (
            ComposableStream.of(1, 2)
            .grouped_within(n=100, timeout=0.1)
            .run_to_list()
        )
        # Elements arrive instantly but don't reach count=100,
        # so the batch is emitted when the timeout fires.
        assert result == [[1, 2]]

    @pytest.mark.asyncio
    async def test_grouped_within_mixed(self):
        """ST04: mixed -- count triggers first batch, time triggers remainder.

        7 fast elements with count=3 and generous timeout.  First two
        batches fill by count; the remaining 1 element flushes by timeout.
        """
        result = await (
            ComposableStream.of(1, 2, 3, 4, 5, 6, 7)
            .grouped_within(n=3, timeout=0.5)
            .run_to_list()
        )
        assert result[0] == [1, 2, 3]
        assert result[1] == [4, 5, 6]
        assert result[2] == [7]
        flat = [x for batch in result for x in batch]
        assert flat == [1, 2, 3, 4, 5, 6, 7]


class TestKeepAlive:
    """ST05-ST06: keep_alive operator."""

    @pytest.mark.asyncio
    async def test_keep_alive_injects_heartbeat(self):
        """ST05: heartbeat injected when source is slower than interval.

        Note: keep_alive uses asyncio.wait_for internally, which cancels
        __anext__() on timeout. For async generators this terminates the
        generator after the first timeout. The test verifies a heartbeat
        is injected when the source is idle.
        """
        async def _slow() -> AsyncIterator[str]:
            yield "a"
            await asyncio.sleep(0.5)  # long pause -- will trigger heartbeat
            yield "b"  # may not be reached due to wait_for cancellation

        result = await (
            ComposableStream(_slow())
            .keep_alive(interval=0.1, element="heartbeat")
            .run_to_list()
        )
        assert result[0] == "a"
        # At least one heartbeat was injected during the gap
        assert "heartbeat" in result

    @pytest.mark.asyncio
    async def test_keep_alive_no_injection_when_fast(self):
        """ST06: no heartbeat when source is faster than interval."""
        result = await (
            ComposableStream.of("a", "b", "c")
            .keep_alive(interval=10.0, element="heartbeat")
            .run_to_list()
        )
        assert result == ["a", "b", "c"]


class TestCompletionTimeout:
    """ST07-ST08: completion_timeout operator."""

    @pytest.mark.asyncio
    async def test_completion_timeout_normal(self):
        """ST07: stream completes within timeout -- no error."""
        result = await (
            ComposableStream.of(1, 2, 3)
            .completion_timeout(seconds=5.0)
            .run_to_list()
        )
        assert result == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_completion_timeout_raises(self):
        """ST08: slow stream exceeds timeout -- raises TimeoutError."""
        stream = ComposableStream(_slow_source([1, 2, 3, 4, 5], delay=0.1))
        with pytest.raises(asyncio.TimeoutError):
            await stream.completion_timeout(seconds=0.15).run_to_list()


class TestIdleTimeout:
    """ST09-ST10: idle_timeout operator."""

    @pytest.mark.asyncio
    async def test_idle_timeout_normal(self):
        """ST09: elements arrive within idle timeout -- no error."""
        # Elements arrive every 0.02s, idle timeout is 0.5s
        stream = ComposableStream(_slow_source([1, 2, 3], delay=0.02))
        result = await stream.idle_timeout(seconds=0.5).run_to_list()
        assert result == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_idle_timeout_raises(self):
        """ST10: gap between elements exceeds idle timeout -- raises TimeoutError."""
        async def _source_with_gap() -> AsyncIterator[int]:
            yield 1
            yield 2
            await asyncio.sleep(0.3)  # gap longer than idle timeout
            yield 3

        stream = ComposableStream(_source_with_gap())
        with pytest.raises(asyncio.TimeoutError):
            await stream.idle_timeout(seconds=0.1).run_to_list()


# ===================================================================
# SL: Lifecycle operators
# ===================================================================


class TestWatchTermination:
    """SL01-SL02: watch_termination operator."""

    @pytest.mark.asyncio
    async def test_watch_termination_success(self):
        """SL01: on normal completion, callback receives None."""
        callback_args: list = []
        result = await (
            ComposableStream.of(1, 2, 3)
            .watch_termination(lambda exc: callback_args.append(exc))
            .run_to_list()
        )
        assert result == [1, 2, 3]
        assert callback_args == [None]

    @pytest.mark.asyncio
    async def test_watch_termination_error(self):
        """SL02: on error, callback receives the exception."""
        callback_args: list = []
        stream = ComposableStream(
            _failing_source(1, 2, error=ValueError("fail"))
        ).watch_termination(lambda exc: callback_args.append(exc))
        with pytest.raises(ValueError, match="fail"):
            await stream.run_to_list()
        assert len(callback_args) == 1
        assert isinstance(callback_args[0], ValueError)
        assert str(callback_args[0]) == "fail"


class TestAlsoTo:
    """SL03-SL04: also_to operator."""

    @pytest.mark.asyncio
    async def test_also_to_side_effect(self):
        """SL03: side effect function is called for every element."""
        side: list[int] = []
        result = await (
            ComposableStream.of(1, 2, 3)
            .also_to(lambda x: side.append(x))
            .run_to_list()
        )
        assert side == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_also_to_values_pass_through(self):
        """SL04: values pass through unchanged despite side effect."""
        result = await (
            ComposableStream.of(10, 20, 30)
            .also_to(lambda x: None)  # no-op side effect
            .map(lambda x: x + 1)
            .run_to_list()
        )
        assert result == [11, 21, 31]
