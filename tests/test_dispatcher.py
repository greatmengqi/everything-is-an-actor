"""Tests for Dispatcher abstraction — offloads handler execution to thread pools."""

import asyncio
import threading
import time

import pytest

from everything_is_an_actor.core.dispatcher import Dispatcher, PoolDispatcher
from everything_is_an_actor.core.composable_future import ComposableFuture
from everything_is_an_actor.core.mailbox import FastMailbox
from everything_is_an_actor import Actor, ActorSystem
from everything_is_an_actor.agents.agent_actor import AgentActor
from everything_is_an_actor.agents.task import Task, TaskResult

pytestmark = pytest.mark.anyio


class TestPoolDispatcher:
    async def test_dispatch_runs_in_thread(self):
        d = PoolDispatcher(pool_size=1)
        await d.start()
        caller_tid = threading.current_thread().ident

        def get_tid():
            return threading.current_thread().ident

        result = await d.dispatch(get_tid)
        assert result != caller_tid
        await d.shutdown()

    async def test_dispatch_returns_value(self):
        d = PoolDispatcher(pool_size=1)
        await d.start()
        result = await d.dispatch(lambda: 42)
        assert result == 42
        await d.shutdown()

    async def test_dispatch_propagates_exception(self):
        d = PoolDispatcher(pool_size=1)
        await d.start()

        def boom():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            await d.dispatch(boom)
        await d.shutdown()

    async def test_concurrent_dispatches(self):
        d = PoolDispatcher(pool_size=4)
        await d.start()

        def slow():
            time.sleep(0.1)
            return threading.current_thread().ident

        t0 = time.perf_counter()
        results = await ComposableFuture.sequence(
            [d.dispatch(slow) for _ in range(4)]
        )
        elapsed = time.perf_counter() - t0

        assert elapsed < 0.3  # parallel, not serial
        assert len(set(results)) > 1  # multiple threads used
        await d.shutdown()

    async def test_shutdown_stops_pool(self):
        d = PoolDispatcher(pool_size=1)
        await d.start()
        await d.dispatch(lambda: "ok")
        await d.shutdown()

        with pytest.raises(RuntimeError, match="not started"):
            await d.dispatch(lambda: "fail")

    def test_pool_size_zero_raises(self):
        with pytest.raises(ValueError, match="pool_size must be >= 1"):
            PoolDispatcher(pool_size=0)

    async def test_dispatch_before_start_raises(self):
        d = PoolDispatcher(pool_size=1)
        with pytest.raises(RuntimeError, match="not started"):
            await d.dispatch(lambda: "fail")


class TestFastMailboxCrossLoop:
    async def test_put_nowait_from_other_thread_wakes_consumer(self):
        """put_nowait from another thread should wake get() on the consumer loop."""
        mbox = FastMailbox(maxsize=16)

        received = []

        async def consumer():
            msg = await asyncio.wait_for(mbox.get(), timeout=2.0)
            received.append(msg)

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.05)

        def producer():
            time.sleep(0.05)
            mbox.put_nowait("hello-from-thread")

        t = threading.Thread(target=producer)
        t.start()
        t.join(timeout=2.0)
        await task
        assert received == ["hello-from-thread"]

    async def test_same_loop_works(self):
        mbox = FastMailbox(maxsize=16)
        mbox.put_nowait("msg1")
        result = await mbox.get()
        assert result == "msg1"

    async def test_multiple_cross_loop_puts(self):
        """Multiple puts from another thread all get delivered."""
        mbox = FastMailbox(maxsize=64)

        N = 20
        received = []

        async def consumer():
            for _ in range(N):
                msg = await asyncio.wait_for(mbox.get(), timeout=2.0)
                received.append(msg)

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.02)

        def producer():
            for i in range(N):
                mbox.put_nowait(f"msg-{i}")
                time.sleep(0.001)

        t = threading.Thread(target=producer)
        t.start()
        t.join(timeout=5.0)
        await task
        assert len(received) == N
        assert received == [f"msg-{i}" for i in range(N)]


class TestNamedDispatchers:
    async def test_named_dispatchers_basic(self):
        # Dispatchers offload blocking sync work — use sync receive().
        class Echo(Actor[str, str]):
            def receive(self, message):
                return f"echo:{message}"

        system = ActorSystem("test", dispatchers={
            "io": PoolDispatcher(pool_size=1),
        })
        ref = await system.spawn(Echo, "echo", dispatcher="io")
        result = await system.ask(ref, "hello")
        assert result == "echo:hello"
        await system.shutdown()

    async def test_unknown_dispatcher_raises(self):
        system = ActorSystem("test", dispatchers={"io": PoolDispatcher(pool_size=1)})
        with pytest.raises(ValueError, match="Unknown dispatcher 'gpu'"):
            await system.spawn(Actor, "x", dispatcher="gpu")
        await system.shutdown()

    async def test_async_handler_with_dispatcher_rejected(self):
        """Async on_receive + dispatcher is a conceptual error — must be rejected at spawn."""
        class AsyncEcho(Actor[str, str]):
            async def on_receive(self, message):
                return message

        system = ActorSystem("test", dispatchers={"io": PoolDispatcher(pool_size=1)})
        with pytest.raises(ValueError, match="async on_receive"):
            await system.spawn(AsyncEcho, "bad", dispatcher="io")
        await system.shutdown()

    async def test_lazy_start(self):
        """Dispatcher is started lazily on first spawn, not at system init."""
        d = PoolDispatcher(pool_size=1)
        system = ActorSystem("test", dispatchers={"io": d})
        assert d._pool is None

        class Echo(Actor[str, str]):
            def receive(self, message):
                return message

        ref = await system.spawn(Echo, "e", dispatcher="io")
        assert d._pool is not None
        await system.ask(ref, "hi")
        await system.shutdown()
        assert d._pool is None


class TestAutoDispatch:
    async def test_sync_actor_runs_in_thread(self):
        """Sync actor (overrides receive()) runs handler in thread pool."""
        class SyncEcho(Actor[str, str]):
            def receive(self, message: str) -> str:
                return f"sync:{message}"

        system = ActorSystem("test")
        ref = await system.spawn(SyncEcho, "echo")
        result = await system.ask(ref, "hello")
        assert result == "sync:hello"
        await system.shutdown()

    async def test_sync_actor_with_dispatcher(self):
        """Sync actor with explicit dispatcher uses that dispatcher."""
        class SyncEcho(Actor[str, str]):
            def receive(self, message: str) -> str:
                return f"sync:{message}"

        system = ActorSystem("test", dispatchers={
            "io": PoolDispatcher(pool_size=1),
        })
        ref = await system.spawn(SyncEcho, "echo", dispatcher="io")
        result = await system.ask(ref, "hello")
        assert result == "sync:hello"
        await system.shutdown()

    async def test_async_actor_stays_on_caller_loop(self):
        """Async actor does NOT auto-route to dispatcher."""
        class AsyncEcho(Actor[str, str]):
            async def on_receive(self, message: str) -> str:
                return f"async:{message}"

        system = ActorSystem("test", dispatchers={
            "io": PoolDispatcher(pool_size=1),
        })
        ref = await system.spawn(AsyncEcho, "echo")
        result = await system.ask(ref, "hello")
        assert result == "async:hello"
        assert system._dispatchers_started == set()
        await system.shutdown()


class TestDispatcherAgentActor:
    async def test_async_agent_actor_with_dispatcher_rejected(self):
        """AgentActor with async execute + dispatcher is rejected at spawn.

        AgentActor's on_receive is framework-managed async, so combining it
        with a dispatcher would route each message through a throwaway event
        loop — breaking ContextVars, pending futures, and sink propagation.
        Users who want AgentActor handlers to offload blocking work should
        override sync ``receive()`` instead of async ``execute()``.
        """
        class UpperAgent(AgentActor[str, str]):
            async def execute(self, input: str) -> str:
                return input.upper()

        system = ActorSystem("test", dispatchers={
            "io": PoolDispatcher(pool_size=1),
        })
        with pytest.raises(ValueError, match="async on_receive"):
            await system.spawn(UpperAgent, "upper", dispatcher="io")
        await system.shutdown()
