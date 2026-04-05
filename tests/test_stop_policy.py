"""Tests for stop_policy mechanism."""

import pytest

from everything_is_an_actor import Actor, ActorSystem, StopMode, AfterMessage, AfterIdle, StopPolicy


class OneTimeActor(Actor):
    """Actor that stops after processing one message."""

    def stop_policy(self) -> StopPolicy:
        return StopMode.ONE_TIME

    async def on_receive(self, message):
        return f"processed: {message}"


class AfterMessageActor(Actor):
    """Actor that stops after receiving 'stop' message."""

    def stop_policy(self) -> StopPolicy:
        return AfterMessage(message="stop")

    async def on_receive(self, message):
        return f"processed: {message}"


class AfterIdleActor(Actor):
    """Actor that stops after 0.5 seconds of idle time."""

    def stop_policy(self) -> StopPolicy:
        return AfterIdle(seconds=0.5)

    async def on_receive(self, message):
        return f"processed: {message}"


class NeverStopActor(Actor):
    """Actor with default NEVER policy."""

    async def on_receive(self, message):
        return f"processed: {message}"


@pytest.mark.anyio
async def test_one_time_actor_stops_after_one_message():
    """ONE_TIME actor stops after processing one message."""
    system = ActorSystem()
    try:
        ref = await system.spawn(OneTimeActor, "one-time")
        assert ref.is_alive

        # Send first message
        result = await system.ask(ref, "first")
        assert result == "processed: first"

        # Actor should stop after processing one message
        import asyncio
        await asyncio.sleep(0.1)
        assert not ref.is_alive
    finally:
        await system.shutdown()


@pytest.mark.anyio
async def test_after_message_actor_stops_on_specific_message():
    """AfterMessage actor stops when it receives the specific message."""
    system = ActorSystem()
    try:
        ref = await system.spawn(AfterMessageActor, "after-msg")
        assert ref.is_alive

        # Send some messages
        result1 = await system.ask(ref, "msg1")
        assert result1 == "processed: msg1"
        assert ref.is_alive

        result2 = await system.ask(ref, "msg2")
        assert result2 == "processed: msg2"
        assert ref.is_alive

        # Send the stop message
        result3 = await system.ask(ref, "stop")
        assert result3 == "processed: stop"

        # Actor should stop
        import asyncio
        await asyncio.sleep(0.1)
        assert not ref.is_alive
    finally:
        await system.shutdown()


@pytest.mark.anyio
async def test_after_idle_actor_stops_after_timeout():
    """AfterIdle actor stops after being idle for N seconds."""
    system = ActorSystem()
    try:
        ref = await system.spawn(AfterIdleActor, "after-idle")
        assert ref.is_alive

        # Send first message
        result = await system.ask(ref, "first")
        assert result == "processed: first"
        assert ref.is_alive

        # Wait for idle timeout (0.5 seconds + buffer)
        import asyncio
        await asyncio.sleep(0.7)
        assert not ref.is_alive
    finally:
        await system.shutdown()


@pytest.mark.anyio
async def test_tell_type_error_on_never_policy():
    """tell() raises TypeError when target actor has NEVER stop_policy."""
    from everything_is_an_actor import Actor, StopMode, StopPolicy

    class CallerActor(Actor):
        async def on_receive(self, message):
            if message == "test":
                try:
                    await self.tell(NeverStopActor, "hello")
                    return "no_error"
                except TypeError as e:
                    return str(e)
            return "done"

    system = ActorSystem()
    try:
        caller = await system.spawn(CallerActor, "caller")
        result = await system.ask(caller, "test")
        assert "non-NEVER stop_policy" in result
    finally:
        await system.shutdown()


@pytest.mark.anyio
async def test_tell_succeeds_on_one_time_actor():
    """tell() succeeds when target actor has ONE_TIME policy."""
    from everything_is_an_actor import Actor, StopMode, StopPolicy

    class CallerActor(Actor):
        async def on_receive(self, message):
            if message == "test":
                await self.tell(OneTimeActor, "hello")
                return "ok"
            return "done"

    system = ActorSystem()
    try:
        caller = await system.spawn(CallerActor, "caller")
        result = await system.ask(caller, "test")
        assert result == "ok"
    finally:
        await system.shutdown()


@pytest.mark.anyio
async def test_never_stop_actor_runs_forever_until_stopped():
    """NEVER actor doesn't auto-stop, must be manually stopped."""
    system = ActorSystem()
    try:
        ref = await system.spawn(NeverStopActor, "never-stop")
        assert ref.is_alive

        # Send multiple messages
        for i in range(5):
            result = await system.ask(ref, f"msg{i}")
            assert result == f"processed: msg{i}"
            assert ref.is_alive

        # Manually stop
        ref.stop()
        import asyncio
        await asyncio.sleep(0.1)
        assert not ref.is_alive
    finally:
        await system.shutdown()


@pytest.mark.anyio
async def test_ask_by_path_not_found():
    """ask() to non-existent path raises error."""
    system = ActorSystem()
    try:
        with pytest.raises(ValueError, match="not found"):
            await system.ask("non-existent-path", "hello")
    finally:
        await system.shutdown()


@pytest.mark.anyio
async def test_get_actor_not_found():
    """get_actor() with non-existent path returns None."""
    system = ActorSystem()
    try:
        result = await system.get_actor("non-existent-path")
        assert result is None
    finally:
        await system.shutdown()


@pytest.mark.anyio
async def test_after_message_only_stops_on_target_message():
    """AfterMessage actor only stops when receiving the exact target message."""
    system = ActorSystem()
    try:
        ref = await system.spawn(AfterMessageActor, "after-msg")
        assert ref.is_alive

        # Send many non-target messages
        for i in range(10):
            result = await system.ask(ref, f"msg{i}")
            assert result == f"processed: msg{i}"
            assert ref.is_alive

        # Send a similar but different message - should NOT stop
        result = await system.ask(ref, "stoppp")
        assert result == "processed: stoppp"
        assert ref.is_alive

        # Only exact "stop" message stops
        await system.ask(ref, "stop")
        import asyncio
        await asyncio.sleep(0.1)
        assert not ref.is_alive
    finally:
        await system.shutdown()


@pytest.mark.anyio
async def test_after_idle_resets_timer_on_message():
    """AfterIdle actor resets timer when it receives a message."""
    system = ActorSystem()
    try:
        ref = await system.spawn(AfterIdleActor, "after-idle")
        assert ref.is_alive

        # Send first message
        result = await system.ask(ref, "first")
        assert result == "processed: first"

        # Wait less than idle timeout
        import asyncio
        await asyncio.sleep(0.3)  # less than 0.5s timeout

        # Send another message - should reset timer
        result = await system.ask(ref, "second")
        assert result == "processed: second"
        assert ref.is_alive

        # Wait again but still less than total time from start
        await asyncio.sleep(0.3)
        assert ref.is_alive  # timer was reset

        # Now wait for full timeout
        await asyncio.sleep(0.3)  # total > 0.5s since last message
        assert not ref.is_alive
    finally:
        await system.shutdown()


@pytest.mark.anyio
async def test_stop_already_dead_actor():
    """Stopping an already dead actor is a no-op."""
    system = ActorSystem()
    try:
        ref = await system.spawn(OneTimeActor, "one-time")
        assert ref.is_alive

        # Send message to trigger stop
        await system.ask(ref, "first")
        import asyncio
        await asyncio.sleep(0.1)
        assert not ref.is_alive

        # Stop again should be a no-op (no exception)
        ref.stop()
        await asyncio.sleep(0.05)
        assert not ref.is_alive

        # Ask dead actor should raise error
        from everything_is_an_actor.core.ref import ActorStoppedError
        with pytest.raises(ActorStoppedError, match="stopped"):
            await system.ask(ref, "test")
    finally:
        await system.shutdown()


@pytest.mark.anyio
async def test_actor_exception_during_message_handling():
    """Actor exception during message handling is logged but actor survives."""

    class CrashActor(Actor):
        def stop_policy(self) -> StopPolicy:
            return StopMode.NEVER  # Use NEVER so actor doesn't auto-stop

        async def on_receive(self, message):
            if message == "crash":
                raise ValueError("intentional crash")
            return f"processed: {message}"

    system = ActorSystem()
    try:
        ref = await system.spawn(CrashActor, "crash-actor")
        assert ref.is_alive

        # Normal message works
        result = await system.ask(ref, "hello")
        assert result == "processed: hello"
        assert ref.is_alive

        # Crash message raises ValueError
        with pytest.raises(ValueError, match="intentional crash"):
            await system.ask(ref, "crash")

        # Actor is still alive after exception (error is logged, actor survives)
        assert ref.is_alive

        # Can still process messages after crash
        result = await system.ask(ref, "after-crash")
        assert result == "processed: after-crash"
        assert ref.is_alive
    finally:
        await system.shutdown()


@pytest.mark.anyio
async def test_tell_to_dead_actor_goes_to_dead_letter():
    """tell() to a dead actor is sent to dead letter handler (no exception)."""
    import asyncio

    class TargetActor(Actor):
        def stop_policy(self) -> StopPolicy:
            return StopMode.ONE_TIME

        async def on_receive(self, message):
            return f"done: {message}"

    dead_letters = []
    system = ActorSystem()
    system.on_dead_letter(lambda letter: dead_letters.append(letter))
    try:
        ref = await system.spawn(TargetActor, "target")
        await system.ask(ref, "first")  # triggers stop
        await asyncio.sleep(0.1)

        # Tell to dead actor should go to dead letter handler, not raise
        await system.tell(ref, "hello")
        await asyncio.sleep(0.1)

        # Verify message went to dead letter
        assert len(dead_letters) == 1
        assert dead_letters[0].message == "hello"
    finally:
        await system.shutdown()


@pytest.mark.anyio
async def test_multiple_actors_idle_timeout():
    """Multiple actors with AfterIdle all stop correctly."""
    import asyncio

    actors = []
    system = ActorSystem()
    try:
        # Spawn multiple AfterIdle actors
        for i in range(3):
            ref = await system.spawn(AfterIdleActor, f"idle-{i}")
            actors.append(ref)
            await system.ask(ref, f"init-{i}")

        # All should be alive
        for ref in actors:
            assert ref.is_alive

        # Wait for timeout
        await asyncio.sleep(0.7)

        # All should be dead
        for ref in actors:
            assert not ref.is_alive
    finally:
        await system.shutdown()


@pytest.mark.anyio
async def test_spawn_after_system_shutdown():
    """Actors can still be spawned and work after system shutdown."""
    import asyncio
    system = ActorSystem()
    await system.shutdown()

    # After shutdown, spawn still works and actor can process messages
    ref = await system.spawn(NeverStopActor, "late-spawn")
    result = await system.ask(ref, "test")
    assert result == "processed: test"


@pytest.mark.anyio
async def test_ask_after_system_shutdown():
    """Asking after system shutdown raises ActorStoppedError."""
    system = ActorSystem()
    ref = await system.spawn(NeverStopActor, "test-actor")
    await system.shutdown()

    # Actor should be stopped after system shutdown
    from everything_is_an_actor.core.ref import ActorStoppedError
    with pytest.raises(ActorStoppedError, match="stopped"):
        await system.ask(ref, "late-ask")
