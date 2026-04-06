"""Composable async future — category-theory-grounded composition over awaitables.

Algebraic structure:

    Functor          map
    Monad            of (pure)  +  flat_map (bind)
    Applicative      zip  +  ap
    MonadError       failed (raise)  +  recover / recover_with
    Traverse         sequence
    Alternative      first_completed (race)

Resolve-once semantics: the first ``await`` runs the underlying coroutine
and caches the outcome.  Subsequent awaits return the cached result,
making it safe to fork multiple composition chains from the same instance.

Blocking callers can use ``result(timeout)`` from any non-async thread.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, Awaitable, Callable, TypeVar, Generic, Sequence

import anyio

from everything_is_an_actor.core.types import Failure, Success, Try

T = TypeVar("T")
U = TypeVar("U")

I = TypeVar("I", contravariant=True)
O = TypeVar("O", covariant=True)


# =====================================================================
# Typed wrappers — explicit type annotations for lambdas
# =====================================================================


class Fn(Generic[I, O]):
    """Typed sync lambda — use with ``map``, ``filter``, ``recover``.

    Python lacks type inference on lambdas; ``Fn`` gives the type checker
    explicit input/output types.

    Example::

        cf.map(Fn[str, int](lambda s: len(s)))
    """

    __slots__ = ("_fn",)

    def __init__(self, fn: Callable[[I], O]) -> None:
        self._fn = fn

    def __call__(self, input: I) -> O:
        return self._fn(input)


class AsyncFn(Generic[I, O]):
    """Typed async lambda — use with ``flat_map``, ``recover_with``.

    Example::

        cf.flat_map(AsyncFn[RawMsg, AuthedMsg](lambda m: auth(ctx, m)))
    """

    __slots__ = ("_fn",)

    def __init__(self, fn: Callable[[I], Awaitable[O]]) -> None:
        self._fn = fn

    async def __call__(self, input: I) -> O:
        return await self._fn(input)


# =====================================================================
# ComposableFuture
# =====================================================================


async def _cancel_and_wait(tasks: list[asyncio.Task]) -> None:
    """Cancel all tasks and await their completion (absorbing exceptions)."""
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


class ComposableFuture(Generic[T]):
    """Category-theory-grounded composable wrapper over any ``Awaitable[T]``.

    Resolve-once, cache forever — like Scala's ``Future``.  The first
    ``await`` (or ``result()``) runs the underlying coroutine and caches
    the outcome as ``Try[T]``.  Subsequent awaits return the cached
    result immediately, making it safe to fork multiple composition
    chains from the same instance::

        cf = ctx.ask(Actor, msg)
        upper = cf.map(str.upper)
        lower = cf.map(str.lower)
        # both work — cf resolves once, both chains read the cache

    Blocking access from a sync thread::

        result = cf.result(timeout=5.0)
    """

    __slots__ = ("_coro", "_outcome", "_resolving")

    def __init__(self, coro: Awaitable[T]) -> None:
        self._coro: Awaitable[T] | None = coro
        self._outcome: Try[T] | None = None
        self._resolving: asyncio.Event | None = None

    def _wrap(self, coro: Awaitable[U]) -> ComposableFuture[U]:
        """Create a child future."""
        return ComposableFuture(coro)

    async def _resolve(self) -> T:
        """Resolve the awaitable, caching the result for subsequent calls.

        First awaiter runs the coroutine and caches the outcome as ``Try[T]``.
        Concurrent awaiters wait on an ``asyncio.Event`` barrier
        until the first one finishes, then read the cache.
        """
        # Fast path: already resolved
        if self._outcome is not None:
            return self._outcome.get()

        # Another awaiter is resolving — wait for it
        if self._resolving is not None:
            await self._resolving.wait()
            if self._outcome is not None:
                return self._outcome.get()
            raise RuntimeError("ComposableFuture resolved without outcome")

        # First awaiter — claim the resolve slot
        self._resolving = asyncio.Event()
        try:
            coro = self._coro
            result = await coro  # type: ignore[misc]
            self._outcome = Success(result)
            self._coro = None  # release coroutine for GC
            return result
        except Exception as e:
            self._outcome = Failure(e)
            self._coro = None
            raise
        finally:
            self._resolving.set()
            self._resolving = None  # release Event for GC

    # =================================================================
    #  EXECUTION — await / blocking
    # =================================================================

    def __await__(self):
        return self._resolve().__await__()

    def result(self, timeout: float | None = None) -> T:
        """Blocking get — works from any non-async thread.

        Raises RuntimeError if called from within a running event loop
        (use ``await`` instead).
        """
        # Fast path: already resolved
        if self._outcome is not None:
            return self._outcome.get()

        # Reject if called from within an async context
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass  # No running loop — good, we're in a sync thread
        else:
            raise RuntimeError(
                "ComposableFuture.result() called from within a running event loop. "
                "Use 'await' instead."
            )

        # Sync thread — run in a fresh event loop
        if timeout is not None:
            async def _timed() -> T:
                with anyio.fail_after(timeout):
                    return await self._resolve()
            return asyncio.run(_timed())
        return asyncio.run(self._resolve())

    # =================================================================
    #  PRIMITIVES — Categorical basis
    # =================================================================

    # -- Functor ------------------------------------------------------

    def map(self, fn: Callable[[T], U]) -> ComposableFuture[U]:
        """Functor fmap.  ``(A → B) → F[A] → F[B]``"""

        async def _mapped():
            return fn(await self._resolve())

        return self._wrap(_mapped())

    # -- Monad --------------------------------------------------------

    def flat_map(self, fn: Callable[[T], Awaitable[U]]) -> ComposableFuture[U]:
        """Monad bind (>>=).  ``F[A] → (A → F[B]) → F[B]``

        Accepts ``Awaitable[U]`` (not just ``ComposableFuture[U]``) because
        Python's ``Awaitable`` is the natural effect-monad type class —
        any ``async def`` returns one, and ``ComposableFuture`` is one.
        """

        async def _flat_mapped():
            return await fn(await self._resolve())

        return self._wrap(_flat_mapped())

    # -- Applicative / Product ----------------------------------------

    def zip(self, other: ComposableFuture[U]) -> ComposableFuture[tuple[T, U]]:
        """Product.  Run two futures concurrently, pair results.

        Cancel-on-failure: if either side raises, the other is cancelled
        and awaited (ensuring cleanup like actor stop+join), then the
        original exception propagates.
        """

        async def _zipped():
            tasks = [asyncio.ensure_future(self._resolve()), asyncio.ensure_future(other._resolve())]
            try:
                a, b = await asyncio.gather(*tasks)
                return (a, b)
            except Exception:
                await _cancel_and_wait(tasks)
                raise

        return self._wrap(_zipped())

    def ap(self, fn_future: ComposableFuture[Callable[[T], U]]) -> ComposableFuture[U]:
        """Applicative apply.  ``F[A] → F[A → B] → F[B]``

        Derived: ``fn_future.zip(self).map(λ (f, a) → f(a))``
        """
        return fn_future.zip(self).map(lambda pair: pair[0](pair[1]))

    # -- MonadError ---------------------------------------------------

    def recover(self, fn: Callable[[Exception], T]) -> ComposableFuture[T]:
        """Handle exceptions synchronously.  MonadError ``handleError``."""

        async def _recovered():
            try:
                return await self._resolve()
            except Exception as e:
                return fn(e)

        return self._wrap(_recovered())

    def recover_with(self, fn: Callable[[Exception], Awaitable[T]]) -> ComposableFuture[T]:
        """Handle exceptions with async recovery.  MonadError ``handleErrorWith``."""

        async def _recovered():
            try:
                return await self._resolve()
            except Exception as e:
                return await fn(e)

        return self._wrap(_recovered())

    # =================================================================
    #  DERIVED — Composed from primitives
    # =================================================================

    def filter(self, predicate: Callable[[T], bool]) -> ComposableFuture[T]:
        """MonadFilter — keep result only if predicate holds.

        ``map(a → a if p(a) else raise ValueError)``
        """

        async def _filtered():
            result = await self._resolve()
            if not predicate(result):
                raise ValueError(f"ComposableFuture.filter predicate failed for {result!r}")
            return result

        return self._wrap(_filtered())

    def transform(
        self,
        success: Callable[[T], U],
        failure: Callable[[Exception], U],
    ) -> ComposableFuture[U]:
        """Bifunctor-like — transform both success and failure paths."""

        async def _transformed():
            try:
                return success(await self._resolve())
            except Exception as e:
                return failure(e)

        return self._wrap(_transformed())

    def fallback_to(self, other: Callable[[], Awaitable[T]]) -> ComposableFuture[T]:
        """Alternative ``orElse`` — on failure, try fallback factory.

        Takes a zero-arg callable to avoid unawaited coroutine warnings.
        """

        async def _fallback():
            try:
                return await self._resolve()
            except Exception:
                return await other()

        return self._wrap(_fallback())

    # -- Side effects -------------------------------------------------

    def and_then(self, fn: Callable[[T], Any]) -> ComposableFuture[T]:
        """Side effect on success (logging, metrics). Returns original value."""

        async def _tapped():
            result = await self._resolve()
            fn(result)
            return result

        return self._wrap(_tapped())

    def on_complete(
        self,
        on_success: Callable[[T], Any] | None = None,
        on_failure: Callable[[Exception], Any] | None = None,
    ) -> ComposableFuture[T]:
        """Attach callbacks for side effects. Returns original result/error."""

        async def _observed():
            try:
                result = await self._resolve()
            except Exception as e:
                if on_failure is not None:
                    on_failure(e)
                raise
            if on_success is not None:
                on_success(result)
            return result

        return self._wrap(_observed())

    # -- Timeout ------------------------------------------------------

    def with_timeout(self, seconds: float) -> ComposableFuture[T]:
        """Fail with ``TimeoutError`` if not complete within *seconds*."""

        async def _timed():
            with anyio.fail_after(seconds):
                return await self._resolve()

        return self._wrap(_timed())

    # =================================================================
    #  CONSTRUCTORS — Monad return, MonadError raise, Traverse
    # =================================================================

    @staticmethod
    def of(value: T) -> ComposableFuture[T]:
        """Monad return / pure.  Lift a value into an already-resolved future."""
        cf: ComposableFuture[T] = object.__new__(ComposableFuture)
        cf._coro = None
        cf._outcome = Success(value)
        cf._resolving = None
        return cf

    @staticmethod
    def from_executor(
        executor: Any,
        fn: Callable[..., T],
        *args: Any,
    ) -> ComposableFuture[T]:
        """Run a blocking function in *executor*, return ComposableFuture.

        Used by Dispatcher to offload sync handler execution::

            cf = ComposableFuture.from_executor(dispatcher.executor(), actor.receive, msg)
            result = await cf
        """
        async def _run() -> T:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(executor, fn, *args)

        return ComposableFuture(_run())

    @staticmethod
    def from_blocking(fn: Callable[..., T], *args: Any) -> ComposableFuture[T]:
        """Run a blocking function in the default thread pool.

        Convenience for sync operations that don't need a specific executor::

            cf = ComposableFuture.from_blocking(requests.get, url)
            result = await cf.map(lambda r: r.json())
        """
        async def _run() -> T:
            return await anyio.to_thread.run_sync(lambda: fn(*args))

        return ComposableFuture(_run())

    @staticmethod
    def promise() -> tuple[ComposableFuture[T], Callable[[T], None], Callable[[Exception], None]]:
        """Promise pattern — create a settable future.

        Returns ``(future, resolve, reject)`` where resolve/reject are
        **thread-safe**: same-loop calls set directly, cross-loop calls
        go through ``call_soon_threadsafe``.

        Used by ReplyRegistry for ask() correlation.
        """
        owner_loop = asyncio.get_running_loop()
        internal: asyncio.Future[Any] = owner_loop.create_future()

        def _settle(fn: Callable[[Any], None], value: Any) -> None:
            if internal.done():
                return
            try:
                current = asyncio.get_running_loop()
            except RuntimeError:
                current = None
            if current is owner_loop:
                fn(value)
            else:
                owner_loop.call_soon_threadsafe(fn, value)

        def _resolve_fn(value: Any) -> None:
            _settle(internal.set_result, value)

        def _reject_fn(error: Exception) -> None:
            _settle(internal.set_exception, error)

        return ComposableFuture(internal), _resolve_fn, _reject_fn

    @staticmethod
    def successful(value: T) -> ComposableFuture[T]:
        """Alias for ``of`` (Scala naming convention)."""
        return ComposableFuture.of(value)

    @staticmethod
    def failed(error: Exception) -> ComposableFuture[Any]:
        """MonadError ``raiseError``.  Already-failed future."""
        cf: ComposableFuture[Any] = object.__new__(ComposableFuture)
        cf._coro = None
        cf._outcome = Failure(error)
        cf._resolving = None
        return cf

    @staticmethod
    def sequence(futures: Sequence[ComposableFuture[T]]) -> ComposableFuture[list[T]]:
        """Traverse with identity — run all concurrently, collect results.

        Cancel-on-failure: if any future raises, all others are cancelled
        and awaited (ensuring cleanup), then the first exception propagates.
        """

        async def _sequenced():
            tasks = [asyncio.ensure_future(f._resolve()) for f in futures]
            try:
                return list(await asyncio.gather(*tasks))
            except Exception:
                await _cancel_and_wait(tasks)
                raise

        return ComposableFuture(_sequenced())

    @staticmethod
    def first_completed(
        *futures: ComposableFuture[T],
        cancel_pending: bool = True,
    ) -> ComposableFuture[T]:
        """Alternative ``race`` — return the first future to complete.

        Prefers success over failure: if both succeed and fail in the same
        scheduling tick, the successful result wins.

        By default, losers are cancelled.  Pass ``cancel_pending=False``
        for idempotent/read-only branches.
        """

        async def _first():
            if not futures:
                raise ValueError("first_completed requires at least one future")

            async def _await_one(f: ComposableFuture[T]) -> Success[T] | Failure:
                try:
                    return Success(await f)
                except Exception as e:
                    return Failure(e)

            def _suppress_exception(t: asyncio.Task[Any]) -> None:
                if not t.cancelled() and t.exception() is not None:
                    pass  # consumed

            tasks = [asyncio.ensure_future(_await_one(f)) for f in futures]

            first_exc: Exception | None = None
            pending = set(tasks)

            # Keep waiting until we find a success or all tasks are done.
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for t in done:
                    if t.cancelled():
                        continue
                    outcome = t.result()  # never raises — exceptions caught in _await_one
                    if isinstance(outcome, Failure):
                        if first_exc is None:
                            first_exc = outcome.error
                        continue
                    # Found a success — cancel remaining and return
                    if cancel_pending:
                        for p in pending:
                            p.cancel()
                        if pending:
                            await asyncio.gather(*pending, return_exceptions=True)
                    else:
                        for p in pending:
                            p.add_done_callback(_suppress_exception)
                    return outcome.value

            # All tasks completed without success
            if first_exc is not None:
                raise first_exc
            raise asyncio.CancelledError()

        return ComposableFuture(_first())

    @staticmethod
    def race(*futures: ComposableFuture[T]) -> ComposableFuture[T]:
        """First-completed-wins — return whatever finishes first.

        Unlike ``first_completed`` (success-biased), ``race`` propagates
        the first outcome regardless of success or failure.  Remaining
        futures are cancelled and awaited for cleanup.
        """

        async def _race():
            if not futures:
                raise ValueError("race requires at least one future")
            tasks = [asyncio.ensure_future(f._resolve()) for f in futures]
            try:
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                await _cancel_and_wait(list(pending))
                return done.pop().result()
            except Exception:
                await _cancel_and_wait(tasks)
                raise

        return ComposableFuture(_race())

    @staticmethod
    def join_all(*futures: ComposableFuture[T]) -> ComposableFuture[list[T | BaseException]]:
        """Wait for all futures, absorbing failures (including CancelledError).

        Unlike ``sequence`` (cancel-on-failure), ``join_all`` waits for
        every future to settle.  Each result is either the resolved value
        or the raised exception.  Used by shutdown paths where all actors
        must finish regardless of individual failures.
        """

        async def _join():
            return list(
                await asyncio.gather(
                    *[asyncio.ensure_future(f._resolve()) for f in futures],
                    return_exceptions=True,
                )
            )

        return ComposableFuture(_join())

    # =================================================================
    #  RUNTIME PRIMITIVES — async infrastructure via CF
    # =================================================================

    @staticmethod
    def sleep(seconds: float) -> ComposableFuture[None]:
        """Async sleep wrapped as ComposableFuture."""
        async def _sleep() -> None:
            await anyio.sleep(seconds)
        return ComposableFuture(_sleep())


    def shield(self) -> ComposableFuture[T]:
        """Protect from cancellation.

        The inner computation continues even if the outer task is cancelled.
        """
        async def _shielded() -> T:
            with anyio.CancelScope(shield=True):
                return await self._resolve()
        return self._wrap(_shielded())

    @staticmethod
    def eager(
        coro: Coroutine[Any, Any, T], *, name: str | None = None,
    ) -> tuple[ComposableFuture[T], Callable[[], None]]:
        """Start a coroutine eagerly as a background task.

        Returns ``(future, cancel)`` — the future for join/await,
        the cancel callable to interrupt the running task.

        Used by the actor runtime for long-lived message loops.
        """
        task = asyncio.create_task(coro, name=name)

        def _cancel() -> None:
            task.cancel()

        return ComposableFuture(task), _cancel

