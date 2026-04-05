"""Composable async stream — category-theory-grounded composition over async iterators.

Organized by algebraic structure.  **Primitives** are the minimal basis from
which all other operators derive:

    Functor          map
    Monad            of (pure)  +  flat_map (bind)
    Monoid           empty  +  concat
    Product          zip_with
    Catamorphism     run_fold (terminal)  /  scan (streaming)
    Anamorphism      unfold  /  unfold_async
    Truncation       take_while
    Non-deterministic merge
    Effectful        map_async

**Derived** operators are implemented via primitives and clearly marked.

Example::

    result = await (
        ComposableStream.of(1, 2, 3, 4, 5)
            .filter(lambda x: x % 2 == 0)   # derived: flat_map + of + empty
            .scan(0, lambda a, b: a + b)     # primitive: streaming catamorphism
            .run_fold("", lambda a, c: f"{a},{c}")  # primitive: terminal catamorphism
    )
"""

from __future__ import annotations

import asyncio
import enum
import time
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Generic,
    Iterable,
    Optional,
    TypeVar,
)

from everything_is_an_actor.core.composable_future import ComposableFuture

T = TypeVar("T")
U = TypeVar("U")
V = TypeVar("V")

_STREAM_DONE = object()  # sentinel — safe even if stream yields None


def _try_get_running_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


# =====================================================================
# Push-side infrastructure (Akka Source.queue equivalent)
# =====================================================================


class OfferResult(enum.Enum):
    ENQUEUED = "enqueued"
    DROPPED = "dropped"
    CLOSED = "closed"


class OverflowStrategy(enum.Enum):
    BACKPRESSURE = "backpressure"
    DROP_NEW = "drop_new"
    DROP_HEAD = "drop_head"
    FAIL = "fail"


class BufferOverflowError(Exception):
    """Raised by ``offer()`` with ``OverflowStrategy.FAIL`` when buffer is full."""


class StreamClosedError(Exception):
    """Raised by ``put()`` on a closed ``StreamSender``."""


class _StreamError:
    __slots__ = ("error",)

    def __init__(self, error: Exception) -> None:
        self.error = error


class StreamSender(Generic[T]):
    """Push-side handle for a channel-backed ``ComposableStream``.

    Thread-safe and cross-loop safe.  Two push modes:

    - ``offer(element)`` — sync, non-blocking, applies overflow strategy
    - ``put(element)``   — async, backpressure-aware
    """

    __slots__ = ("_queue", "_loop", "_overflow", "_closed", "_capacity")

    def __init__(
        self,
        queue: asyncio.Queue[Any],
        loop: asyncio.AbstractEventLoop,
        overflow: OverflowStrategy,
        capacity: int,
    ) -> None:
        self._queue = queue
        self._loop = loop
        self._overflow = overflow
        self._closed = False
        self._capacity = capacity  # user-visible limit (queue has +1 for sentinel)

    @property
    def is_closed(self) -> bool:
        return self._closed

    # -- sync, non-blocking -------------------------------------------

    def offer(self, element: T) -> OfferResult:
        if self._closed:
            return OfferResult.CLOSED
        current = _try_get_running_loop()
        if current is self._loop:
            return self._offer_local(element)
        return self._offer_remote(element)

    def _offer_local(self, element: T) -> OfferResult:
        if self._queue.qsize() < self._capacity:
            self._queue.put_nowait(element)
            return OfferResult.ENQUEUED
        if self._overflow in (OverflowStrategy.DROP_NEW, OverflowStrategy.BACKPRESSURE):
            return OfferResult.DROPPED
        if self._overflow == OverflowStrategy.DROP_HEAD:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self._queue.put_nowait(element)
            return OfferResult.ENQUEUED
        raise BufferOverflowError("Stream buffer full (OverflowStrategy.FAIL)")

    def _offer_remote(self, element: T) -> OfferResult:
        overflow = self._overflow

        capacity = self._capacity

        def _do() -> None:
            # Don't check _closed here — this callback was dispatched by
            # offer() which already returned ENQUEUED.  Checking _closed
            # would break the contract if complete() races ahead.
            if self._queue.qsize() < capacity:
                self._queue.put_nowait(element)
            elif overflow == OverflowStrategy.DROP_HEAD:
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    self._queue.put_nowait(element)
                except asyncio.QueueFull:
                    pass

        self._loop.call_soon_threadsafe(_do)
        return OfferResult.ENQUEUED  # best-effort

    # -- async, backpressure-aware ------------------------------------

    async def put(self, element: T) -> None:
        if self._closed:
            raise StreamClosedError("StreamSender is closed")
        current = _try_get_running_loop()
        if current is self._loop:
            await self._queue.put(element)
        else:
            fut = asyncio.run_coroutine_threadsafe(
                self._queue.put(element),
                self._loop,
            )
            await asyncio.wrap_future(fut)

    # -- lifecycle ----------------------------------------------------

    async def complete(self) -> None:
        if self._closed:
            return
        self._closed = True
        current = _try_get_running_loop()
        if current is self._loop:
            await self._queue.put(_STREAM_DONE)
        else:
            fut = asyncio.run_coroutine_threadsafe(
                self._queue.put(_STREAM_DONE),
                self._loop,
            )
            await asyncio.wrap_future(fut)

    async def fail(self, error: Exception) -> None:
        if self._closed:
            return
        self._closed = True
        sentinel = _StreamError(error)
        current = _try_get_running_loop()
        if current is self._loop:
            await self._queue.put(sentinel)
        else:
            fut = asyncio.run_coroutine_threadsafe(
                self._queue.put(sentinel),
                self._loop,
            )
            await asyncio.wrap_future(fut)


# =====================================================================
# ComposableStream
# =====================================================================


class ComposableStream(Generic[T]):
    """Category-theory-grounded composable wrapper over ``AsyncIterator[T]``.

    Primitives form the minimal algebraic basis; derived operators are
    composed from them.  All operators are lazy — no work until a
    terminal ``run_*`` is called.
    """

    __slots__ = ("_source",)

    def __init__(self, source: AsyncIterator[T]) -> None:
        self._source = source

    # =================================================================
    #  PRIMITIVES — Categorical basis
    # =================================================================

    # -- Functor ------------------------------------------------------

    def map(self, fn: Callable[[T], U]) -> ComposableStream[U]:
        """Functor fmap.  ``(A → B) → F[A] → F[B]``"""
        source = self._source

        async def _gen() -> AsyncIterator[U]:
            async for item in source:
                yield fn(item)

        return ComposableStream(_gen())

    # -- Monad --------------------------------------------------------

    def flat_map(self, fn: Callable[[T], ComposableStream[U]]) -> ComposableStream[U]:
        """Monad bind (>>=).  ``F[A] → (A → F[B]) → F[B]``

        Sequential: each sub-stream is fully consumed before the next.
        For concurrent sub-streams, see ``flat_map_merge``.
        """
        source = self._source

        async def _gen() -> AsyncIterator[U]:
            async for item in source:
                async for sub in fn(item)._source:
                    yield sub

        return ComposableStream(_gen())

    # -- Monoid -------------------------------------------------------

    def concat(self, other: ComposableStream[T]) -> ComposableStream[T]:
        """Monoid combine (sequential).  ``F[A] ⊕ F[A] → F[A]``"""
        source = self._source

        async def _gen() -> AsyncIterator[T]:
            async for item in source:
                yield item
            async for item in other._source:
                yield item

        return ComposableStream(_gen())

    # -- Product (Applicative) ----------------------------------------

    def zip_with(self, other: ComposableStream[U]) -> ComposableStream[tuple[T, U]]:
        """Product in the category of streams.  Pairs elements positionally;
        terminates when the shorter stream ends."""
        source_a = self._source
        source_b = other._source

        async def _gen() -> AsyncIterator[tuple[T, U]]:
            async for a in source_a:
                try:
                    b = await source_b.__anext__()
                except StopAsyncIteration:
                    break
                yield (a, b)

        return ComposableStream(_gen())

    # -- Catamorphism (streaming) -------------------------------------

    def scan(self, zero: U, fn: Callable[[U, T], U]) -> ComposableStream[U]:
        """Streaming catamorphism — like ``run_fold`` but emits every
        intermediate accumulator.

        Equivalent to Akka ``scan`` / Haskell ``scanl``.
        """
        source = self._source

        async def _gen() -> AsyncIterator[U]:
            acc = zero
            yield acc
            async for item in source:
                acc = fn(acc, item)
                yield acc

        return ComposableStream(_gen())

    # -- Truncation (coalgebra) ---------------------------------------

    def take_while(self, predicate: Callable[[T], bool]) -> ComposableStream[T]:
        """Coalgebra truncation — stop observation when predicate fails.

        Cannot be derived from flat_map because flat_map cannot signal
        early termination of the upstream source.
        """
        source = self._source

        async def _gen() -> AsyncIterator[T]:
            async for item in source:
                if not predicate(item):
                    break
                yield item

        return ComposableStream(_gen())

    # -- Non-deterministic combine ------------------------------------

    def merge(self, other: ComposableStream[T]) -> ComposableStream[T]:
        """Non-deterministic interleave (concurrent).

        Elements arrive from whichever source is ready first.
        Not derivable from sequential monad — requires concurrency.
        """

        async def _gen() -> AsyncIterator[T]:
            q: asyncio.Queue[Any] = asyncio.Queue()

            async def _pump(src: AsyncIterator[T]) -> None:
                async for item in src:
                    await q.put(item)
                await q.put(_STREAM_DONE)

            t1 = asyncio.create_task(_pump(self._source))
            t2 = asyncio.create_task(_pump(other._source))
            try:
                finished = 0
                while finished < 2:
                    item = await q.get()
                    if item is _STREAM_DONE:
                        finished += 1
                    else:
                        yield item
            finally:
                t1.cancel()
                t2.cancel()
                for t in (t1, t2):
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass

        return ComposableStream(_gen())

    # -- Effectful functor --------------------------------------------

    def map_async(
        self,
        fn: Callable[[T], Awaitable[U]],
        parallelism: int = 1,
    ) -> ComposableStream[U]:
        """Effectful (Kleisli) functor lift.

        ``parallelism=1`` is sequential.  ``parallelism>1`` runs up to N
        transforms concurrently but yields in **input order** (Akka
        ``mapAsync`` semantics).
        """
        source = self._source
        if parallelism <= 1:

            async def _sequential() -> AsyncIterator[U]:
                async for item in source:
                    yield await fn(item)

            return ComposableStream(_sequential())

        async def _ordered_parallel() -> AsyncIterator[U]:
            pending: asyncio.Queue[asyncio.Task[U]] = asyncio.Queue(maxsize=parallelism)
            done_event = asyncio.Event()
            producer_error: list[BaseException] = []

            async def _run_fn(item: T) -> U:
                return await fn(item)

            async def _producer() -> None:
                try:
                    async for item in source:
                        task = asyncio.create_task(_run_fn(item))
                        await pending.put(task)
                except BaseException as e:
                    producer_error.append(e)
                finally:
                    done_event.set()

            producer = asyncio.create_task(_producer())
            try:
                while True:
                    if pending.empty() and done_event.is_set():
                        break
                    try:
                        task = await asyncio.wait_for(pending.get(), timeout=0.1)
                        yield await task
                    except asyncio.TimeoutError:
                        if done_event.is_set() and pending.empty():
                            break
                if producer_error:
                    raise producer_error[0]
            finally:
                producer.cancel()
                try:
                    await producer
                except asyncio.CancelledError:
                    pass

        return ComposableStream(_ordered_parallel())

    # -- Flow control (infrastructure, not algebraic) -----------------

    def buffer(self, size: int) -> ComposableStream[T]:
        """Decouple producer/consumer with a bounded async buffer."""
        source = self._source

        async def _gen() -> AsyncIterator[T]:
            q: asyncio.Queue[Any] = asyncio.Queue(maxsize=size)

            async def _fill() -> None:
                async for item in source:
                    await q.put(item)
                await q.put(_STREAM_DONE)

            filler = asyncio.create_task(_fill())
            try:
                while True:
                    item = await q.get()
                    if item is _STREAM_DONE:
                        break
                    yield item
            finally:
                filler.cancel()
                try:
                    await filler
                except asyncio.CancelledError:
                    pass

        return ComposableStream(_gen())

    # =================================================================
    #  DERIVED — Composed from primitives
    #
    #  Each operator documents its categorical derivation and why the
    #  optimized implementation was chosen.  The derivations are verified
    #  by property-based equivalence tests (test_stream_laws.py).
    # =================================================================

    def filter(self, predicate: Callable[[T], bool]) -> ComposableStream[T]:
        """Keep elements matching predicate.

        Derivation:  ``flat_map(λx. of(x) if p(x) else empty())``
        Why direct:  flat_map derivation allocates a temporary ComposableStream
                     + async generator per element.  For N elements that's 2N
                     objects; direct generator is zero-alloc per element.
        """
        source = self._source

        async def _gen() -> AsyncIterator[T]:
            async for item in source:
                if predicate(item):
                    yield item

        return ComposableStream(_gen())

    def collect(self, fn: Callable[[T], Optional[U]]) -> ComposableStream[U]:
        """Filter + map in one pass.

        Derivation:  ``flat_map(λx. of(r) if (r := fn(x)) is not None else empty())``
        Why direct:  same per-element allocation issue as filter.
        """
        source = self._source

        async def _gen() -> AsyncIterator[U]:
            async for item in source:
                result = fn(item)
                if result is not None:
                    yield result

        return ComposableStream(_gen())

    def map_concat(self, fn: Callable[[T], Iterable[U]]) -> ComposableStream[U]:
        """Sync 1→N expand.  Akka ``mapConcat`` / Haskell ``concatMap``.

        Derivation:  ``flat_map(λx. from_iterable(f(x)))``
        Why direct:  flat_map wraps each iterable in a ComposableStream +
                     async generator.  Direct implementation iterates the
                     sync iterable inline — no async overhead per sub-item.
        """
        source = self._source

        async def _gen() -> AsyncIterator[U]:
            async for item in source:
                for sub in fn(item):
                    yield sub

        return ComposableStream(_gen())

    def enumerate(self, start: int = 0) -> ComposableStream[tuple[int, T]]:
        """Attach index to each element.

        Derivation:  ``zip_with(unfold(start, λi. (i, i+1))).map(swap)``
        Why direct:  zip_with + unfold creates two extra async generator
                     chains + per-element tuple allocation from zip.
                     Direct generator uses one int counter.
        """
        source = self._source

        async def _gen() -> AsyncIterator[tuple[int, T]]:
            i = start
            async for item in source:
                yield (i, item)
                i += 1

        return ComposableStream(_gen())

    def take(self, n: int) -> ComposableStream[T]:
        """Take first N elements.

        Derivation:  ``zip_with(unfold(0, λi. (i,i+1) if i<n else None)).map(fst)``
        Why direct:  zip_with derivation creates a counter stream + pairs
                     every element into a tuple.  Direct generator is a
                     single counter variable with early break.
        """
        source = self._source

        async def _gen() -> AsyncIterator[T]:
            count = 0
            async for item in source:
                if count >= n:
                    break
                yield item
                count += 1

        return ComposableStream(_gen())

    def drop(self, n: int) -> ComposableStream[T]:
        """Skip first N elements.

        Derivation:  ``enumerate().flat_map(λ(i,x). of(x) if i≥n else empty())``
        Why direct:  three-layer derivation (enumerate + flat_map + of/empty)
                     vs one counter variable.
        """
        source = self._source

        async def _gen() -> AsyncIterator[T]:
            count = 0
            async for item in source:
                if count < n:
                    count += 1
                    continue
                yield item

        return ComposableStream(_gen())

    def grouped(self, n: int) -> ComposableStream[list[T]]:
        """Group into fixed-size batches (last batch may be shorter).

        Derivation:  would need ``scan`` with emit-and-reset semantics,
                     but scan only accumulates — it cannot emit a batch
                     AND reset the accumulator in one step.  A clean
                     derivation requires a ``stateful_map`` primitive
                     (scan variant with explicit output control), which
                     we deliberately exclude to keep the primitive set
                     minimal.  Adding stateful_map would be an "escape
                     hatch for arbitrary stateful logic" that undermines
                     algebraic composability.
        Why direct:  no clean derivation exists from the current primitives.
        """
        source = self._source

        async def _gen() -> AsyncIterator[list[T]]:
            batch: list[T] = []
            async for item in source:
                batch.append(item)
                if len(batch) == n:
                    yield batch
                    batch = []
            if batch:
                yield batch

        return ComposableStream(_gen())

    def sliding(self, size: int, step: int = 1) -> ComposableStream[list[T]]:
        """Sliding window over elements.

        Derivation:  same constraint as ``grouped`` — needs stateful
                     emit-and-shift that scan cannot express.
        Why direct:  no clean derivation from current primitives.
        """
        source = self._source

        async def _gen() -> AsyncIterator[list[T]]:
            window: list[T] = []
            async for item in source:
                window.append(item)
                if len(window) == size:
                    yield list(window)
                    window = window[step:]

        return ComposableStream(_gen())

    def prepend(self, *items: T) -> ComposableStream[T]:
        """Prepend elements before the stream.

        Derivation:  ``from_iterable(items).concat(self)``
        Impl:        uses concat directly — overhead is one extra async
                     generator boundary, acceptable for a one-time prefix.
        """
        return ComposableStream.from_iterable(items).concat(self)

    def intersperse(self, separator: T) -> ComposableStream[T]:
        """Insert separator between elements.

        Derivation:  ``enumerate().flat_map(λ(i,x). of(x) if i==0
                     else of(sep).concat(of(x)))``
        Why direct:  derivation creates 3 ComposableStreams per element
                     (enumerate + flat_map + of/concat/of).  Direct
                     generator uses a boolean flag.
        """
        source = self._source

        async def _gen() -> AsyncIterator[T]:
            first = True
            async for item in source:
                if not first:
                    yield separator
                first = False
                yield item

        return ComposableStream(_gen())

    def distinct(self, key: Callable[[T], Any] = lambda x: x) -> ComposableStream[T]:
        """Deduplicate by key.

        Derivation:  ``scan((∅, _, False), λ(seen,_,_), x.
                     (seen∪{k}, x, k∉seen)).drop(1).collect(λs. s[1] if s[2])``
        Why direct:  scan derivation has type variance issues — the initial
                     state ``(set(), None, False)`` and step return
                     ``(set, T, bool)`` are structurally different tuples;
                     Pyright cannot unify them.  Also, the scan emits
                     every intermediate state; we'd need collect to filter,
                     adding two extra generator layers.
        """
        source = self._source

        async def _gen() -> AsyncIterator[T]:
            seen: set[Any] = set()
            async for item in source:
                k = key(item)
                if k not in seen:
                    seen.add(k)
                    yield item

        return ComposableStream(_gen())

    # -- Error handling -----------------------------------------------

    def recover(self, fn: Callable[[Exception], T]) -> ComposableStream[T]:
        """On error, emit recovery value and stop."""
        source = self._source

        async def _gen() -> AsyncIterator[T]:
            try:
                async for item in source:
                    yield item
            except Exception as e:
                yield fn(e)

        return ComposableStream(_gen())

    def recover_with(
        self,
        fn: Callable[[Exception], ComposableStream[T]],
    ) -> ComposableStream[T]:
        """On error, switch to fallback stream."""
        source = self._source

        async def _gen() -> AsyncIterator[T]:
            try:
                async for item in source:
                    yield item
            except Exception as e:
                async for item in fn(e)._source:
                    yield item

        return ComposableStream(_gen())

    def map_error(self, fn: Callable[[Exception], Exception]) -> ComposableStream[T]:
        """Transform errors.  Akka ``mapError``."""
        source = self._source

        async def _gen() -> AsyncIterator[T]:
            try:
                async for item in source:
                    yield item
            except Exception as e:
                raise fn(e) from e

        return ComposableStream(_gen())

    # -- Side effects -------------------------------------------------

    def and_then(self, fn: Callable[[T], Any]) -> ComposableStream[T]:
        """Side effect per element (logging, metrics). Passes through unchanged.

        Akka ``wireTap`` equivalent.
        """
        source = self._source

        async def _gen() -> AsyncIterator[T]:
            async for item in source:
                fn(item)
                yield item

        return ComposableStream(_gen())

    def also_to(self, sink_fn: Callable[[T], Any]) -> ComposableStream[T]:
        """Fan-out side-effect — alias for ``and_then``.  Akka ``alsoTo``."""
        return self.and_then(sink_fn)

    # -- Flow control (derived) ---------------------------------------

    def throttle(self, elements: int, per: float) -> ComposableStream[T]:
        """Limit throughput to *elements* per *per* seconds."""
        source = self._source

        async def _gen() -> AsyncIterator[T]:
            count = 0
            window_start = time.monotonic()
            async for item in source:
                count += 1
                if count >= elements:
                    elapsed = time.monotonic() - window_start
                    if elapsed < per:
                        await asyncio.sleep(per - elapsed)
                    count = 0
                    window_start = time.monotonic()
                yield item

        return ComposableStream(_gen())

    def grouped_within(self, n: int, timeout: float) -> ComposableStream[list[T]]:
        """Group by count OR time window — whichever triggers first.

        Akka ``groupedWithin``.  Requires concurrency (timer vs source).
        """
        source = self._source

        async def _gen() -> AsyncIterator[list[T]]:
            exhausted = False
            while not exhausted:
                batch = []
                deadline = time.monotonic() + timeout
                while len(batch) < n:
                    try:
                        remaining = max(0, deadline - time.monotonic())
                        item = await asyncio.wait_for(
                            source.__anext__(),
                            timeout=remaining,
                        )
                        batch.append(item)
                    except asyncio.TimeoutError:
                        break
                    except StopAsyncIteration:
                        exhausted = True
                        break
                if batch:
                    yield batch

        return ComposableStream(_gen())

    def keep_alive(self, interval: float, element: T) -> ComposableStream[T]:
        """Inject *element* if upstream is idle for *interval* seconds.

        Akka ``keepAlive``.
        """
        source = self._source

        async def _gen() -> AsyncIterator[T]:
            while True:
                try:
                    item = await asyncio.wait_for(source.__anext__(), timeout=interval)
                    yield item
                except asyncio.TimeoutError:
                    yield element
                except StopAsyncIteration:
                    break

        return ComposableStream(_gen())

    def completion_timeout(self, seconds: float) -> ComposableStream[T]:
        """Fail if stream doesn't complete within *seconds*.

        Akka ``completionTimeout``.
        """
        source = self._source

        async def _gen() -> AsyncIterator[T]:
            deadline = time.monotonic() + seconds
            async for item in source:
                if time.monotonic() > deadline:
                    raise asyncio.TimeoutError(f"Stream did not complete within {seconds}s")
                yield item

        return ComposableStream(_gen())

    def idle_timeout(self, seconds: float) -> ComposableStream[T]:
        """Fail if no element arrives within *seconds*.

        Akka ``idleTimeout``.
        """
        source = self._source

        async def _gen() -> AsyncIterator[T]:
            while True:
                try:
                    item = await asyncio.wait_for(source.__anext__(), timeout=seconds)
                    yield item
                except asyncio.TimeoutError:
                    raise asyncio.TimeoutError(f"Stream idle for {seconds}s")
                except StopAsyncIteration:
                    break

        return ComposableStream(_gen())

    # -- Concurrent derived -------------------------------------------

    def flat_map_merge(
        self,
        fn: Callable[[T], ComposableStream[U]],
        breadth: int = 16,
    ) -> ComposableStream[U]:
        """Concurrent flat_map — up to *breadth* sub-streams run in parallel.

        Akka ``flatMapMerge``.  Elements arrive in non-deterministic order.
        """
        source = self._source

        async def _gen() -> AsyncIterator[U]:
            q: asyncio.Queue[Any] = asyncio.Queue()
            sem = asyncio.Semaphore(breadth)
            active = 0
            producer_done = asyncio.Event()
            lock = asyncio.Lock()

            async def _run_sub(item: T) -> None:
                nonlocal active
                try:
                    async for sub_item in fn(item)._source:
                        await q.put(sub_item)
                finally:
                    sem.release()
                    async with lock:
                        active -= 1
                        if active == 0 and producer_done.is_set():
                            await q.put(_STREAM_DONE)

            async def _producer() -> None:
                nonlocal active
                async for item in source:
                    await sem.acquire()
                    async with lock:
                        active += 1
                    asyncio.create_task(_run_sub(item))
                producer_done.set()
                async with lock:
                    if active == 0:
                        await q.put(_STREAM_DONE)

            prod_task = asyncio.create_task(_producer())
            try:
                while True:
                    item = await q.get()
                    if item is _STREAM_DONE:
                        break
                    yield item
            finally:
                prod_task.cancel()
                try:
                    await prod_task
                except asyncio.CancelledError:
                    pass

        return ComposableStream(_gen())

    def interleave(self, other: ComposableStream[T], segment: int = 1) -> ComposableStream[T]:
        """Ordered interleave — take *segment* elements from each stream in turn.

        Akka ``interleave``.
        """
        source_a = self._source
        source_b = other._source

        async def _gen() -> AsyncIterator[T]:
            a_done = False
            b_done = False
            while not (a_done and b_done):
                if not a_done:
                    for _ in range(segment):
                        try:
                            yield await source_a.__anext__()
                        except StopAsyncIteration:
                            a_done = True
                            break
                if not b_done:
                    for _ in range(segment):
                        try:
                            yield await source_b.__anext__()
                        except StopAsyncIteration:
                            b_done = True
                            break

        return ComposableStream(_gen())

    # -- Lifecycle ----------------------------------------------------

    def watch_termination(
        self,
        callback: Callable[[Optional[Exception]], Any],
    ) -> ComposableStream[T]:
        """Invoke *callback* when stream completes or errors.

        ``callback(None)`` on success, ``callback(exc)`` on error.
        Akka ``watchTermination``.
        """
        source = self._source

        async def _gen() -> AsyncIterator[T]:
            try:
                async for item in source:
                    yield item
                callback(None)
            except Exception as e:
                callback(e)
                raise

        return ComposableStream(_gen())

    # =================================================================
    #  TERMINAL SINKS — Catamorphism (run the stream)
    # =================================================================

    def run_fold(self, zero: U, fn: Callable[[U, T], U]) -> ComposableFuture[U]:
        """Terminal catamorphism — fold all elements into a single value."""
        source = self._source

        async def _fold() -> U:
            acc = zero
            async for item in source:
                acc = fn(acc, item)
            return acc

        return ComposableFuture(_fold())

    def run_reduce(self, fn: Callable[[T, T], T]) -> ComposableFuture[T]:
        """Terminal reduce (requires ≥ 1 element)."""
        source = self._source

        async def _reduce() -> T:
            acc: T | None = None
            first = True
            async for item in source:
                if first:
                    acc = item
                    first = False
                else:
                    acc = fn(acc, item)  # type: ignore[arg-type]
            if first:
                raise ValueError("run_reduce on empty stream")
            return acc  # type: ignore[return-value]

        return ComposableFuture(_reduce())

    def run_to_list(self) -> ComposableFuture[list[T]]:
        """Collect all elements into a list."""
        source = self._source

        async def _collect() -> list[T]:
            return [item async for item in source]

        return ComposableFuture(_collect())

    def run_foreach(self, fn: Callable[[T], Any]) -> ComposableFuture[None]:
        """Consume all elements with a side-effecting function."""
        source = self._source

        async def _foreach() -> None:
            async for item in source:
                fn(item)

        return ComposableFuture(_foreach())

    def run_first(self) -> ComposableFuture[T]:
        """Return the first element."""
        source = self._source

        async def _first() -> T:
            async for item in source:
                return item
            raise ValueError("run_first on empty stream")

        return ComposableFuture(_first())

    def run_last(self) -> ComposableFuture[T]:
        """Return the last element."""
        source = self._source

        async def _last() -> T:
            last: T | None = None
            found = False
            async for item in source:
                last = item
                found = True
            if not found:
                raise ValueError("run_last on empty stream")
            return last  # type: ignore[return-value]

        return ComposableFuture(_last())

    def run_count(self) -> ComposableFuture[int]:
        """Count elements."""
        return self.run_fold(0, lambda acc, _: acc + 1)

    # =================================================================
    #  CONSTRUCTORS — Anamorphisms & lifting
    # =================================================================

    @staticmethod
    def of(*items: T) -> ComposableStream[T]:
        """Monad return / pure.  Lift values into a stream."""

        async def _gen() -> AsyncIterator[T]:
            for item in items:
                yield item

        return ComposableStream(_gen())

    @staticmethod
    def empty() -> ComposableStream[Any]:
        """Monoid identity.  Zero-element stream."""

        async def _gen() -> AsyncIterator[Any]:
            if False:
                yield  # type: ignore[misc]  # make it an async generator

        return ComposableStream(_gen())

    @staticmethod
    def from_iterable(items: Iterable[T]) -> ComposableStream[T]:
        """Lift any sync iterable into a stream."""

        async def _gen() -> AsyncIterator[T]:
            for item in items:
                yield item

        return ComposableStream(_gen())

    @staticmethod
    def from_list(items: list[T]) -> ComposableStream[T]:
        """Alias for ``from_iterable`` (backwards compat)."""
        return ComposableStream.from_iterable(items)

    @staticmethod
    def unfold(seed: U, fn: Callable[[U], Optional[tuple[T, U]]]) -> ComposableStream[T]:
        """Anamorphism — generate from seed.

        ``fn(state) → (element, next_state)`` or ``None`` to stop.
        """

        async def _gen() -> AsyncIterator[T]:
            state = seed
            while True:
                result = fn(state)
                if result is None:
                    break
                element, state = result
                yield element

        return ComposableStream(_gen())

    @staticmethod
    def unfold_async(
        seed: U,
        fn: Callable[[U], Awaitable[Optional[tuple[T, U]]]],
    ) -> ComposableStream[T]:
        """Async anamorphism — generate from seed with async step function."""

        async def _gen() -> AsyncIterator[T]:
            state = seed
            while True:
                result = await fn(state)
                if result is None:
                    break
                element, state = result
                yield element

        return ComposableStream(_gen())

    @staticmethod
    def channel(
        buffer_size: int = 16,
        overflow: OverflowStrategy = OverflowStrategy.BACKPRESSURE,
    ) -> tuple[ComposableStream[T], StreamSender[T]]:
        """Push-based source with sender handle.

        Akka ``Source.queue.preMaterialize()``.  Returns ``(stream, sender)``.
        """
        loop = asyncio.get_running_loop()
        # +1 internal slot so complete()/fail() sentinel never blocks
        queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=buffer_size + 1)

        async def _gen() -> AsyncIterator[T]:
            while True:
                item = await queue.get()
                if item is _STREAM_DONE:
                    break
                if isinstance(item, _StreamError):
                    raise item.error
                yield item

        return ComposableStream(_gen()), StreamSender(queue, loop, overflow, buffer_size)

    # =================================================================
    #  CROSS-LOOP — delegates to ComposableFuture's bridging
    # =================================================================

    def on(self, loop: asyncio.AbstractEventLoop) -> ComposableStream[T]:
        """Pin stream to *loop* — enables cross-loop consumption.

        Each ``__anext__()`` is bridged via ``ComposableFuture``'s
        cross-loop machinery (``run_coroutine_threadsafe`` + callback
        chaining).  The returned stream can be iterated from any loop.

        No cross-loop logic lives here — it's fully delegated to
        ``ComposableFuture.on(loop)``.

        Usage::

            # Stream source on loop-A, consumer on loop-B:
            async for item in stream.on(loop_a):
                process(item)

            # Or pin the terminal future:
            result = await stream.run_to_list().on(loop_a)
        """
        source = self._source
        source_loop = loop

        async def _gen() -> AsyncIterator[T]:
            while True:
                try:
                    item = await ComposableFuture(
                        source.__anext__(),
                        loop=source_loop,
                    )
                    yield item
                except StopAsyncIteration:
                    break

        return ComposableStream(_gen())

    # =================================================================
    #  PROTOCOL
    # =================================================================

    def __aiter__(self) -> AsyncIterator[T]:
        return self._source
