"""Regression tests for backward compatibility and shutdown races."""

import asyncio
import threading
import warnings

import pytest

from everything_is_an_actor import Actor
from everything_is_an_actor.agents.agent_actor import AgentActor
from everything_is_an_actor.agents.system import AgentSystem
from everything_is_an_actor.agents.task import Task
from everything_is_an_actor.mailbox import MailboxClosed, ThreadedMailbox
from everything_is_an_actor.system import ActorSystem


class TestAgentActorBackwardCompatibility:
    """Tests for AgentActor backward compatibility during migration from sync receive() to async execute()."""

    @pytest.mark.anyio
    async def test_sync_receive_emits_deprecation_warning(self):
        """AgentActor with sync receive() emits DeprecationWarning instead of hard error."""

        class LegacyAgent(AgentActor[str, str]):
            def receive(self, msg: str) -> str:
                return f"processed: {msg}"

        # Spawning should emit a DeprecationWarning, not raise TypeError
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            system = ActorSystem("test")
            ref = await system.spawn(LegacyAgent, "legacy")
            ref.stop()
            await ref.join()

            # Should have exactly one DeprecationWarning
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "DEPRECATION" in str(w[0].message)
            assert "sync receive()" in str(w[0].message)
            assert "async execute()" in str(w[0].message)

    @pytest.mark.anyio
    async def test_sync_on_receive_emits_deprecation_warning(self):
        """AgentActor with sync on_receive() emits DeprecationWarning instead of hard error."""

        class LegacyAgent(AgentActor[str, str]):
            def on_receive(self, msg: str) -> str:
                return f"processed: {msg}"

        # Spawning should emit a DeprecationWarning, not raise TypeError
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            system = ActorSystem("test")
            ref = await system.spawn(LegacyAgent, "legacy")
            ref.stop()
            await ref.join()

            # Should have exactly one DeprecationWarning
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "DEPRECATION" in str(w[0].message)
            assert "sync on_receive()" in str(w[0].message)

    @pytest.mark.anyio
    async def test_async_execute_works_without_warning(self):
        """AgentActor with async execute() works without any warnings."""

        class ModernAgent(AgentActor[str, str]):
            async def execute(self, input: str) -> str:
                return f"processed: {input}"

        # Spawning should not emit any warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            system = AgentSystem("test")
            ref = await system.spawn(ModernAgent, "modern")
            result = await system.ask(ref, Task(input="test"))
            ref.stop()
            await ref.join()

            # No warnings should be emitted
            assert len(w) == 0
            assert result.output == "processed: test"

    @pytest.mark.anyio
    async def test_sync_execute_still_raises_typeerror(self):
        """AgentActor with sync execute() still raises TypeError (hard error)."""

        class BrokenAgent(AgentActor[str, str]):
            def execute(self, input: str) -> str:  # Missing async
                return f"processed: {input}"

        # Spawning should raise TypeError, not just warn
        with pytest.raises(TypeError, match="sync execute"):
            system = ActorSystem("test")
            await system.spawn(BrokenAgent, "broken")

    @pytest.mark.anyio
    async def test_inherited_sync_receive_from_parent(self):
        """Sync receive() inherited from parent class should emit warning."""

        class BaseAgent(AgentActor[str, str]):
            def receive(self, msg: str) -> str:
                return f"base: {msg}"

        class DerivedAgent(BaseAgent):
            pass  # Inherits sync receive()

        # Spawning should emit warning for inherited sync receive()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            system = ActorSystem("test")
            ref = await system.spawn(DerivedAgent, "derived")
            ref.stop()
            await ref.join()

            # Should have exactly one DeprecationWarning
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "BaseAgent" in str(w[0].message)  # Should mention defining class


class TestThreadedMailboxShutdownRaces:
    """Tests for ThreadedMailbox shutdown race conditions and put-after-close behavior."""

    @pytest.mark.anyio
    async def test_put_after_close_raises_mailbox_closed(self):
        """put() raises MailboxClosed after close()."""
        mailbox = ThreadedMailbox(maxsize=10)

        # Close the mailbox
        await mailbox.close()

        # put() should raise MailboxClosed
        with pytest.raises(MailboxClosed, match="closed"):
            await mailbox.put("msg1")

    @pytest.mark.anyio
    async def test_put_nowait_after_close_raises_mailbox_closed(self):
        """put_nowait() raises MailboxClosed after close()."""
        mailbox = ThreadedMailbox(maxsize=10)

        # Close the mailbox
        await mailbox.close()

        # put_nowait() should raise MailboxClosed
        with pytest.raises(MailboxClosed, match="closed"):
            mailbox.put_nowait("msg1")

    @pytest.mark.anyio
    async def test_concurrent_producers_during_shutdown_race(self):
        """Multiple producers racing with close() should all get MailboxClosed."""
        mailbox = ThreadedMailbox(maxsize=10)
        errors = []
        success_count = [0]

        async def producer(producer_id):
            try:
                # Try to put messages with small delays
                for i in range(5):
                    await asyncio.sleep(0.001)
                    await mailbox.put(f"producer-{producer_id}-msg-{i}")
                    success_count[0] += 1
            except MailboxClosed as e:
                errors.append((producer_id, str(e)))

        # Start multiple producers
        producer_tasks = [asyncio.create_task(producer(i)) for i in range(10)]

        # Let producers run for a bit, then close
        await asyncio.sleep(0.01)
        await mailbox.close()

        # Wait for all producers to finish
        await asyncio.gather(*producer_tasks, return_exceptions=True)

        # At least some producers should have hit MailboxClosed
        assert len(errors) > 0, "Expected at least one producer to hit MailboxClosed"

        # All errors should be MailboxClosed
        for producer_id, err_msg in errors:
            assert "closed" in err_msg.lower()

    @pytest.mark.anyio
    async def test_put_before_close_succeeds_put_after_close_fails(self):
        """put() succeeds before close(), fails after close()."""
        mailbox = ThreadedMailbox(maxsize=10)

        # Should succeed before close
        assert await mailbox.put("msg1")
        assert await mailbox.put("msg2")

        # Close the mailbox
        await mailbox.close()

        # Should fail after close
        with pytest.raises(MailboxClosed):
            await mailbox.put("msg3")

        # But we should still be able to drain the messages we put before close
        msg1 = await mailbox.get()
        msg2 = await mailbox.get()
        assert msg1 == "msg1"
        assert msg2 == "msg2"

    @pytest.mark.anyio
    async def test_producer_consumer_race_during_shutdown(self):
        """Producer and consumer racing with close() - no silent message loss."""
        mailbox = ThreadedMailbox(maxsize=100)
        produced = []
        consumed = []
        producer_errors = []
        consumer_errors = []

        async def producer():
            try:
                for i in range(20):
                    await mailbox.put(f"msg{i}")
                    produced.append(i)
                    await asyncio.sleep(0.001)
            except MailboxClosed as e:
                producer_errors.append(str(e))

        async def consumer():
            try:
                for _ in range(30):  # Try to consume more than produced
                    msg = await mailbox.get()
                    consumed.append(msg)
                    await asyncio.sleep(0.001)
            except Exception as e:
                consumer_errors.append(str(e))

        # Start producer and consumer
        producer_task = asyncio.create_task(producer())
        consumer_task = asyncio.create_task(consumer())

        # Let them run, then close
        await asyncio.sleep(0.02)
        await mailbox.close()

        # Wait for both to finish
        await asyncio.gather(producer_task, consumer_task, return_exceptions=True)

        # Producer should have hit MailboxClosed
        assert len(producer_errors) > 0
        assert all("closed" in e.lower() for e in producer_errors)

        # All produced messages should have been consumed (no silent loss)
        # The consumer might get Empty after draining, but that's expected
        assert len(consumed) == len(produced)
        assert consumed == [f"msg{i}" for i in produced]

    @pytest.mark.anyio
    async def test_multiple_close_calls_are_idempotent(self):
        """Multiple close() calls are safe and idempotent."""
        mailbox = ThreadedMailbox(maxsize=10)

        # First close
        await mailbox.close()

        # Second close should not raise
        await mailbox.close()

        # Third close should also not raise
        await mailbox.close()

        # put() should still raise MailboxClosed
        with pytest.raises(MailboxClosed):
            await mailbox.put("msg1")

    @pytest.mark.anyio
    async def test_close_with_full_queue_and_concurrent_puts(self):
        """close() with full queue and concurrent puts - puts should fail explicitly."""
        mailbox = ThreadedMailbox(maxsize=5)
        errors = []

        # Fill the queue
        for i in range(5):
            await mailbox.put(f"fill-{i}")

        async def late_producer():
            try:
                await asyncio.sleep(0.01)
                # This should fail with MailboxClosed, not silently drop
                await mailbox.put("late-msg")
            except MailboxClosed as e:
                errors.append(str(e))

        # Start producer that will try to put after close
        producer_task = asyncio.create_task(late_producer())

        # Let producer start, then close
        await asyncio.sleep(0.005)
        await mailbox.close()

        # Wait for producer
        await producer_task

        # Producer should have hit MailboxClosed
        assert len(errors) == 1
        assert "closed" in errors[0].lower()

        # We should still be able to drain the 5 messages we put before close
        for i in range(5):
            msg = await mailbox.get()
            assert msg == f"fill-{i}"

    def test_threaded_put_nowait_after_close_raises(self):
        """put_nowait() from thread raises MailboxClosed after close()."""
        mailbox = ThreadedMailbox(maxsize=10)
        errors = []

        def worker():
            try:
                mailbox.put_nowait("thread-msg")
            except MailboxClosed as e:
                errors.append(str(e))

        # Start thread
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()

        # No errors yet (mailbox not closed)
        assert len(errors) == 0

        # Close the mailbox (this needs to be async, so we'll use asyncio.run)
        async def close_and_test():
            await mailbox.close()

            # Now try to put from thread
            errors.clear()
            thread2 = threading.Thread(target=worker)
            thread2.start()
            thread2.join()

            # Should have hit MailboxClosed
            assert len(errors) == 1
            assert "closed" in errors[0].lower()

        asyncio.run(close_and_test())


class TestThreadedSyncActorReplies:
    """Tests for threaded sync actor reply correctness (regression for silent None returns)."""

    @pytest.mark.anyio
    async def test_threaded_sync_actor_ask_returns_actual_value(self):
        """Threaded sync actor ask() should return the handler's actual value, not None."""

        class SyncActor(Actor[str, str]):
            def receive(self, msg: str) -> str:
                return f"processed: {msg}"

        system = ActorSystem("test", threaded=True, executor_workers=2)
        ref = await system.spawn(SyncActor, "sync-actor")

        # Multiple ask() calls should all return correct values
        result1 = await system.ask(ref, "hello")
        assert result1 == "processed: hello", f"Expected 'processed: hello', got {result1}"

        result2 = await system.ask(ref, "world")
        assert result2 == "processed: world", f"Expected 'processed: world', got {result2}"

        result3 = await system.ask(ref, "test123")
        assert result3 == "processed: test123", f"Expected 'processed: test123', got {result3}"

        await system.shutdown()

    @pytest.mark.anyio
    async def test_threaded_sync_actor_with_complex_return_types(self):
        """Threaded sync actor should correctly return complex types (dicts, lists, tuples)."""

        class ComplexReturnActor(Actor[str, dict]):
            def receive(self, msg: str) -> dict:
                return {"message": msg, "length": len(msg), "upper": msg.upper()}

        system = ActorSystem("test", threaded=True, executor_workers=2)
        ref = await system.spawn(ComplexReturnActor, "complex-actor")

        result = await system.ask(ref, "hello")
        assert result == {"message": "hello", "length": 5, "upper": "HELLO"}

        await system.shutdown()

    @pytest.mark.anyio
    async def test_threaded_sync_actor_with_none_return(self):
        """Threaded sync actor explicitly returning None should return None, not error."""

        class NoneReturnActor(Actor[str, None]):
            def receive(self, msg: str) -> None:
                # Explicitly return None
                return None

        system = ActorSystem("test", threaded=True, executor_workers=2)
        ref = await system.spawn(NoneReturnActor, "none-actor")

        result = await system.ask(ref, "test")
        assert result is None

        await system.shutdown()

    @pytest.mark.anyio
    async def test_threaded_sync_actor_concurrent_asks(self):
        """Concurrent ask() calls to threaded sync actor should all return correct values."""

        class CounterActor(Actor[int, int]):
            def __init__(self):
                super().__init__()
                self.count = 0

            def receive(self, msg: int) -> int:
                self.count += msg
                return self.count

        system = ActorSystem("test", threaded=True, executor_workers=4)
        ref = await system.spawn(CounterActor, "counter-actor")

        # Send concurrent messages
        tasks = [system.ask(ref, i) for i in range(10)]
        results = await asyncio.gather(*tasks)

        # All results should be non-None and increasing
        assert all(r is not None for r in results)
        assert results == sorted(results)  # Should be monotonically increasing

        await system.shutdown()


class TestThreadedBackgroundTaskPreservation:
    """Tests for background task preservation across sync messages (regression for task cancellation)."""

    @pytest.mark.anyio
    async def test_background_task_survives_across_sync_messages(self):
        """Background tasks created in sync actor should survive across multiple message boundaries."""

        class BackgroundTaskActor(Actor[str, int]):
            def __init__(self):
                super().__init__()
                self.background_task = None
                self.background_counter = 0

            def receive(self, msg: str) -> int:
                import asyncio

                loop = asyncio.get_event_loop()

                if msg == "start_background":
                    # Create a long-lived background task
                    async def background_work():
                        while True:
                            self.background_counter += 1
                            await asyncio.sleep(0.01)

                    self.background_task = loop.create_task(background_work())
                    return 0

                elif msg == "check_background":
                    # Check if background task is still running
                    if self.background_task and not self.background_task.done():
                        return self.background_counter
                    return -1

                elif msg == "stop_background":
                    if self.background_task:
                        self.background_task.cancel()
                    return self.background_counter

                return 0

        system = ActorSystem("test", threaded=True, executor_workers=2)
        ref = await system.spawn(BackgroundTaskActor, "bg-actor")

        # Start background task
        await system.ask(ref, "start_background")

        # Send multiple sync messages - background task should survive
        for _ in range(5):
            await asyncio.sleep(0.02)  # Let background task run
            counter = await system.ask(ref, "check_background")
            assert counter > 0, f"Background task should be running, got counter={counter}"

        # Stop background task
        final_counter = await system.ask(ref, "stop_background")
        assert final_counter > 0, "Background task should have incremented counter"

        await system.shutdown()

    @pytest.mark.anyio
    async def test_multiple_background_tasks_survive(self):
        """Multiple background tasks should all survive across message boundaries."""

        class MultiBgTaskActor(Actor[str, dict]):
            def __init__(self):
                super().__init__()
                self.tasks = []
                self.counters = [0, 0, 0]

            def receive(self, msg: str) -> dict:
                import asyncio

                loop = asyncio.get_event_loop()

                if msg == "start_all":
                    # Create multiple background tasks
                    for i in range(3):
                        async def work(idx=i):
                            while True:
                                self.counters[idx] += 1
                                await asyncio.sleep(0.01)

                        self.tasks.append(loop.create_task(work()))
                    return {"status": "started"}

                elif msg == "check_all":
                    # Check all tasks are still running
                    status = {
                        "task0_alive": not self.tasks[0].done() if len(self.tasks) > 0 else False,
                        "task1_alive": not self.tasks[1].done() if len(self.tasks) > 1 else False,
                        "task2_alive": not self.tasks[2].done() if len(self.tasks) > 2 else False,
                        "counters": self.counters.copy(),
                    }
                    return status

                elif msg == "stop_all":
                    for task in self.tasks:
                        task.cancel()
                    return {"counters": self.counters.copy()}

                return {}

        system = ActorSystem("test", threaded=True, executor_workers=2)
        ref = await system.spawn(MultiBgTaskActor, "multi-bg-actor")

        # Start all background tasks
        await system.ask(ref, "start_all")

        # Send multiple sync messages - all background tasks should survive
        for _ in range(5):
            await asyncio.sleep(0.02)
            status = await system.ask(ref, "check_all")

            # All tasks should be alive
            assert status["task0_alive"], "Task 0 should be alive"
            assert status["task1_alive"], "Task 1 should be alive"
            assert status["task2_alive"], "Task 2 should be alive"

            # All counters should be increasing
            assert all(c > 0 for c in status["counters"]), "All counters should be > 0"

        # Stop all tasks
        final_status = await system.ask(ref, "stop_all")
        assert all(c > 0 for c in final_status["counters"]), "Final counters should all be > 0"

        await system.shutdown()

    @pytest.mark.anyio
    async def test_invocation_tasks_persist_across_messages(self):
        """Tasks created during an invocation persist — they are not auto-cancelled."""

        class ScopedTaskActor(Actor[str, int]):
            def __init__(self):
                super().__init__()
                self.invocation_task = None

            def receive(self, msg: str) -> int:
                import asyncio

                loop = asyncio.get_event_loop()

                if msg == "create_task":
                    async def work():
                        for i in range(100):
                            await asyncio.sleep(0.01)
                        return 999

                    self.invocation_task = loop.create_task(work())
                    return 1

                elif msg == "check_task":
                    if self.invocation_task:
                        return 1 if self.invocation_task.done() else 0
                    return -1

                return 0

        system = ActorSystem("test", threaded=True, executor_workers=2)
        ref = await system.spawn(ScopedTaskActor, "scoped-actor")

        # Create a task
        await system.ask(ref, "create_task")

        # Task should still be running (not auto-cancelled)
        await asyncio.sleep(0.05)
        status = await system.ask(ref, "check_task")
        assert status == 0, "Task should still be running, not auto-cancelled"

        await system.shutdown()
