"""Tests for Dispatcher abstraction — loop assignment for actors."""

import asyncio
import threading
import time

import pytest

from everything_is_an_actor.core.dispatcher import DefaultDispatcher, PoolDispatcher
from everything_is_an_actor.core.mailbox import FastMailbox


class TestDefaultDispatcher:
    @pytest.mark.anyio
    async def test_returns_current_loop(self):
        d = DefaultDispatcher()
        await d.start()
        loop = d.assign(object, "test")
        assert loop is asyncio.get_running_loop()
        await d.shutdown()

    @pytest.mark.anyio
    async def test_always_same_loop(self):
        d = DefaultDispatcher()
        await d.start()
        loops = [d.assign(object, f"a{i}") for i in range(10)]
        assert all(l is loops[0] for l in loops)
        await d.shutdown()


class TestPoolDispatcher:
    @pytest.mark.anyio
    async def test_creates_worker_loops(self):
        d = PoolDispatcher(pool_size=2)
        await d.start()
        loop1 = d.assign(object, "a1")
        loop2 = d.assign(object, "a2")
        current = asyncio.get_running_loop()
        assert loop1 is not current
        assert loop2 is not current
        assert loop1 is not loop2
        await d.shutdown()

    @pytest.mark.anyio
    async def test_round_robin(self):
        d = PoolDispatcher(pool_size=2)
        await d.start()
        loops = [d.assign(object, f"a{i}") for i in range(4)]
        # a0→worker0, a1→worker1, a2→worker0, a3→worker1
        assert loops[0] is loops[2]
        assert loops[1] is loops[3]
        assert loops[0] is not loops[1]
        await d.shutdown()

    @pytest.mark.anyio
    async def test_shutdown_stops_threads(self):
        d = PoolDispatcher(pool_size=2)
        await d.start()
        threads = [w.thread for w in d._workers]
        assert all(t.is_alive() for t in threads)
        await d.shutdown()
        for t in threads:
            t.join(timeout=2.0)
        assert all(not t.is_alive() for t in threads)

    @pytest.mark.anyio
    async def test_pool_size_1(self):
        d = PoolDispatcher(pool_size=1)
        await d.start()
        loop = d.assign(object, "a")
        assert loop is not asyncio.get_running_loop()
        assert loop.is_running()
        await d.shutdown()

    def test_pool_size_zero_raises(self):
        with pytest.raises(ValueError, match="pool_size must be >= 1"):
            PoolDispatcher(pool_size=0)

    @pytest.mark.anyio
    async def test_assign_before_start_raises(self):
        d = PoolDispatcher(pool_size=1)
        with pytest.raises(RuntimeError, match="not started"):
            d.assign(object, "a")


class TestFastMailboxCrossLoop:
    @pytest.mark.anyio
    async def test_put_nowait_from_other_thread_wakes_consumer(self):
        """put_nowait from another thread should wake get() on the consumer loop."""
        current_loop = asyncio.get_running_loop()
        mbox = FastMailbox(maxsize=16, target_loop=current_loop)

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

    @pytest.mark.anyio
    async def test_same_loop_still_works(self):
        mbox = FastMailbox(maxsize=16, target_loop=asyncio.get_running_loop())
        mbox.put_nowait("msg1")
        result = await mbox.get()
        assert result == "msg1"

    @pytest.mark.anyio
    async def test_no_target_loop_backward_compatible(self):
        mbox = FastMailbox(maxsize=16)
        mbox.put_nowait("msg1")
        result = await mbox.get()
        assert result == "msg1"

    @pytest.mark.anyio
    async def test_multiple_cross_loop_puts(self):
        """Multiple puts from another thread all get delivered."""
        current_loop = asyncio.get_running_loop()
        mbox = FastMailbox(maxsize=64, target_loop=current_loop)

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


# ---------------------------------------------------------------------------
# Integration tests — Dispatcher + ActorSystem
# ---------------------------------------------------------------------------
from everything_is_an_actor import Actor, ActorSystem
from everything_is_an_actor.agents.agent_actor import AgentActor
from everything_is_an_actor.agents.task import Task, TaskResult


class TestDispatcherIntegration:
    @pytest.mark.anyio
    async def test_default_dispatcher_same_loop(self):
        class LoopReporter(Actor[str, str]):
            async def on_receive(self, message):
                return str(id(asyncio.get_running_loop()))

        system = ActorSystem("test", dispatcher=DefaultDispatcher())
        ref = await system.spawn(LoopReporter, "reporter")
        result = await system.ask(ref, "which-loop")
        assert result == str(id(asyncio.get_running_loop()))
        await system.shutdown()

    @pytest.mark.anyio
    async def test_pool_dispatcher_different_loop(self):
        class LoopReporter(Actor[str, str]):
            async def on_receive(self, message):
                return str(id(asyncio.get_running_loop()))

        dispatcher = PoolDispatcher(pool_size=2)
        await dispatcher.start()
        system = ActorSystem("test", dispatcher=dispatcher)
        ref = await system.spawn(LoopReporter, "reporter")
        result = await system.ask(ref, "which-loop")
        assert result != str(id(asyncio.get_running_loop()))
        await system.shutdown()
        await dispatcher.shutdown()

    @pytest.mark.anyio
    async def test_pool_dispatcher_ask_reply(self):
        class Echo(Actor[str, str]):
            async def on_receive(self, message):
                return f"echo:{message}"

        dispatcher = PoolDispatcher(pool_size=1)
        await dispatcher.start()
        system = ActorSystem("test", dispatcher=dispatcher)
        ref = await system.spawn(Echo, "echo")
        result = await system.ask(ref, "hello")
        assert result == "echo:hello"
        await system.shutdown()
        await dispatcher.shutdown()

    @pytest.mark.anyio
    async def test_pool_dispatcher_multiple_actors(self):
        class Doubler(Actor[int, int]):
            async def on_receive(self, message):
                return message * 2

        dispatcher = PoolDispatcher(pool_size=2)
        await dispatcher.start()
        system = ActorSystem("test", dispatcher=dispatcher)
        refs = [await system.spawn(Doubler, f"d{i}") for i in range(4)]
        results = await asyncio.gather(*[system.ask(r, i + 1) for i, r in enumerate(refs)])
        assert results == [2, 4, 6, 8]
        await system.shutdown()
        await dispatcher.shutdown()

    @pytest.mark.anyio
    async def test_pool_dispatcher_stop_join(self):
        class SlowActor(Actor[str, str]):
            async def on_receive(self, message):
                await asyncio.sleep(0.1)
                return "done"

        dispatcher = PoolDispatcher(pool_size=1)
        await dispatcher.start()
        system = ActorSystem("test", dispatcher=dispatcher)
        ref = await system.spawn(SlowActor, "slow")
        ref.stop()
        await ref.join()
        assert not ref.is_alive
        await system.shutdown()
        await dispatcher.shutdown()

    @pytest.mark.anyio
    async def test_pool_dispatcher_tell(self):
        class Counter(Actor[str, int]):
            def __init__(self):
                super().__init__()
                self.count = 0

            async def on_receive(self, message):
                if message == "inc":
                    self.count += 1
                    return self.count
                return self.count

        dispatcher = PoolDispatcher(pool_size=1)
        await dispatcher.start()
        system = ActorSystem("test", dispatcher=dispatcher)
        ref = await system.spawn(Counter, "counter")
        await system.tell(ref, "inc")
        await system.tell(ref, "inc")
        await asyncio.sleep(0.05)  # let tells process
        result = await system.ask(ref, "get")
        assert result == 2
        await system.shutdown()
        await dispatcher.shutdown()


class TestNamedDispatchers:
    @pytest.mark.anyio
    async def test_named_dispatchers_basic(self):
        class Echo(Actor[str, str]):
            async def on_receive(self, message):
                return f"echo:{message}"

        system = ActorSystem("test", dispatchers={
            "io": PoolDispatcher(pool_size=1),
        })
        ref = await system.spawn(Echo, "echo", dispatcher="io")
        result = await system.ask(ref, "hello")
        assert result == "echo:hello"
        await system.shutdown()  # auto-shuts dispatcher

    @pytest.mark.anyio
    async def test_named_dispatchers_multiple(self):
        class LoopReporter(Actor[str, str]):
            async def on_receive(self, message):
                return str(id(asyncio.get_running_loop()))

        system = ActorSystem("test", dispatchers={
            "pool-a": PoolDispatcher(pool_size=1),
            "pool-b": PoolDispatcher(pool_size=1),
        })
        ref_a = await system.spawn(LoopReporter, "a", dispatcher="pool-a")
        ref_b = await system.spawn(LoopReporter, "b", dispatcher="pool-b")
        ref_c = await system.spawn(LoopReporter, "c")  # no dispatcher, caller loop

        loop_a = await system.ask(ref_a, "x")
        loop_b = await system.ask(ref_b, "x")
        loop_c = await system.ask(ref_c, "x")
        caller = str(id(asyncio.get_running_loop()))

        # All three on different loops
        assert loop_a != caller
        assert loop_b != caller
        assert loop_c == caller
        assert loop_a != loop_b
        await system.shutdown()

    @pytest.mark.anyio
    async def test_unknown_dispatcher_raises(self):
        system = ActorSystem("test", dispatchers={"io": PoolDispatcher(pool_size=1)})
        with pytest.raises(ValueError, match="Unknown dispatcher 'gpu'"):
            await system.spawn(Actor, "x", dispatcher="gpu")
        await system.shutdown()

    @pytest.mark.anyio
    async def test_lazy_start(self):
        """Dispatcher is started lazily on first spawn, not at system init."""
        d = PoolDispatcher(pool_size=1)
        system = ActorSystem("test", dispatchers={"io": d})
        assert not d._started  # not started yet

        class Echo(Actor[str, str]):
            async def on_receive(self, message):
                return message

        ref = await system.spawn(Echo, "e", dispatcher="io")
        assert d._started  # started on first use
        await system.ask(ref, "hi")
        await system.shutdown()
        assert not d._started  # shutdown cleans up


class TestAutoDispatch:
    @pytest.mark.anyio
    async def test_sync_actor_auto_routes_to_default_pool(self):
        """Sync actor (overrides receive()) auto-routes to 'default' dispatcher."""
        class SyncEcho(Actor[str, str]):
            def receive(self, message: str) -> str:
                return f"sync:{message}"

        system = ActorSystem("test", dispatchers={
            "io": PoolDispatcher(pool_size=1),
        })
        ref = await system.spawn(SyncEcho, "echo")
        result = await system.ask(ref, "hello")
        assert result == "sync:hello"
        # Verify it ran on a different loop (pool dispatcher)
        assert system._dispatchers_started == {"io"}
        await system.shutdown()

    @pytest.mark.anyio
    async def test_async_actor_stays_on_caller_loop(self):
        """Async actor does NOT auto-route, stays on caller loop."""
        class AsyncEcho(Actor[str, str]):
            async def on_receive(self, message: str) -> str:
                return f"async:{message}"

        system = ActorSystem("test", dispatchers={
            "io": PoolDispatcher(pool_size=1),
        })
        ref = await system.spawn(AsyncEcho, "echo")
        result = await system.ask(ref, "hello")
        assert result == "async:hello"
        # io dispatcher NOT started — async actor didn't trigger it
        assert system._dispatchers_started == set()
        await system.shutdown()

    @pytest.mark.anyio
    async def test_explicit_dispatcher_overrides_auto(self):
        """Explicit dispatcher= param overrides auto-detection."""
        class SyncActor(Actor[str, str]):
            def receive(self, message: str) -> str:
                return f"sync:{message}"

        system = ActorSystem("test", dispatchers={
            "io": PoolDispatcher(pool_size=1),
            "special": PoolDispatcher(pool_size=1),
        })
        ref = await system.spawn(SyncActor, "s", dispatcher="special")
        result = await system.ask(ref, "hello")
        assert result == "sync:hello"
        # "special" was started, NOT "io"
        assert "special" in system._dispatchers_started
        assert "io" not in system._dispatchers_started
        await system.shutdown()

    @pytest.mark.anyio
    async def test_no_default_dispatcher_sync_stays_on_caller(self):
        """Without 'default' dispatcher, sync actor stays on caller loop (backward compatible)."""
        class SyncEcho(Actor[str, str]):
            def receive(self, message: str) -> str:
                return f"sync:{message}"

        system = ActorSystem("test")  # no dispatchers at all
        ref = await system.spawn(SyncEcho, "echo")
        result = await system.ask(ref, "hello")
        assert result == "sync:hello"
        await system.shutdown()


class TestDispatcherAgentActor:
    @pytest.mark.anyio
    async def test_agent_actor_on_pool_dispatcher(self):
        class UpperAgent(AgentActor[str, str]):
            async def execute(self, input: str) -> str:
                return input.upper()

        dispatcher = PoolDispatcher(pool_size=1)
        await dispatcher.start()
        system = ActorSystem("test", dispatcher=dispatcher)
        ref = await system.spawn(UpperAgent, "upper")
        result: TaskResult[str] = await system.ask(ref, Task(input="hello"))
        assert result.output == "HELLO"
        await system.shutdown()
        await dispatcher.shutdown()

    @pytest.mark.anyio
    async def test_agent_actor_multiple_asks(self):
        class Reverser(AgentActor[str, str]):
            async def execute(self, input: str) -> str:
                return input[::-1]

        dispatcher = PoolDispatcher(pool_size=2)
        await dispatcher.start()
        system = ActorSystem("test", dispatcher=dispatcher)
        ref = await system.spawn(Reverser, "rev")
        for word in ["hello", "world", "test"]:
            result = await system.ask(ref, Task(input=word))
            assert result.output == word[::-1]
        await system.shutdown()
        await dispatcher.shutdown()
