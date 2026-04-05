"""Composable async future — category-theory-grounded composition over awaitables.

Algebraic structure:

    Functor          map
    Monad            of (pure)  +  flat_map (bind)
    Applicative      zip  +  ap
    MonadError       failed (raise)  +  recover / recover_with
    Traverse         sequence
    Alternative      first_completed (race)

Cross-thread support: when pinned to a target loop via ``on(loop)``,
``await`` automatically bridges via ``run_coroutine_threadsafe``.
Blocking callers can use ``result(timeout)``.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, TypeVar, Generic, Optional, Sequence

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
# Internal — cross-loop Future bridging
# =====================================================================


async def _ensure_coro(awaitable: Awaitable[T]) -> T:
    """Normalize any awaitable into a coroutine safe for cross-loop submission.

    ``run_coroutine_threadsafe`` only accepts coroutine objects, not bare
    Futures/Tasks.  An ``asyncio.Future`` bound to loop-A cannot be directly
    awaited from loop-B — we bridge manually via done-callback chaining.
    """
    if isinstance(awaitable, asyncio.Future):
        try:
            source_loop = awaitable.get_loop()
            current_loop = asyncio.get_running_loop()
            if source_loop is not current_loop:
                proxy: asyncio.Future[T] = current_loop.create_future()

                def _forward(f: asyncio.Future[T]) -> None:
                    if proxy.done():
                        return
                    proxy.remove_done_callback(_backward)
                    if f.cancelled():
                        current_loop.call_soon_threadsafe(proxy.cancel)
                    else:
                        exc = f.exception()
                        if exc is not None:
                            current_loop.call_soon_threadsafe(proxy.set_exception, exc)
                        else:
                            current_loop.call_soon_threadsafe(proxy.set_result, f.result())

                def _backward(p: asyncio.Future[T]) -> None:
                    if p.cancelled() and not awaitable.done():
                        source_loop.call_soon_threadsafe(awaitable.cancel)

                source_loop.call_soon_threadsafe(awaitable.add_done_callback, _forward)
                proxy.add_done_callback(_backward)
                return await proxy
        except (AttributeError, RuntimeError):
            pass
    return await awaitable


# =====================================================================
# ComposableFuture
# =====================================================================


class ComposableFuture(Generic[T]):
    """Category-theory-grounded composable wrapper over any ``Awaitable[T]``.

    Composition is lazy — no work happens until ``await`` or ``result()``.

    Cross-thread::

        cf = ComposableFuture(ref._ask(msg), loop=actor_loop)
        result = cf.map(str.upper).result(timeout=5.0)

    Same-loop (zero overhead)::

        result = await ComposableFuture(ref._ask(msg)).map(f).recover(g)
    """

    __slots__ = ("_coro", "_loop")

    def __init__(
        self,
        coro: Awaitable[T],
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ) -> None:
        self._coro = coro
        self._loop = loop

    def _wrap(self, coro: Awaitable[U]) -> ComposableFuture[U]:
        """Create a child future that inherits the target loop."""
        return ComposableFuture(coro, loop=self._loop)

    async def _resolve(self) -> T:
        """Await the base awaitable, bridging cross-loop Futures if needed."""
        coro = self._coro
        if isinstance(coro, asyncio.Future):
            return await _ensure_coro(coro)
        return await coro

    # =================================================================
    #  EXECUTION — await / blocking / loop pinning
    # =================================================================

    def __await__(self):
        if self._loop is None:
            return self._resolve().__await__()
        return self._cross_loop_await().__await__()

    async def _cross_loop_await(self) -> T:
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None

        if current is self._loop:
            return await _ensure_coro(self._coro)

        coro = _ensure_coro(self._coro)
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)  # type: ignore[arg-type]
        return await asyncio.wrap_future(fut)

    def result(self, timeout: Optional[float] = None) -> T:
        """Blocking get — works from any thread.

        Raises RuntimeError if called from within the pinned event loop
        (use ``await`` instead).
        """
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None

        if self._loop is not None:
            if running is self._loop:
                raise RuntimeError(
                    "ComposableFuture.result() called from the same loop it's pinned to. Use 'await' instead."
                )
            coro = _ensure_coro(self._coro)
            fut = asyncio.run_coroutine_threadsafe(coro, self._loop)  # type: ignore[arg-type]
            return fut.result(timeout=timeout)

        if running is not None:
            raise RuntimeError(
                "ComposableFuture.result() called from within a running event loop. "
                "Use 'await' instead, or pin to a target loop with .on(loop)."
            )
        return asyncio.run(_ensure_coro(self._coro))

    def on(self, loop: asyncio.AbstractEventLoop) -> ComposableFuture[T]:
        """Pin execution to *loop*. Enables cross-thread await and blocking result()."""
        return ComposableFuture(self._coro, loop=loop)

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
        """Product.  Run two futures concurrently, pair results."""

        async def _zipped():
            async def _a() -> T:
                return await self

            async def _b() -> U:
                return await other

            a, b = await asyncio.gather(_a(), _b())
            return (a, b)

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
        """Fail with asyncio.TimeoutError if not complete within *seconds*."""

        async def _timed():
            async def _inner() -> T:
                return await self

            return await asyncio.wait_for(_inner(), timeout=seconds)

        return self._wrap(_timed())

    # =================================================================
    #  CONSTRUCTORS — Monad return, MonadError raise, Traverse
    # =================================================================

    @staticmethod
    def of(value: T) -> ComposableFuture[T]:
        """Monad return / pure.  Lift a value into a resolved future."""

        async def _resolved():
            return value

        return ComposableFuture(_resolved())

    @staticmethod
    def promise(
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ) -> tuple[ComposableFuture[T], Callable[[T], None], Callable[[Exception], None]]:
        """Promise pattern — create a settable future.

        Returns ``(future, resolve, reject)`` where resolve/reject are
        **thread-safe**: same-loop calls set directly, cross-loop calls
        go through ``call_soon_threadsafe``.

        This bridges the gap between ComposableFuture (consumption side)
        and asyncio.Future (settable side), used by ReplyRegistry for
        ask() correlation.
        """
        owner_loop = loop or asyncio.get_running_loop()
        internal: asyncio.Future[Any] = owner_loop.create_future()

        def _resolve_fn(value: Any) -> None:
            if internal.done():
                return
            try:
                current = asyncio.get_running_loop()
            except RuntimeError:
                current = None
            if current is owner_loop:
                internal.set_result(value)
            else:
                owner_loop.call_soon_threadsafe(internal.set_result, value)

        def _reject_fn(error: Exception) -> None:
            if internal.done():
                return
            try:
                current = asyncio.get_running_loop()
            except RuntimeError:
                current = None
            if current is owner_loop:
                internal.set_exception(error)
            else:
                owner_loop.call_soon_threadsafe(internal.set_exception, error)

        return ComposableFuture(internal, loop=owner_loop), _resolve_fn, _reject_fn

    @staticmethod
    def successful(value: T) -> ComposableFuture[T]:
        """Alias for ``of`` (Scala naming convention)."""
        return ComposableFuture.of(value)

    @staticmethod
    def failed(error: Exception) -> ComposableFuture[Any]:
        """MonadError ``raiseError``.  Already-failed future."""

        async def _failed():
            raise error

        return ComposableFuture(_failed())

    @staticmethod
    def sequence(futures: Sequence[ComposableFuture[T]]) -> ComposableFuture[list[T]]:
        """Traverse with identity — run all concurrently, collect results.

        Mixed-loop futures work: each element bridges independently.
        """

        async def _sequenced():
            async def _await_one(f: ComposableFuture[T]) -> T:
                return await f

            return list(await asyncio.gather(*[_await_one(f) for f in futures]))

        return ComposableFuture(_sequenced())

    @staticmethod
    def first_completed(
        *futures: ComposableFuture[T],
        cancel_pending: bool = True,
    ) -> ComposableFuture[T]:
        """Alternative ``race`` — return the first future to complete.

        By default, losers are cancelled.  Pass ``cancel_pending=False``
        for idempotent/read-only branches.
        """

        async def _first():
            if not futures:
                raise ValueError("first_completed requires at least one future")

            async def _await_one(f: ComposableFuture[T]) -> T:
                return await f

            def _suppress_exception(t: asyncio.Task[T]) -> None:
                if not t.cancelled() and t.exception() is not None:
                    pass  # consumed

            tasks = [asyncio.ensure_future(_await_one(f)) for f in futures]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if cancel_pending:
                for p in pending:
                    p.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
            else:
                for p in pending:
                    p.add_done_callback(_suppress_exception)

            winner = None
            first_exc = None
            for t in done:
                if t.cancelled():
                    continue
                exc = t.exception()
                if exc is None:
                    winner = t
                    break
                if first_exc is None:
                    first_exc = t
            if winner is not None:
                return winner.result()
            if first_exc is not None:
                return first_exc.result()  # re-raises
            raise asyncio.CancelledError()

        return ComposableFuture(_first())
