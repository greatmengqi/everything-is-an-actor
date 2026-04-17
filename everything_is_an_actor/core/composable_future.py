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
from dataclasses import dataclass
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

    __slots__ = ("_coro", "_task", "_owner_loop", "_outcome")

    def __init__(self, coro_or_task: Awaitable[T]) -> None:
        """Eager (in async context) / lazy-fallback (in sync context) constructor.

        - In an async context (``get_running_loop()`` succeeds), the coroutine
          is scheduled immediately as an ``asyncio.Task``. This matches Scala
          ``Future`` semantics: the effect starts now, awaiters observe an
          in-flight computation.
        - In a sync context (no running loop), the coroutine is retained
          and scheduled on first ``await`` / ``result()``. This keeps
          ``ComposableFuture(coro).map(f).result()`` working when no loop
          is running yet.

        Unawaited chains built inside an async context leak no "coroutine was
        never awaited" warnings because the coroutine is owned by a Task.
        """
        self._outcome: Try[T] | None = None
        self._coro: Awaitable[T] | None = None
        self._task: asyncio.Future[T] | None = None
        self._owner_loop: asyncio.AbstractEventLoop | None = None
        if isinstance(coro_or_task, (asyncio.Task, asyncio.Future)):
            self._task = coro_or_task
            self._owner_loop = coro_or_task.get_loop()
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Sync context — keep coroutine until first resolve/result.
            self._coro = coro_or_task
            return
        self._task = asyncio.ensure_future(coro_or_task, loop=loop)
        self._owner_loop = loop

    def _wrap_deferred(self, factory: Callable[[], Awaitable[U]]) -> ComposableFuture[U]:
        """Build a child future from a zero-arg coroutine factory.

        In an async context, the coroutine is scheduled immediately. In a
        sync context (no loop), it is retained as a lazy thunk and resolved
        on first use — necessary to keep ``of(x).map(f).result()`` working
        from plain threads.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            cf: ComposableFuture[U] = object.__new__(ComposableFuture)
            cf._outcome = None
            cf._coro = factory()
            cf._task = None
            cf._owner_loop = None
            return cf
        return ComposableFuture(factory())

    async def _resolve(self) -> T:
        """Await the underlying task, caching the outcome as ``Try[T]``.

        ``asyncio.Task`` is resolve-once by construction and supports
        concurrent awaiters natively — no barrier required. The ``_outcome``
        cache is purely a fast-path for ``result()`` and repeated awaits.

        If constructed in sync context (``_task is None`` but ``_coro`` set),
        schedule now on the current running loop.

        Cross-loop contract: the underlying ``_task`` is bound to
        ``self._owner_loop``. Awaiting from a different loop is rejected
        with a clear error rather than asyncio's generic "attached to a
        different loop" — this surfaces the contract enforced by
        ``promise()``: resolve/reject are cross-loop safe, but **awaiting
        must happen on the owner loop**.
        """
        if self._outcome is not None:
            return self._outcome.get()
        if self._task is None and self._coro is not None:
            loop = asyncio.get_running_loop()
            self._task = asyncio.ensure_future(self._coro, loop=loop)
            self._coro = None
            self._owner_loop = loop
        if self._owner_loop is not None:
            current = asyncio.get_running_loop()
            if current is not self._owner_loop:
                raise RuntimeError(
                    "ComposableFuture awaited from a different event loop than the one "
                    "it was scheduled on. Cross-loop await is not supported — use "
                    "ComposableFuture.promise() + resolve/reject (cross-loop safe) to "
                    "signal between loops, or await on the owner loop."
                )
        try:
            result = await self._task  # type: ignore[misc]
            self._outcome = Success(result)
            return result
        except Exception as e:
            self._outcome = Failure(e)
            raise

    # =================================================================
    #  EXECUTION — await / blocking
    # =================================================================

    def __await__(self):
        return self._resolve().__await__()

    def result(self, timeout: float | None = None) -> T:
        """Blocking get — must be called from a non-async thread.

        Bridges back to the future's owner loop via
        ``run_coroutine_threadsafe`` so tasks, futures, and contextvars
        created on the owner loop remain valid. Previously this used
        ``asyncio.run()`` which created a fresh loop — unsafe whenever the
        CF referenced any cross-loop object.
        """
        if self._outcome is not None:
            return self._outcome.get()

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass  # sync thread — OK
        else:
            raise RuntimeError(
                "ComposableFuture.result() called from within a running event loop. Use 'await' instead."
            )

        if self._owner_loop is None:
            # Lazy CF built in sync context (no task scheduled yet).
            # Spin up a throwaway loop to run the retained coroutine.
            if timeout is not None:

                async def _timed() -> T:
                    with anyio.fail_after(timeout):
                        return await self._resolve()

                return asyncio.run(_timed())
            return asyncio.run(self._resolve())

        import concurrent.futures as _cf

        handle = asyncio.run_coroutine_threadsafe(self._resolve(), self._owner_loop)
        try:
            return handle.result(timeout)
        except _cf.TimeoutError as e:
            handle.cancel()
            raise TimeoutError(str(e)) from e

    # =================================================================
    #  PRIMITIVES — Categorical basis
    # =================================================================

    # -- Functor ------------------------------------------------------

    def map(self, fn: Callable[[T], U]) -> ComposableFuture[U]:
        """Functor fmap.  ``(A → B) → F[A] → F[B]``"""

        async def _mapped() -> U:
            return fn(await self._resolve())

        return self._wrap_deferred(_mapped)

    # -- Monad --------------------------------------------------------

    def flat_map(self, fn: Callable[[T], Awaitable[U]]) -> ComposableFuture[U]:
        """Monad bind (>>=).  ``F[A] → (A → F[B]) → F[B]``

        Accepts ``Awaitable[U]`` (not just ``ComposableFuture[U]``) because
        Python's ``Awaitable`` is the natural effect-monad type class —
        any ``async def`` returns one, and ``ComposableFuture`` is one.
        """

        async def _flat_mapped() -> U:
            return await fn(await self._resolve())

        return self._wrap_deferred(_flat_mapped)

    # -- Applicative / Product ----------------------------------------

    def zip(self, other: ComposableFuture[U]) -> ComposableFuture[tuple[T, U]]:
        """Product.  Run two futures concurrently, pair results.

        Cancel-on-failure: if either side raises, the other is cancelled
        and awaited (ensuring cleanup like actor stop+join), then the
        original exception propagates.
        """

        async def _zipped() -> tuple[T, U]:
            tasks = [asyncio.ensure_future(self._resolve()), asyncio.ensure_future(other._resolve())]
            try:
                a, b = await asyncio.gather(*tasks)
                return (a, b)
            except Exception:
                await _cancel_and_wait(tasks)
                raise

        return self._wrap_deferred(_zipped)

    def ap(self, fn_future: ComposableFuture[Callable[[T], U]]) -> ComposableFuture[U]:
        """Applicative apply.  ``F[A] → F[A → B] → F[B]``

        Derived: ``fn_future.zip(self).map(λ (f, a) → f(a))``
        """
        return fn_future.zip(self).map(lambda pair: pair[0](pair[1]))

    # -- MonadError ---------------------------------------------------

    def recover(self, fn: Callable[[Exception], T]) -> ComposableFuture[T]:
        """Handle exceptions synchronously.  MonadError ``handleError``."""

        async def _recovered() -> T:
            try:
                return await self._resolve()
            except Exception as e:
                return fn(e)

        return self._wrap_deferred(_recovered)

    def recover_with(self, fn: Callable[[Exception], Awaitable[T]]) -> ComposableFuture[T]:
        """Handle exceptions with async recovery.  MonadError ``handleErrorWith``."""

        async def _recovered() -> T:
            try:
                return await self._resolve()
            except Exception as e:
                return await fn(e)

        return self._wrap_deferred(_recovered)

    # =================================================================
    #  DERIVED — Composed from primitives
    # =================================================================

    def filter(self, predicate: Callable[[T], bool]) -> ComposableFuture[T]:
        """MonadFilter — keep result only if predicate holds.

        ``map(a → a if p(a) else raise ValueError)``
        """

        async def _filtered() -> T:
            result = await self._resolve()
            if not predicate(result):
                raise ValueError(f"ComposableFuture.filter predicate failed for {result!r}")
            return result

        return self._wrap_deferred(_filtered)

    def transform(
        self,
        success: Callable[[T], U],
        failure: Callable[[Exception], U],
    ) -> ComposableFuture[U]:
        """Bifunctor-like — transform both success and failure paths."""

        async def _transformed() -> U:
            try:
                return success(await self._resolve())
            except Exception as e:
                return failure(e)

        return self._wrap_deferred(_transformed)

    def fallback_to(self, other: Callable[[], Awaitable[T]]) -> ComposableFuture[T]:
        """Alternative ``orElse`` — on failure, try fallback factory.

        Takes a zero-arg callable to avoid unawaited coroutine warnings.
        """

        async def _fallback() -> T:
            try:
                return await self._resolve()
            except Exception:
                return await other()

        return self._wrap_deferred(_fallback)

    # -- Side effects -------------------------------------------------

    def and_then(self, fn: Callable[[T], Any]) -> ComposableFuture[T]:
        """Side effect on success (logging, metrics). Returns original value."""

        async def _tapped() -> T:
            result = await self._resolve()
            fn(result)
            return result

        return self._wrap_deferred(_tapped)

    def on_complete(
        self,
        on_success: Callable[[T], Any] | None = None,
        on_failure: Callable[[Exception], Any] | None = None,
    ) -> ComposableFuture[T]:
        """Attach callbacks for side effects. Returns original result/error."""

        async def _observed() -> T:
            try:
                result = await self._resolve()
            except Exception as e:
                if on_failure is not None:
                    on_failure(e)
                raise
            if on_success is not None:
                on_success(result)
            return result

        return self._wrap_deferred(_observed)

    # -- Timeout ------------------------------------------------------

    def with_timeout(self, seconds: float) -> ComposableFuture[T]:
        """Fail with ``TimeoutError`` if not complete within *seconds*."""

        async def _timed() -> T:
            with anyio.fail_after(seconds):
                return await self._resolve()

        return self._wrap_deferred(_timed)

    # =================================================================
    #  CONSTRUCTORS — Monad return, MonadError raise, Traverse
    # =================================================================

    @staticmethod
    def of(value: T) -> ComposableFuture[T]:
        """Monad return / pure.  Lift a value into an already-resolved future."""
        cf: ComposableFuture[T] = object.__new__(ComposableFuture)
        cf._task = None
        cf._owner_loop = None
        cf._outcome = Success(value)
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
        cf._task = None
        cf._owner_loop = None
        cf._outcome = Failure(error)
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

            def _retrieve_exception(t: asyncio.Task[Any]) -> None:
                # Reading ``t.exception()`` marks the exception as retrieved,
                # which is what silences asyncio's "Task exception was never
                # retrieved" warning. The call itself is the side effect —
                # the returned value is intentionally discarded.
                if not t.cancelled():
                    t.exception()

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
                            p.add_done_callback(_retrieve_exception)
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

        return self._wrap_deferred(_shielded)

    @staticmethod
    def eager(
        coro: Coroutine[Any, Any, T],
        *,
        name: str | None = None,
    ) -> "EagerHandle[T]":
        """Start a coroutine eagerly as a background task.

        Returns an ``EagerHandle`` exposing both ``.future`` (for join/await)
        and ``.cancel`` (to interrupt the running task). The handle is also
        tuple-unpackable so ``fut, cancel = ComposableFuture.eager(...)`` keeps
        working. If you genuinely don't need the cancel callable — use
        ``fire_and_forget`` instead; dropping an ``EagerHandle`` on the floor
        drops the interrupt channel.
        """
        task = asyncio.create_task(coro, name=name)

        def _cancel() -> None:
            task.cancel()

        return EagerHandle(ComposableFuture(task), _cancel)

    @staticmethod
    def fire_and_forget(
        coro: Coroutine[Any, Any, T],
        *,
        name: str | None = None,
    ) -> ComposableFuture[T]:
        """Schedule a coroutine as a background task with NO cancel handle.

        This is the explicit "I am giving up interrupt control" API. Use it
        where the current code is ``ComposableFuture.eager(coro, name=...)``
        with the return value discarded — that pattern silently loses the
        cancel callable; this one makes the intent visible.

        If you later need to cancel, reach for ``eager()`` instead.
        """
        task = asyncio.create_task(coro, name=name)
        return ComposableFuture(task)


@dataclass(frozen=True)
class EagerHandle(Generic[T]):
    """Handle returned by ``ComposableFuture.eager``.

    Exposes the scheduled future and a cancel callable as named attributes so
    callers cannot drop the cancel channel by failing to unpack a tuple.
    Iterable for backward compatibility with the old tuple return shape —
    ``fut, cancel = Cf.eager(coro)`` still works.
    """

    future: "ComposableFuture[T]"
    cancel: Callable[[], None]

    def __iter__(self):
        yield self.future
        yield self.cancel

