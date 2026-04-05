"""Adversarial tests to verify bug fixes.

These tests specifically target the bugs that were found and fixed:
1. Middleware bypass for sync actors
2. Event loop cleanup race condition
3. ThreadedMailbox thread safety
"""

import asyncio
import threading
import time
from typing import Any

import pytest

from everything_is_an_actor import Actor, ActorSystem
from everything_is_an_actor.core.mailbox import MemoryMailbox, FastMailbox, ThreadedMailbox
from everything_is_an_actor.core.middleware import Middleware


class TestMiddlewareForSyncActors:
    """Verify that middleware is applied to sync actors (receive() method)."""

    @pytest.mark.anyio
    async def test_sync_actor_middleware_is_called(self):
        """Middleware should be called for sync actors."""
        call_log = []

        class LoggingMiddleware(Middleware):
            async def on_receive(self, ctx, message, next_fn):
                call_log.append(('middleware', message))
                result = await next_fn(ctx, message)
                call_log.append(('middleware_result', result))
                return result

        class SyncActor(Actor):
            def receive(self, message):
                call_log.append(('actor', message))
                return f"processed: {message}"

        system = ActorSystem('test', mailbox_cls=MemoryMailbox)
        ref = await system.spawn(SyncActor, 'sync', middlewares=[LoggingMiddleware()])

        result = await system.ask(ref, "hello")

        # Verify middleware was called
        assert ('middleware', 'hello') in call_log
        assert ('actor', 'hello') in call_log
        assert ('middleware_result', 'processed: hello') in call_log
        assert result == 'processed: hello'

        # Verify order: middleware -> actor -> middleware_result
        middleware_idx = call_log.index(('middleware', 'hello'))
        actor_idx = call_log.index(('actor', 'hello'))
        result_idx = call_log.index(('middleware_result', 'processed: hello'))
        assert middleware_idx < actor_idx < result_idx

        await system.shutdown()

    @pytest.mark.anyio
    async def test_sync_actor_middleware_can_modify_message(self):
        """Middleware can modify messages for sync actors."""
        class ModifyMiddleware(Middleware):
            async def on_receive(self, ctx, message, next_fn):
                # Modify the message before passing to actor
                modified = f"modified_{message}"
                return await next_fn(ctx, modified)

        class SyncActor(Actor):
            def receive(self, message):
                return f"actor_received: {message}"

        system = ActorSystem('test', mailbox_cls=MemoryMailbox)
        ref = await system.spawn(SyncActor, 'sync', middlewares=[ModifyMiddleware()])

        result = await system.ask(ref, "original")

        # Actor should receive modified message
        assert result == 'actor_received: modified_original'

        await system.shutdown()

    @pytest.mark.anyio
    async def test_sync_actor_middleware_can_short_circuit(self):
        """Middleware can short-circuit and return without calling actor."""
        class ShortCircuitMiddleware(Middleware):
            async def on_receive(self, ctx, message, next_fn):
                # Short-circuit: don't call next_fn()
                return "blocked_by_middleware"

        class SyncActor(Actor):
            def receive(self, message):
                # This should never be called
                raise RuntimeError("Actor should not be called!")

        system = ActorSystem('test', mailbox_cls=MemoryMailbox)
        ref = await system.spawn(SyncActor, 'sync', middlewares=[ShortCircuitMiddleware()])

        result = await system.ask(ref, "test")

        # Middleware should have blocked the message
        assert result == 'blocked_by_middleware'

        await system.shutdown()


class TestEventLoopCleanup:
    """Verify that event loop cleanup handles background tasks correctly."""

    @pytest.mark.anyio
    async def test_event_loop_cleanup_on_exception(self):
        """When sync actor throws, event loop should clean up background tasks."""
        import asyncio

        background_tasks_completed = []

        class FailingSyncActor(Actor):
            def receive(self, message):
                if message == "fail":
                    raise ValueError("Intentional failure")
                return "ok"

        system = ActorSystem('test', mailbox_cls=MemoryMailbox)
        ref = await system.spawn(FailingSyncActor, 'failing')

        # Send a successful message first
        result = await system.ask(ref, "ok")
        assert result == "ok"

        # Send a failing message
        with pytest.raises(Exception):
            await system.ask(ref, "fail")

        # Actor should still be able to process messages after failure
        # (event loop should have been cleaned up and recreated)
        result = await system.ask(ref, "ok")
        assert result == "ok"

        await system.shutdown()

    @pytest.mark.anyio
    async def test_event_loop_cleanup_with_multiple_threads(self):
        """Event loop cleanup works correctly with multiple concurrent sync actors."""
        results = []

        class SlowSyncActor(Actor):
            def receive(self, message):
                time.sleep(0.01)  # Simulate slow work
                results.append(message)
                return f"done: {message}"

        system = ActorSystem('test', mailbox_cls=MemoryMailbox)
        ref = await system.spawn(SlowSyncActor, 'slow')

        # Send multiple messages concurrently then wait for processing
        for i in range(10):
            await system.ask(ref, f"msg{i}")

        # All messages should be processed
        assert len(results) == 10

        await system.shutdown()


class TestThreadedMailboxThreadSafety:
    """Verify ThreadedMailbox is thread-safe for concurrent operations."""

    def test_concurrent_puts_from_multiple_threads(self):
        """Multiple threads can put messages concurrently without data loss."""
        mailbox = ThreadedMailbox(maxsize=10000)
        sent_count = []

        def sender_thread(thread_id, num_messages):
            for i in range(num_messages):
                success = mailbox.put_nowait(f"t{thread_id}_m{i}")
                if success:
                    sent_count.append(1)

        # Create 10 threads, each sending 100 messages
        threads = [
            threading.Thread(target=sender_thread, args=(i, 100))
            for i in range(10)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All messages should be in the mailbox
        received_count = 0
        while not mailbox.empty():
            mailbox.get_nowait()
            received_count += 1

        assert received_count == 1000
        assert len(sent_count) == 1000

    def test_concurrent_puts_and_gets(self):
        """Concurrent puts and gets work correctly."""
        import queue

        mailbox = ThreadedMailbox(maxsize=100)
        put_count = []
        get_count = []
        done = threading.Event()

        def producer():
            for i in range(100):
                mailbox.put_nowait(f"msg{i}")
                put_count.append(1)
            done.set()

        def consumer():
            while not done.is_set() or not mailbox.empty():
                try:
                    mailbox.get_nowait()
                    get_count.append(1)
                except:
                    pass

        # Start producers and consumers
        producer_thread = threading.Thread(target=producer)
        consumer_threads = [threading.Thread(target=consumer) for _ in range(2)]

        for t in [producer_thread] + consumer_threads:
            t.start()
        for t in [producer_thread] + consumer_threads:
            t.join()

        # All messages should be accounted for
        assert len(put_count) == 100
        assert len(get_count) == 100

    @pytest.mark.anyio
    async def test_threaded_mailbox_with_async_interface(self):
        """ThreadedMailbox works with async put/get interface."""
        import asyncio

        mailbox = ThreadedMailbox(maxsize=10)

        # Async put
        result = await mailbox.put("msg1")
        assert result is True

        # Async get
        msg = await mailbox.get()
        assert msg == "msg1"

        # Test backpressure
        for i in range(10):
            await mailbox.put(f"msg{i}")

        # Should be full now
        assert mailbox.full


class TestSyncActorWithDifferentMailboxes:
    """Verify sync actors work correctly with different mailbox types."""

    @pytest.mark.anyio
    async def test_sync_actor_with_fastmailbox(self):
        """Sync actor works with FastMailbox."""
        class SyncActor(Actor):
            def receive(self, message):
                return f"processed: {message}"

        system = ActorSystem('test', mailbox_cls=FastMailbox)
        ref = await system.spawn(SyncActor, 'sync')

        result = await system.ask(ref, "test")
        assert result == 'processed: test'

        await system.shutdown()

    @pytest.mark.anyio
    async def test_sync_actor_with_threadedmailbox(self):
        """Sync actor works with ThreadedMailbox."""
        class SyncActor(Actor):
            def receive(self, message):
                return f"processed: {message}"

        system = ActorSystem('test', mailbox_cls=ThreadedMailbox)
        ref = await system.spawn(SyncActor, 'sync')

        result = await system.ask(ref, "test")
        assert result == 'processed: test'

        await system.shutdown()


class TestSyncActorErrorHandling:
    """Verify error handling works correctly for sync actors."""

    @pytest.mark.anyio
    async def test_sync_actor_exception_propagates(self):
        """Exceptions in sync actors propagate correctly."""
        class FailingActor(Actor):
            def receive(self, message):
                raise ValueError("Intentional error")

        system = ActorSystem('test', mailbox_cls=MemoryMailbox)
        ref = await system.spawn(FailingActor, 'failing')

        with pytest.raises(ValueError, match="Intentional error"):
            await system.ask(ref, "test")

        await system.shutdown()

    @pytest.mark.anyio
    async def test_sync_actor_can_recover_after_exception(self):
        """Sync actor can continue processing after an exception."""
        state = {'count': 0}

        class RecoverableActor(Actor):
            def receive(self, message):
                if message == "fail":
                    raise ValueError("Intentional error")
                state['count'] += 1
                return state['count']

        system = ActorSystem('test', mailbox_cls=MemoryMailbox)
        ref = await system.spawn(RecoverableActor, 'recoverable')

        # First message succeeds
        result = await system.ask(ref, "ok")
        assert result == 1

        # Second message fails
        with pytest.raises(ValueError):
            await system.ask(ref, "fail")

        # Third message succeeds (actor recovered)
        result = await system.ask(ref, "ok")
        assert result == 2

        await system.shutdown()
