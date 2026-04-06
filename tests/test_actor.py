"""Tests for the async Actor framework."""

import asyncio

import pytest

from everything_is_an_actor import (
    Actor,
    ActorRef,
    ActorSystem,
    AllForOneStrategy,
    Directive,
    Middleware,
    OneForOneStrategy,
)
from everything_is_an_actor.core.ref import ActorStoppedError


# ---------------------------------------------------------------------------
# Basic actors for testing
# ---------------------------------------------------------------------------


class EchoActor(Actor):
    async def on_receive(self, message):
        return message


class CounterActor(Actor):
    async def on_started(self):
        self.count = 0

    async def on_receive(self, message):
        if message == "inc":
            self.count += 1
        elif message == "get":
            return self.count


class CrashActor(Actor):
    async def on_receive(self, message):
        if message == "crash":
            raise ValueError("boom")
        return "ok"


class ParentActor(Actor):
    def __init__(self):
        self.child_ref: ActorRef | None = None
        self.restarts = 0

    def supervisor_strategy(self):
        return OneForOneStrategy(max_restarts=3, within_seconds=60)

    async def on_started(self):
        self.child_ref = await self.context.spawn(CrashActor, "child")

    async def on_receive(self, message):
        if message == "get_child":
            return self.child_ref


class StopOnCrashParent(Actor):
    def supervisor_strategy(self):
        return OneForOneStrategy(decider=lambda _: Directive.stop)

    async def on_started(self):
        self.child_ref = await self.context.spawn(CrashActor, "child")

    async def on_receive(self, message):
        if message == "get_child":
            return self.child_ref


class AllForOneParent(Actor):
    def supervisor_strategy(self):
        return AllForOneStrategy(max_restarts=2, within_seconds=60)

    async def on_started(self):
        self.c1 = await self.context.spawn(CounterActor, "c1")
        self.c2 = await self.context.spawn(CrashActor, "c2")

    async def on_receive(self, message):
        if message == "get_children":
            return (self.c1, self.c2)


class LifecycleActor(Actor):
    started = False
    stopped = False
    restarted_with: Exception | None = None

    async def on_started(self):
        LifecycleActor.started = True

    async def on_stopped(self):
        LifecycleActor.stopped = True

    async def on_restart(self, error):
        LifecycleActor.restarted_with = error

    async def on_receive(self, message):
        if message == "crash":
            raise RuntimeError("lifecycle crash")
        return "alive"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBasicMessaging:
    @pytest.mark.anyio
    async def test_tell_and_ask(self):
        system = ActorSystem("test")
        ref = await system.spawn(EchoActor, "echo")
        result = await system.ask(ref, "hello")
        assert result == "hello"
        await system.shutdown()

    @pytest.mark.anyio
    async def test_ask_timeout(self):
        class SlowActor(Actor):
            async def on_receive(self, message):
                await asyncio.sleep(10)

        system = ActorSystem("test")
        ref = await system.spawn(SlowActor, "slow")
        with pytest.raises(asyncio.TimeoutError):
            await system.ask(ref, "hi", timeout=0.1)
        await system.shutdown()

    @pytest.mark.anyio
    async def test_tell_fire_and_forget(self):
        system = ActorSystem("test")
        ref = await system.spawn(CounterActor, "counter")
        await system.tell(ref, "inc")
        await system.tell(ref, "inc")
        await system.tell(ref, "inc")
        # Give the actor time to process
        await asyncio.sleep(0.05)
        count = await system.ask(ref, "get")
        assert count == 3
        await system.shutdown()

    @pytest.mark.anyio
    async def test_ask_stopped_actor(self):
        system = ActorSystem("test")
        ref = await system.spawn(EchoActor, "echo")
        ref.stop()
        await asyncio.sleep(0.05)
        with pytest.raises(ActorStoppedError):
            await system.ask(ref, "hello")
        await system.shutdown()

    @pytest.mark.anyio
    async def test_tell_stopped_actor_goes_to_dead_letters(self):
        system = ActorSystem("test")
        ref = await system.spawn(EchoActor, "echo")
        ref.stop()
        await asyncio.sleep(0.05)
        await system.tell(ref, "orphan")
        assert len(system.dead_letters) >= 1
        await system.shutdown()


class TestActorPath:
    @pytest.mark.anyio
    async def test_root_actor_path(self):
        system = ActorSystem("app")
        ref = await system.spawn(EchoActor, "echo")
        assert ref.path == "/app/echo"
        await system.shutdown()

    @pytest.mark.anyio
    async def test_child_actor_path(self):
        system = ActorSystem("app")
        parent = await system.spawn(ParentActor, "parent")
        child: ActorRef = await system.ask(parent, "get_child")
        assert child.path == "/app/parent/child"
        await system.shutdown()


class TestLifecycle:
    @pytest.mark.anyio
    async def test_on_started_called(self):
        LifecycleActor.started = False
        system = ActorSystem("test")
        await system.spawn(LifecycleActor, "lc")
        assert LifecycleActor.started is True
        await system.shutdown()

    @pytest.mark.anyio
    async def test_on_stopped_called(self):
        LifecycleActor.stopped = False
        system = ActorSystem("test")
        ref = await system.spawn(LifecycleActor, "lc")
        ref.stop()
        await asyncio.sleep(0.1)
        assert LifecycleActor.stopped is True
        await system.shutdown()

    @pytest.mark.anyio
    async def test_shutdown_stops_all(self):
        system = ActorSystem("test")
        r1 = await system.spawn(EchoActor, "a")
        r2 = await system.spawn(EchoActor, "b")
        await system.shutdown()
        assert not r1.is_alive
        assert not r2.is_alive


class TestSupervision:
    @pytest.mark.anyio
    async def test_restart_on_crash(self):
        system = ActorSystem("test")
        parent = await system.spawn(ParentActor, "parent")
        child: ActorRef = await system.ask(parent, "get_child")

        # Crash the child
        with pytest.raises(ValueError, match="boom"):
            await system.ask(child, "crash")
        await asyncio.sleep(0.1)

        # Child should still be alive (restarted)
        assert child.is_alive
        result = await system.ask(child, "safe")
        assert result == "ok"
        await system.shutdown()

    @pytest.mark.anyio
    async def test_stop_directive(self):
        system = ActorSystem("test")
        parent = await system.spawn(StopOnCrashParent, "parent")
        child: ActorRef = await system.ask(parent, "get_child")

        with pytest.raises(ValueError, match="boom"):
            await system.ask(child, "crash")
        await asyncio.sleep(0.1)

        assert not child.is_alive
        await system.shutdown()

    @pytest.mark.anyio
    async def test_restart_limit_exceeded(self):
        system = ActorSystem("test")

        class StrictParent(Actor):
            def supervisor_strategy(self):
                return OneForOneStrategy(max_restarts=2, within_seconds=60)

            async def on_started(self):
                self.child_ref = await self.context.spawn(CrashActor, "child")

            async def on_receive(self, message):
                return self.child_ref

        parent = await system.spawn(StrictParent, "parent")
        child: ActorRef = await system.ask(parent, "any")

        # Exhaust restart limit
        for _ in range(3):
            try:
                await system.ask(child, "crash")
            except (ValueError, ActorStoppedError):
                pass
            await asyncio.sleep(0.05)

        # After exceeding limit, child should be stopped
        assert not child.is_alive
        await system.shutdown()

    @pytest.mark.anyio
    async def test_all_for_one_restarts_siblings(self):
        system = ActorSystem("test")
        parent = await system.spawn(AllForOneParent, "parent")
        c1, c2 = await system.ask(parent, "get_children")

        # Increment counter on c1
        await system.tell(c1, "inc")
        await asyncio.sleep(0.05)
        count_before = await system.ask(c1, "get")
        assert count_before == 1

        # Crash c2 → AllForOne should restart both
        try:
            await system.ask(c2, "crash")
        except ValueError:
            pass
        await asyncio.sleep(0.1)

        # c1 was restarted, counter should be 0
        count_after = await system.ask(c1, "get")
        assert count_after == 0
        await system.shutdown()


class TestDeadLetters:
    @pytest.mark.anyio
    async def test_dead_letter_callback(self):
        received = []
        system = ActorSystem("test")
        system.on_dead_letter(lambda dl: received.append(dl))

        ref = await system.spawn(EchoActor, "echo")
        ref.stop()
        await asyncio.sleep(0.05)
        await system.tell(ref, "orphan")

        assert len(received) >= 1
        assert received[-1].message == "orphan"
        await system.shutdown()


class TestDuplicateNames:
    @pytest.mark.anyio
    async def test_duplicate_root_name_raises(self):
        system = ActorSystem("test")
        await system.spawn(EchoActor, "echo")
        with pytest.raises(ValueError, match="already exists"):
            await system.spawn(EchoActor, "echo")
        await system.shutdown()


# ---------------------------------------------------------------------------
# Middleware tests
# ---------------------------------------------------------------------------


class LogMiddleware(Middleware):
    def __init__(self):
        self.log: list[str] = []

    async def on_receive(self, ctx, message, next_fn):
        self.log.append(f"before:{message}")
        result = await next_fn(ctx, message)
        self.log.append(f"after:{result}")
        return result

    async def on_started(self, actor_ref):
        self.log.append("started")

    async def on_stopped(self, actor_ref):
        self.log.append("stopped")


class TransformMiddleware(Middleware):
    """Uppercases string messages before passing to actor."""

    async def on_receive(self, ctx, message, next_fn):
        if isinstance(message, str):
            message = message.upper()
        return await next_fn(ctx, message)


class TestExecutor:
    @pytest.mark.anyio
    async def test_run_in_executor(self):
        """Blocking function runs in thread pool without blocking event loop."""
        import time

        class BlockingActor(Actor):
            async def on_receive(self, message):
                # Simulate blocking I/O via thread pool
                result = await self.context.run_in_executor(time.sleep, 0.01)
                return "done"

        system = ActorSystem("test")
        ref = await system.spawn(BlockingActor, "blocker")
        result = await system.ask(ref, "go", timeout=5.0)
        assert result == "done"
        await system.shutdown()

    @pytest.mark.anyio
    async def test_concurrent_blocking_calls(self):
        """Multiple actors can run blocking I/O concurrently via shared pool."""
        import time

        class SlowActor(Actor):
            async def on_receive(self, message):
                await self.context.run_in_executor(time.sleep, 0.1)
                return "ok"

        system = ActorSystem("test")
        refs = [await system.spawn(SlowActor, f"s{i}") for i in range(4)]

        start = time.monotonic()
        results = await asyncio.gather(*[system.ask(r, "go", timeout=5.0) for r in refs])
        elapsed = time.monotonic() - start

        assert all(r == "ok" for r in results)
        # 4 parallel × 0.1s should finish in ~0.1-0.2s, not 0.4s
        assert elapsed < 0.3
        await system.shutdown()


class TestMiddleware:
    @pytest.mark.anyio
    async def test_middleware_intercepts_messages(self):
        mw = LogMiddleware()
        system = ActorSystem("test")
        ref = await system.spawn(EchoActor, "echo", middlewares=[mw])
        result = await system.ask(ref, "hello")
        assert result == "hello"
        assert "before:hello" in mw.log
        assert "after:hello" in mw.log
        await system.shutdown()

    @pytest.mark.anyio
    async def test_middleware_lifecycle_hooks(self):
        mw = LogMiddleware()
        system = ActorSystem("test")
        ref = await system.spawn(EchoActor, "echo", middlewares=[mw])
        assert "started" in mw.log
        ref.stop()
        await asyncio.sleep(0.1)
        assert "stopped" in mw.log
        await system.shutdown()

    @pytest.mark.anyio
    async def test_middleware_chain_order(self):
        """First middleware wraps outermost — sees original message."""
        mw1 = LogMiddleware()
        mw2 = TransformMiddleware()
        system = ActorSystem("test")
        # Chain: mw1(mw2(actor)). mw1 logs original, mw2 uppercases, actor echoes
        ref = await system.spawn(EchoActor, "echo", middlewares=[mw1, mw2])
        result = await system.ask(ref, "hello")
        assert result == "HELLO"  # TransformMiddleware uppercased
        assert "before:hello" in mw1.log  # LogMiddleware saw original
        assert "after:HELLO" in mw1.log  # LogMiddleware saw transformed result
        await system.shutdown()

    @pytest.mark.anyio
    async def test_middleware_with_tell(self):
        mw = LogMiddleware()
        system = ActorSystem("test")
        await system.spawn(CounterActor, "counter", middlewares=[mw])
        # tell goes through middleware too
        assert any("before:" in entry for entry in mw.log) is False
        await system.shutdown()

    @pytest.mark.anyio
    async def test_middleware_on_restart_hook(self):
        """on_restart is called on the middleware when a child actor is restarted."""

        class RestartTrackingMiddleware(Middleware):
            def __init__(self):
                self.restart_errors: list[Exception] = []

            async def on_restart(self, actor_ref, error):
                self.restart_errors.append(error)

        mw = RestartTrackingMiddleware()

        class ChildSpawningParent(Actor):
            async def on_receive(self, message):
                if message == "spawn":
                    ref = await self.context.spawn(CrashActor, "child", middlewares=[mw])
                    return ref

        system = ActorSystem("test")
        parent = await system.spawn(ChildSpawningParent, "parent")
        child = await system.ask(parent, "spawn")

        # Crash the child — parent supervisor will restart it
        try:
            await system.ask(child, "crash")
        except ValueError:
            pass
        await asyncio.sleep(0.1)

        assert len(mw.restart_errors) == 1
        assert isinstance(mw.restart_errors[0], ValueError)
        await system.shutdown()


class TestAskErrorPropagation:
    @pytest.mark.anyio
    async def test_ask_propagates_actor_exception(self):
        """ask() re-raises the original exception type when on_receive crashes."""

        class BoomActor(Actor):
            async def on_receive(self, message):
                raise ValueError("intentional crash")

        system = ActorSystem("test")
        ref = await system.spawn(BoomActor, "boom")
        with pytest.raises(ValueError, match="intentional crash"):
            await system.ask(ref, "trigger")
        await system.shutdown()

    @pytest.mark.anyio
    async def test_ask_propagates_exception_while_supervised(self):
        """ask() gets the exception even when the actor is supervised (not stopped)."""

        class SometimesCrashActor(Actor):
            async def on_receive(self, message):
                if message == "crash":
                    raise RuntimeError("supervised crash")
                return "ok"

        system = ActorSystem("test")
        ref = await system.spawn(SometimesCrashActor, "sca")
        with pytest.raises(RuntimeError, match="supervised crash"):
            await system.ask(ref, "crash")
        # Root actor keeps running after a crash (consecutive_failures, not restart)
        result = await system.ask(ref, "hello", timeout=2.0)
        assert result == "ok"
        await system.shutdown()

    @pytest.mark.anyio
    async def test_ask_timeout_late_reply_no_exception(self):
        """Late reply arriving after ask() timeout is silently dropped — no exception, no orphaned future."""

        class SlowActor(Actor):
            async def on_receive(self, message):
                await asyncio.sleep(0.3)
                return "late"

        system = ActorSystem("test")
        ref = await system.spawn(SlowActor, "slow")

        with pytest.raises(asyncio.TimeoutError):
            await system.ask(ref, "go", timeout=0.05)

        # Wait for actor to finish processing — late reply arrives, should be a no-op
        await asyncio.sleep(0.4)
        # System still functional: no orphaned futures, no leaked state
        assert ref.is_alive
        result = await system.ask(ref, "go", timeout=2.0)
        assert result == "late"
        await system.shutdown()
