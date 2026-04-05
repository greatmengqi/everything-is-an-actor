"""Tests for ComposableStream operators.

Covers primitives, derived operators, terminals, and constructors.
Uses pytest + pytest-asyncio with strict asyncio_mode.
"""

import asyncio
import time

import pytest

from everything_is_an_actor.core.composable_stream import ComposableStream

pytestmark = pytest.mark.anyio


# =====================================================================
#  PRIMITIVES (S01-S25)
# =====================================================================


class TestMap:
    async def test_map_happy(self):
        result = await ComposableStream.of(1, 2, 3).map(lambda x: x * 2).run_to_list()
        assert result == [2, 4, 6]

    async def test_map_empty_stream(self):
        result = await ComposableStream.empty().map(lambda x: x * 2).run_to_list()
        assert result == []

    async def test_map_exception_passthrough(self):
        def boom(x):
            if x == 2:
                raise ValueError("boom")
            return x * 2

        with pytest.raises(ValueError, match="boom"):
            await ComposableStream.of(1, 2, 3).map(boom).run_to_list()


class TestFlatMap:
    async def test_flat_map_happy(self):
        result = await (
            ComposableStream.of(1, 2, 3)
            .flat_map(lambda x: ComposableStream.of(x, x * 10))
            .run_to_list()
        )
        assert result == [1, 10, 2, 20, 3, 30]

    async def test_flat_map_empty_sub_stream(self):
        result = await (
            ComposableStream.of(1, 2, 3)
            .flat_map(lambda x: ComposableStream.empty() if x == 2 else ComposableStream.of(x))
            .run_to_list()
        )
        assert result == [1, 3]

    async def test_flat_map_nested(self):
        result = await (
            ComposableStream.of(1, 2)
            .flat_map(lambda x: ComposableStream.of(x).flat_map(lambda y: ComposableStream.of(y, y + 10)))
            .run_to_list()
        )
        assert result == [1, 11, 2, 12]

    async def test_flat_map_empty_stream(self):
        result = await (
            ComposableStream.empty()
            .flat_map(lambda x: ComposableStream.of(x, x * 10))
            .run_to_list()
        )
        assert result == []


class TestConcat:
    async def test_concat_happy(self):
        s1 = ComposableStream.of(1, 2)
        s2 = ComposableStream.of(3, 4)
        result = await s1.concat(s2).run_to_list()
        assert result == [1, 2, 3, 4]

    async def test_concat_empty_plus_non_empty(self):
        result = await ComposableStream.empty().concat(ComposableStream.of(3, 4)).run_to_list()
        assert result == [3, 4]

    async def test_concat_non_empty_plus_empty(self):
        result = await ComposableStream.of(1, 2).concat(ComposableStream.empty()).run_to_list()
        assert result == [1, 2]

    async def test_concat_empty_plus_empty(self):
        result = await ComposableStream.empty().concat(ComposableStream.empty()).run_to_list()
        assert result == []


class TestZipWith:
    async def test_zip_with_happy(self):
        s1 = ComposableStream.of(1, 2, 3)
        s2 = ComposableStream.of("a", "b", "c")
        result = await s1.zip_with(s2).run_to_list()
        assert result == [(1, "a"), (2, "b"), (3, "c")]

    async def test_zip_with_unequal_length_truncation(self):
        s1 = ComposableStream.of(1, 2, 3, 4, 5)
        s2 = ComposableStream.of("a", "b")
        result = await s1.zip_with(s2).run_to_list()
        assert result == [(1, "a"), (2, "b")]

    async def test_zip_with_empty_stream(self):
        result = await ComposableStream.of(1, 2).zip_with(ComposableStream.empty()).run_to_list()
        assert result == []


class TestScan:
    async def test_scan_happy(self):
        result = await ComposableStream.of(1, 2, 3).scan(0, lambda a, b: a + b).run_to_list()
        assert result == [0, 1, 3, 6]

    async def test_scan_empty_yields_only_zero(self):
        result = await ComposableStream.empty().scan(0, lambda a, b: a + b).run_to_list()
        assert result == [0]

    async def test_scan_accumulation_verify(self):
        result = await ComposableStream.of(1, 2, 3, 4).scan(10, lambda a, b: a * b).run_to_list()
        # 10, 10*1=10, 10*2=20, 20*3=60, 60*4=240
        assert result == [10, 10, 20, 60, 240]


class TestTakeWhile:
    async def test_take_while_happy(self):
        result = await ComposableStream.of(1, 2, 3, 4, 5).take_while(lambda x: x < 4).run_to_list()
        assert result == [1, 2, 3]

    async def test_take_while_all_match(self):
        result = await ComposableStream.of(1, 2, 3).take_while(lambda x: x < 10).run_to_list()
        assert result == [1, 2, 3]

    async def test_take_while_none_match(self):
        result = await ComposableStream.of(1, 2, 3).take_while(lambda x: x > 10).run_to_list()
        assert result == []

    async def test_take_while_empty_stream(self):
        result = await ComposableStream.empty().take_while(lambda x: True).run_to_list()
        assert result == []


class TestMapAsync:
    async def test_map_async_sequential(self):
        async def double(x):
            return x * 2

        result = await ComposableStream.of(1, 2, 3).map_async(double, parallelism=1).run_to_list()
        assert result == [2, 4, 6]

    async def test_map_async_parallel_order_preserved(self):
        """Parallel execution with order preserved, verified by timing."""
        delay = 0.1

        async def slow_double(x):
            await asyncio.sleep(delay)
            return x * 2

        items = list(range(5))
        start = time.monotonic()
        result = await ComposableStream.from_list(items).map_async(slow_double, parallelism=5).run_to_list()
        elapsed = time.monotonic() - start

        assert result == [x * 2 for x in items]
        # With parallelism=5 and 5 items, all run concurrently ~0.1s total.
        # Sequential would be ~0.5s. Allow generous margin.
        assert elapsed < delay * len(items) * 0.8, (
            f"Parallel execution took {elapsed:.2f}s, expected < {delay * len(items) * 0.8:.2f}s"
        )

    async def test_map_async_exception(self):
        async def boom(x):
            if x == 2:
                raise ValueError("async boom")
            return x

        with pytest.raises(ValueError, match="async boom"):
            await ComposableStream.of(1, 2, 3).map_async(boom).run_to_list()


# =====================================================================
#  DERIVED (S26-S66)
# =====================================================================


class TestFilter:
    async def test_filter_happy(self):
        result = await ComposableStream.of(1, 2, 3, 4, 5).filter(lambda x: x % 2 == 0).run_to_list()
        assert result == [2, 4]

    async def test_filter_all_filtered(self):
        result = await ComposableStream.of(1, 2, 3).filter(lambda x: x > 10).run_to_list()
        assert result == []

    async def test_filter_all_kept(self):
        result = await ComposableStream.of(1, 2, 3).filter(lambda x: x > 0).run_to_list()
        assert result == [1, 2, 3]

    async def test_filter_empty(self):
        result = await ComposableStream.empty().filter(lambda x: True).run_to_list()
        assert result == []


class TestCollect:
    async def test_collect_happy(self):
        result = await (
            ComposableStream.of(1, 2, 3)
            .collect(lambda x: x * 10 if x > 1 else None)
            .run_to_list()
        )
        assert result == [20, 30]

    async def test_collect_all_none(self):
        result = await ComposableStream.of(1, 2, 3).collect(lambda x: None).run_to_list()
        assert result == []

    async def test_collect_empty(self):
        result = await ComposableStream.empty().collect(lambda x: x).run_to_list()
        assert result == []


class TestMapConcat:
    async def test_map_concat_happy(self):
        result = await ComposableStream.of(1, 2, 3).map_concat(lambda x: [x] * x).run_to_list()
        assert result == [1, 2, 2, 3, 3, 3]

    async def test_map_concat_empty_iterable_return(self):
        result = await (
            ComposableStream.of(1, 2, 3)
            .map_concat(lambda x: [] if x == 2 else [x])
            .run_to_list()
        )
        assert result == [1, 3]

    async def test_map_concat_empty_stream(self):
        result = await ComposableStream.empty().map_concat(lambda x: [x, x]).run_to_list()
        assert result == []


class TestEnumerate:
    async def test_enumerate_happy(self):
        result = await ComposableStream.of("a", "b", "c").enumerate().run_to_list()
        assert result == [(0, "a"), (1, "b"), (2, "c")]

    async def test_enumerate_start_offset(self):
        result = await ComposableStream.of("a", "b").enumerate(start=5).run_to_list()
        assert result == [(5, "a"), (6, "b")]

    async def test_enumerate_empty(self):
        result = await ComposableStream.empty().enumerate().run_to_list()
        assert result == []


class TestTake:
    async def test_take_n_less_than_len(self):
        result = await ComposableStream.of(1, 2, 3, 4, 5).take(3).run_to_list()
        assert result == [1, 2, 3]

    async def test_take_n_equal_to_len(self):
        result = await ComposableStream.of(1, 2, 3).take(3).run_to_list()
        assert result == [1, 2, 3]

    async def test_take_n_greater_than_len(self):
        result = await ComposableStream.of(1, 2).take(5).run_to_list()
        assert result == [1, 2]

    async def test_take_n_zero(self):
        result = await ComposableStream.of(1, 2, 3).take(0).run_to_list()
        assert result == []

    async def test_take_empty(self):
        result = await ComposableStream.empty().take(3).run_to_list()
        assert result == []


class TestDrop:
    async def test_drop_n_less_than_len(self):
        result = await ComposableStream.of(1, 2, 3, 4, 5).drop(2).run_to_list()
        assert result == [3, 4, 5]

    async def test_drop_n_equal_to_len(self):
        result = await ComposableStream.of(1, 2, 3).drop(3).run_to_list()
        assert result == []

    async def test_drop_n_greater_than_len(self):
        result = await ComposableStream.of(1, 2).drop(5).run_to_list()
        assert result == []

    async def test_drop_n_zero(self):
        result = await ComposableStream.of(1, 2, 3).drop(0).run_to_list()
        assert result == [1, 2, 3]

    async def test_drop_empty(self):
        result = await ComposableStream.empty().drop(3).run_to_list()
        assert result == []


class TestGrouped:
    async def test_grouped_exact_division(self):
        result = await ComposableStream.of(1, 2, 3, 4, 5, 6).grouped(2).run_to_list()
        assert result == [[1, 2], [3, 4], [5, 6]]

    async def test_grouped_remainder(self):
        result = await ComposableStream.of(1, 2, 3, 4, 5).grouped(2).run_to_list()
        assert result == [[1, 2], [3, 4], [5]]

    async def test_grouped_n_greater_than_len(self):
        result = await ComposableStream.of(1, 2).grouped(5).run_to_list()
        assert result == [[1, 2]]

    async def test_grouped_empty(self):
        result = await ComposableStream.empty().grouped(3).run_to_list()
        assert result == []


class TestSliding:
    async def test_sliding_size_equals_step(self):
        result = await ComposableStream.of(1, 2, 3, 4).sliding(2, step=2).run_to_list()
        assert result == [[1, 2], [3, 4]]

    async def test_sliding_step_greater_than_one(self):
        result = await ComposableStream.of(1, 2, 3, 4, 5).sliding(3, step=2).run_to_list()
        assert result == [[1, 2, 3], [3, 4, 5]]

    async def test_sliding_size_greater_than_len(self):
        result = await ComposableStream.of(1, 2).sliding(5).run_to_list()
        assert result == []

    async def test_sliding_empty(self):
        result = await ComposableStream.empty().sliding(2).run_to_list()
        assert result == []


class TestPrepend:
    async def test_prepend_happy(self):
        result = await ComposableStream.of(3, 4).prepend(1, 2).run_to_list()
        assert result == [1, 2, 3, 4]

    async def test_prepend_empty_prefix(self):
        result = await ComposableStream.of(1, 2).prepend().run_to_list()
        assert result == [1, 2]

    async def test_prepend_empty_stream(self):
        result = await ComposableStream.empty().prepend(1, 2).run_to_list()
        assert result == [1, 2]


class TestIntersperse:
    async def test_intersperse_happy(self):
        result = await ComposableStream.of("a", "b", "c").intersperse(",").run_to_list()
        assert result == ["a", ",", "b", ",", "c"]

    async def test_intersperse_single_element(self):
        result = await ComposableStream.of("a").intersperse(",").run_to_list()
        assert result == ["a"]

    async def test_intersperse_empty(self):
        result = await ComposableStream.empty().intersperse(",").run_to_list()
        assert result == []


class TestDistinct:
    async def test_distinct_happy(self):
        result = await ComposableStream.of(1, 2, 2, 3, 1, 4).distinct().run_to_list()
        assert result == [1, 2, 3, 4]

    async def test_distinct_all_duplicates(self):
        result = await ComposableStream.of(5, 5, 5, 5).distinct().run_to_list()
        assert result == [5]

    async def test_distinct_custom_key(self):
        result = await (
            ComposableStream.of("apple", "Apricot", "banana", "Blueberry")
            .distinct(key=lambda s: s[0].lower())
            .run_to_list()
        )
        assert result == ["apple", "banana"]

    async def test_distinct_empty(self):
        result = await ComposableStream.empty().distinct().run_to_list()
        assert result == []


# =====================================================================
#  TERMINAL (S67-S80)
# =====================================================================


class TestRunFold:
    async def test_run_fold_happy(self):
        result = await ComposableStream.of(1, 2, 3).run_fold(0, lambda a, b: a + b)
        assert result == 6

    async def test_run_fold_empty_returns_zero(self):
        result = await ComposableStream.empty().run_fold(0, lambda a, b: a + b)
        assert result == 0


class TestRunReduce:
    async def test_run_reduce_happy(self):
        result = await ComposableStream.of(1, 2, 3).run_reduce(lambda a, b: a + b)
        assert result == 6

    async def test_run_reduce_empty_raises(self):
        with pytest.raises(ValueError, match="empty stream"):
            await ComposableStream.empty().run_reduce(lambda a, b: a + b)


class TestRunToList:
    async def test_run_to_list_happy(self):
        result = await ComposableStream.of(10, 20, 30).run_to_list()
        assert result == [10, 20, 30]

    async def test_run_to_list_empty(self):
        result = await ComposableStream.empty().run_to_list()
        assert result == []


class TestRunForeach:
    async def test_run_foreach_happy(self):
        collected = []
        await ComposableStream.of(1, 2, 3).run_foreach(collected.append)
        assert collected == [1, 2, 3]

    async def test_run_foreach_empty(self):
        collected = []
        await ComposableStream.empty().run_foreach(collected.append)
        assert collected == []


class TestRunFirst:
    async def test_run_first_happy(self):
        result = await ComposableStream.of(10, 20, 30).run_first()
        assert result == 10

    async def test_run_first_empty_raises(self):
        with pytest.raises(ValueError, match="empty stream"):
            await ComposableStream.empty().run_first()


class TestRunLast:
    async def test_run_last_happy(self):
        result = await ComposableStream.of(10, 20, 30).run_last()
        assert result == 30

    async def test_run_last_empty_raises(self):
        with pytest.raises(ValueError, match="empty stream"):
            await ComposableStream.empty().run_last()


class TestRunCount:
    async def test_run_count_happy(self):
        result = await ComposableStream.of(1, 2, 3, 4, 5).run_count()
        assert result == 5

    async def test_run_count_empty(self):
        result = await ComposableStream.empty().run_count()
        assert result == 0


# =====================================================================
#  CONSTRUCTORS (S81-S92)
# =====================================================================


class TestOf:
    async def test_of_single_value(self):
        result = await ComposableStream.of(42).run_to_list()
        assert result == [42]

    async def test_of_multiple_values(self):
        result = await ComposableStream.of(1, 2, 3).run_to_list()
        assert result == [1, 2, 3]

    async def test_of_no_args_empty(self):
        result = await ComposableStream.of().run_to_list()
        assert result == []


class TestEmpty:
    async def test_empty_yields_nothing(self):
        result = await ComposableStream.empty().run_to_list()
        assert result == []


class TestFromIterable:
    async def test_from_iterable_list(self):
        result = await ComposableStream.from_iterable([10, 20, 30]).run_to_list()
        assert result == [10, 20, 30]

    async def test_from_iterable_generator(self):
        result = await ComposableStream.from_iterable(x * 2 for x in range(3)).run_to_list()
        assert result == [0, 2, 4]

    async def test_from_iterable_tuple(self):
        result = await ComposableStream.from_iterable((7, 8, 9)).run_to_list()
        assert result == [7, 8, 9]


class TestFromList:
    async def test_from_list_happy(self):
        result = await ComposableStream.from_list([1, 2, 3]).run_to_list()
        assert result == [1, 2, 3]


class TestUnfold:
    async def test_unfold_happy(self):
        # Fibonacci-like: state = (a, b), emit a, next state (b, a+b), stop when a >= 10
        result = await ComposableStream.unfold(
            (0, 1),
            lambda s: (s[0], (s[1], s[0] + s[1])) if s[0] < 10 else None,
        ).run_to_list()
        assert result == [0, 1, 1, 2, 3, 5, 8]

    async def test_unfold_immediate_termination(self):
        result = await ComposableStream.unfold(0, lambda s: None).run_to_list()
        assert result == []


class TestUnfoldAsync:
    async def test_unfold_async_happy(self):
        async def step(s):
            if s >= 3:
                return None
            return (s * 10, s + 1)

        result = await ComposableStream.unfold_async(0, step).run_to_list()
        assert result == [0, 10, 20]

    async def test_unfold_async_immediate_termination(self):
        async def stop_immediately(s):
            return None

        result = await ComposableStream.unfold_async(0, stop_immediately).run_to_list()
        assert result == []
